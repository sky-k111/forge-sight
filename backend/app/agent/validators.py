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
    """把语义维度（产线/工序…）解析成事实表里的物理列名。找不到返回 None。"""
    if not semantic:
        return None
    lowered = semantic.lower()
    for table in _fact_tables(semantic_context):
        for f in table.get("fields") or []:
            name = str(f.get("name") or "")
            sem = str(f.get("semanticName") or "")
            if name and (lowered in name.lower() or (sem and (lowered in sem.lower() or sem in lowered)) or name.lower() in lowered):
                return name
    return None


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
    q = (question or "").lower()
    # 优先：semantic 命中指标名/定义/别名
    for m in cands:
        hay = " ".join(str(m.get(k) or "") for k in ("name", "definition", "semanticFormula", "aliases"))
        if metric_semantic and metric_semantic.lower() in hay.lower():
            return m
    # 退而求其次：关键词打分
    best, best_score = None, 0
    for m in cands:
        text = " ".join(str(m.get(k) or "") for k in ("name", "definition", "semanticFormula"))
        score = sum(1 for w in str(metric_semantic).split() if w and w.lower() in text.lower())
        if q:
            keys = m.get("matchedTerms") or (m.get("name") or "").split()
            score += sum(1 for t in keys if t and str(t).lower() in q)
        if score > best_score:
            best, best_score = m, score
    return best if best_score > 0 else None


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
        resolved = resolve_metric(met.semantic, ctx, question)
        if resolved is None:
            metric_clarify.append(f"指标「{met.semantic}」")
        metrics.append(met.model_copy(update={"id": str(resolved.get("id") or "")}) if resolved else met)

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
