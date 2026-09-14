"""确定性校验层：只保证，不猜语义。

职责：
1. 维度/指标/时间字段必须在当前数据源的元数据中解析得到，否则给出澄清（而不是猜）。
2. 图类型/粒度等枚举已由 Pydantic 约束；这里补充「字段存在性」与「回填物理列/指标 id」。
3. 不在这里判断「用户想要什么」——语义由 AgentIntent 决定。

安全红线（只读/白名单/预算/权限）仍由 capability_executor / budget / 受治理子图保证。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.agent.intent_contract import AgentIntent, Dimension, Metric


@dataclass
class ValidationResult:
    valid: bool
    reason_code: str = "OK"
    message: str = ""
    ask_clarify: bool = False
    resolved: dict[str, Any] = field(default_factory=dict)


def _fact_tables(semantic_context: dict[str, Any] | None) -> list[dict[str, Any]]:
    ctx = semantic_context or {}
    tables = ctx.get("tables") or []
    facts = []
    for t in tables:
        obj = str(t.get("objectType") or "").lower()
        if "fact" in obj or obj in {"", "table"}:
            facts.append(t)
    return facts or list(tables)


def resolve_dimension_field(
    semantic: str,
    semantic_context: dict[str, Any] | None,
) -> str | None:
    """把语义维度解析成唯一物理列名；跨表重名或找不到均返回 None。"""
    if not semantic:
        return None
    lowered = semantic.strip().lower()
    ranked: list[tuple[int, str]] = []
    # Dimension labels often resolve to a display field ("工序" -> "工序名称")
    # while some sources only expose an id/code. Rank those generic metadata
    # roles instead of maintaining a product-specific keyword table.
    for table in (semantic_context or {}).get("tables") or []:
        for f in table.get("fields") or []:
            name = str(f.get("name") or "")
            sem = str(f.get("semanticName") or f.get("cnName") or "").strip()
            name_lower = name.lower()
            sem_lower = sem.lower()
            if not name or not sem or lowered not in sem_lower and lowered != name_lower:
                continue
            exact = lowered in {name_lower, sem_lower}
            score = 1000 if exact else 700 - abs(len(sem_lower) - len(lowered))
            display_tokens = ("名称", "name", "label", "描述", "description")
            identity_tokens = (" id", "_id", "编码", "code", "序号", "sequence", " seq")
            if not exact and any(token in sem_lower or token in name_lower for token in display_tokens):
                score += 120
            if not exact and any(token in sem_lower or token in name_lower for token in identity_tokens):
                score -= 40
            ranked.append((score, name))
    if not ranked:
        return None
    best_score = max(item[0] for item in ranked)
    best_names = list(dict.fromkeys(name for score, name in ranked if score == best_score))
    return best_names[0] if len(best_names) == 1 else None


def resolve_metric(
    metric_semantic: str,
    semantic_context: dict[str, Any] | None,
    question: str = "",
) -> dict[str, Any] | None:
    """把语义指标（产量/良率…）解析成已登记的指标；找不到返回 None。"""
    ctx = semantic_context or {}
    metrics = ctx.get("metrics") or []
    cands = [m for m in metrics if m.get("physicalFormula")]
    if not cands:
        return None
    needle = str(metric_semantic or "").strip().lower()
    if not needle:
        return None
    question_text = str(question or "").strip().lower()
    # The accepted user text outranks a broader model paraphrase.  If the user
    # literally named one published metric (or alias), choose the longest unique
    # mention before fuzzy-resolving the model token.  This lets a governed rule
    # such as "低于安全库存" win over an LLM's generic "库存量".
    mentioned: list[tuple[int, dict[str, Any]]] = []
    for metric in cands:
        aliases = metric.get("aliases") or metric.get("synonyms") or []
        if isinstance(aliases, str):
            aliases = [part.strip() for part in aliases.split(",")]
        terms = [str(metric.get("name") or "").strip().lower(),
                 *(str(alias).strip().lower() for alias in aliases)]
        hits = [term for term in terms if term and term in question_text]
        if hits:
            mentioned.append((max(len(term) for term in hits), metric))
    if mentioned:
        longest = max(score for score, _ in mentioned)
        winners = [metric for score, metric in mentioned if score == longest]
        if len(winners) == 1:
            return winners[0]
    ranked: list[tuple[int, dict[str, Any]]] = []
    for metric in cands:
        name = str(metric.get("name") or "").strip().lower()
        aliases = metric.get("aliases") or metric.get("synonyms") or []
        if isinstance(aliases, str):
            aliases = [part.strip() for part in aliases.split(",")]
        terms = [name, *(str(alias).strip().lower() for alias in aliases if str(alias).strip())]
        score = 0
        if needle == name:
            score = 1000
        elif needle in terms[1:]:
            score = 900
        else:
            containers = [term for term in terms if needle in term]
            if containers:
                score = 700 - min(len(term) - len(needle) for term in containers)
        if not score:
            continue
        if question_text and name and name in question_text:
            score += 100
        ranked.append((score, metric))
    if not ranked:
        return None
    ranked.sort(key=lambda item: item[0], reverse=True)
    return ranked[0][1] if len(ranked) == 1 or ranked[0][0] > ranked[1][0] else None


def validate_intent(
    intent: AgentIntent,
    semantic_context: dict[str, Any] | None = None,
    *,
    question: str = "",
) -> ValidationResult:
    """校验 + 回填。语义空缺 → ask_clarify=True；非法 → valid=False。"""
    ctx = semantic_context or {}
    has_fact = bool(_fact_tables(ctx))
    dimensions: list[Dimension] = []
    clarify_fields: list[str] = []
    for dim in intent.dimensions:
        if dim.semantic == "时间":
            # 时间维度：能消费的前提是目标指标有登记时间字段；这里不强制（由 query_compiler 决定）。
            dimensions.append(dim)
            continue
        if not dim.semantic:
            continue
        if not has_fact:
            clarify_fields.append(f"维度「{dim.semantic}」");
            dimensions.append(dim)
            continue
        code = resolve_dimension_field(dim.semantic, ctx)
        if code is None:
            clarify_fields.append(f"维度「{dim.semantic}」")
        dimensions.append(dim.model_copy(update={"code": code}) if code else dim)

    metrics: list[Metric] = []
    metric_clarify: list[str] = []
    for met in intent.metrics:
        if not met.semantic:
            continue
        resolved = next((item for item in ctx.get("metrics") or []
                         if met.id and str(item.get("id")) == str(met.id)), None)
        resolved = resolved or resolve_metric(met.semantic, ctx, question)
        if resolved is None:
            metric_clarify.append(f"指标「{met.semantic}」")
        metrics.append(met.model_copy(update={
            "id": str(resolved.get("id") or ""),
            "semantic": str(resolved.get("name") or met.semantic),
        }) if resolved else met)

    time_clarify = ""
    if intent.time is not None and intent.time.mode.value in {"absolute", "comparison", "recent"}:
        # 需要指标有物理时间字段才能做时间过滤/分组；没有则必须澄清。
        has_time_field = any(m.get("physicalTimeField") for m in (ctx.get("metrics") or []))
        if not has_time_field and any(d.semantic == "时间" for d in intent.dimensions):
            time_clarify = "时间维度需要在指标上登记物理时间字段"

    message_bits = []
    if clarify_fields:
        message_bits.append("缺少可解析维度：" + "、".join(dict.fromkeys(clarify_fields)))
    if metric_clarify:
        message_bits.append("缺少可解析指标：" + "、".join(dict.fromkeys(metric_clarify)))
    if time_clarify:
        message_bits.append(time_clarify)
    ask = bool(message_bits)
    return ValidationResult(
        valid=not ask,
        reason_code="INTENT_CLARIFY" if ask else "OK",
        message="；".join(message_bits),
        ask_clarify=ask,
        resolved={
            "dimensions": [d.model_dump(mode="json", by_alias=True) for d in dimensions],
            "metrics": [m.model_dump(mode="json", by_alias=True) for m in metrics],
        },
    )
