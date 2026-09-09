"""Plan A: deterministic, dialect-aware SQL compiler for metric-resolvable questions.

For questions that map to a published, source-bound metric, we compose the SQL
from the metric physical formula + resolved absolute time periods + group
dimensions. The LLM is NOT asked to write time filters or grouping — they are
rendered by code, which removes the recurring "month not converted" and
only_full_group_by failures.

Anything the compiler cannot resolve deterministically returns None so the
caller keeps the existing LLM generator (no regression for the long tail).
"""

from __future__ import annotations

import re
from typing import Any

from app.gateway.connectors import get_adapter


_DIM_KEYWORDS: dict[str, tuple[str, ...]] = {
    "产品": ("product_id", "product_code", "product_name", "sku"),
    "产线": ("line_id", "line_code", "line_name", "production_line", "line"),
    "车间": ("workshop", "workshop_id", "workshop_name"),
    "工序": ("process_id", "process_code", "process_name", "operation"),
    "班次": ("shift", "shift_id", "shift_name"),
    "缺陷类型": ("defect_type", "defect_type_id", "defect_type_name"),
    "类型": ("defect_type", "defect_type_id", "defect_type_name"),
    "状态": ("status", "equipment_status", "line_status"),
}


_METRIC_CORES = ("停机时长", "不良率", "返工率", "良率", "合格数量", "不良数量", "数量", "产量", "库存", "时长", "占比", "合格")


def _split_metric_name(name: str) -> tuple[str, str]:
    """把指标名拆成 (核心词, 修饰词)。核心词是结尾最长的指标计量词，其前为修饰词。
    例：工序不良率 -> (不良率, 工序)；设备停机时长 -> (停机时长, 设备)。"""
    n = str(name or "").strip()
    for core in _METRIC_CORES:
        if n.endswith(core):
            modifier = n[: len(n) - len(core)].strip("的")
            return core, modifier
    return "", n


def _pick_metric(metrics: list[dict[str, Any]], q: str = "", hint: str | None = None) -> dict[str, Any] | None:
    cands = [m for m in (metrics or []) if m.get("physicalFormula")]
    if not cands:
        return None
    if not q:
        return cands[0]

    ql = q.lower()
    hint_l = (hint or "").lower()

    def score(m) -> tuple[int, int, str]:
        core, modifier = _split_metric_name(m.get("name"))
        if not core:
            return (-10, 0, str(m.get("name") or ""))
        in_q = lambda w: bool(w) and w.lower() in ql
        core_hit = 1 if in_q(core) else 0
        # 泛化惩罚：若问句里出现了“更长、且嵌入当前核心词”的核心词，说明当前核心词过泛（如问“不良率”时，
        # “良率”是“不良率”的子串，不应抢走“工序不良率”）。
        generic_pen = 0
        if core_hit:
            for c2 in _METRIC_CORES:
                if c2 != core and c2.lower().endswith(core.lower()) and in_q(c2):
                    generic_pen = 1
                    break
        mod_hit = 1 if (modifier and in_q(modifier)) else 0
        mod_pen = 1 if (modifier and not in_q(modifier)) else 0
        # 指标提示命中（多指标拆分后每个任务只带自己的指标词）权重最高，避免“良率和不良率”
        # 都落在同一指标上；核心词命中权重次之。
        # 提示命中按「指标核心词」整词匹配，避免 hint=良率 误命中「不良率」这类含子串的指标。
        hint_hit = 1 if (hint_l and (core == hint_l or str(m.get("name") or "").lower() == hint_l)) else 0
        # 核心词命中权重高；问句出现/未出现的修饰词分别加分/扣分，避免“工序不良率”被“抽检不良率”抢走。
        return (core_hit * 4 + mod_hit - mod_pen - generic_pen + hint_hit * 5, core_hit, str(m.get("name") or ""))

    scored = sorted(((score(m), m) for m in cands), key=lambda x: x[0], reverse=True)
    top, _ = scored[0]
    if top[0] <= 0:
        return None
    return scored[0][1]

def _match_column(fact: dict[str, Any], keywords: tuple[str, ...]) -> str | None:
    for field in fact.get("fields") or []:
        name = str(field.get("name") or "").lower()
        sem = str(field.get("semanticName") or "").lower()
        for kw in keywords:
            if kw.lower() in name or kw.lower() in sem:
                return field["name"]
    return None


def _resolve_dimension(dim: str, fact: dict[str, Any], dialect: str) -> str | None:
    d = dim.lower()
    for sem, kws in _DIM_KEYWORDS.items():
        # 必须整词命中：产线/产品 都以 产 开头，不能用单个字符前缀匹配，否则 产线 会被误判成产品。
        if dim and (sem in dim or dim in sem):
            col = _match_column(fact, kws)
            if col:
                return col
    for field in fact.get("fields") or []:
        if dim in str(field.get("semanticName") or "") or dim in str(field.get("name") or ""):
            return field["name"]
    return None


def _period_filter(metric: dict[str, Any], periods: list[dict[str, Any]], dialect: str) -> str | None:
    tfield = metric.get("physicalTimeField")
    if not tfield or not periods:
        return None
    adapter = get_adapter(dialect)
    quoted = adapter.quote_identifier(tfield)
    clauses: list[str] = []
    for p in periods:
        s = p.get("startInclusive")
        e = p.get("endExclusive")
        if s:
            clauses.append(f"f.{quoted} >= '{s}'")
        if e:
            clauses.append(f"f.{quoted} < '{e}'")
    return " AND ".join(clauses) if clauses else None


def _time_group(dialect: str, metric: dict[str, Any], grain: str) -> str | None:
    tfield = metric.get("physicalTimeField")
    if not tfield:
        return None
    adapter = get_adapter(dialect)
    quoted = adapter.quote_identifier(tfield)
    if dialect == "mysql":
        if grain in ("year", "年"):
            return f"DATE_FORMAT(f.{quoted}, '%Y')"
        if grain in ("month", "月"):
            return f"DATE_FORMAT(f.{quoted}, '%Y-%m')"
        if grain in ("quarter", "季度"):
            return f"CONCAT(DATE_FORMAT(f.{quoted}, '%Y'), '-Q', QUARTER(f.{quoted}))"
        if grain in ("week", "周"):
            return f"YEARWEEK(f.{quoted})"
        return f"DATE(f.{quoted})"
    return f"DATE(f.{quoted})"


def _is_time_dim(dim: str) -> bool:
    return any(t in dim for t in ("日期", "时间", "天", "日", "星期", "date", "time", "day", "weekday", "月份", "月", "周"))




def _dim_time_grain(dim: str) -> str | None:
    """从时间拆分维度名推断合适的粒度（周/月/季度/日/年）。
    “周”维度应按 week 聚合，而非盲目引用 timeConstraint.timeGrain（常为 day）
    不然会把“按周拆分”误判成按天，导致 x 轴对不上。"""
    d = str(dim or "").lower()
    if any(t in d for t in ("年", "year")):
        return "year"
    if any(t in d for t in ("季", "quarter")):
        return "quarter"
    if any(t in d for t in ("月", "month")):
        return "month"
    if any(t in d for t in ("周", "星期", "week")):
        return "week"
    if any(t in d for t in ("日", "天", "day")):
        return "day"
    return None


def compile_query(
    semantic_context: dict[str, Any],
    task: dict[str, Any],
    dialect: str | None = None,
) -> tuple[str, dict[str, Any]] | None:
    metrics = (semantic_context or {}).get("metrics") or []
    qtext = str((task or {}).get("normalizedQuestion") or (task or {}).get("originalQuestion") or "")
    metric = _pick_metric(metrics, qtext, (task or {}).get("metricHint") or None)
    if not metric:
        return None
    dialect = (dialect or (semantic_context or {}).get("source", {}).get("type") or "mysql").lower()
    adapter = get_adapter(dialect)
    source = (semantic_context or {}).get("source") or {}

    tables = (semantic_context or {}).get("tables") or []
    fact = None
    for t in tables or []:
        if "fact" in str(t.get("objectType", "")).lower():
            fact = t
            break
    if not fact:
        src = metric.get("sourceTables") or []
        fact = next((t for t in tables if t.get("name") == (src[0] if src else None)), None)
    if not fact:
        return None

    fq = fact.get("qualifiedName") or fact.get("name")
    metric_expr = str(metric.get("physicalFormula") or "")
    if not metric_expr:
        return None

    time_constraint = (task or {}).get("timeConstraint") or {}
    periods = time_constraint.get("periods") or []
    where_parts: list[str] = []
    tf = _period_filter(metric, periods, dialect)
    if tf:
        where_parts.append(tf)
    filt = metric.get("filterExpression")
    if filt:
        where_parts.append(f"({filt})")

    group_by = (task or {}).get("groupBy") or []
    dim_cols: list[str] = []
    dim_raw: list[str] = []
    for dim in group_by:
        if _is_time_dim(dim):
            col = _time_group(dialect, metric, (_dim_time_grain(dim) or str(time_constraint.get("timeGrain") or "day")))
            if not col:
                # 用户明确要求“按月份/按周/按天/逐月”拆分，但当前指标没有登记时间字段，
                # 不能静默退化为整段聚合（会把“各月份”变成“全年求和”）。回退给 LLM 生成。
                return None
            dim_cols.append(col)
            dim_raw.append(dim)
            continue
        col = _resolve_dimension(dim, fact, dialect)
        if not col:
            return None
        dim_cols.append(f"f.{adapter.quote_identifier(col)}")
        dim_raw.append(dim)

    atype = (task or {}).get("analysisType") or ""
    q = str((task or {}).get("normalizedQuestion") or (task or {}).get("originalQuestion") or "")
    limit: int | None = None
    order: str | None = None
    out_hint = "chart"
    if "rank" in atype or "top" in atype or "排行" in atype or "前" in q:
        order = "DESC"
        m = re.search(r"前\s*(\d+)", q)
        if m:
            limit = int(m.group(1))
        if not dim_raw:
            # ranking implies a dimension: pick the most salient dim in the question
            fallback = next((d for d in ("产品", "产线", "工序", "车间", "缺陷类型") if d in q), None)
            if fallback:
                col = _resolve_dimension(fallback, fact, dialect)
                if col:
                    dim_cols = [f"f.{adapter.quote_identifier(col)}"]
                    dim_raw = [fallback]
    elif "trend" in atype or "趋势" in atype:
        order = "ASC"
        if not dim_raw:
            col = _time_group(dialect, metric, (_dim_time_grain(dim) or str(time_constraint.get("timeGrain") or "day")))
            if col:
                dim_cols = [col]
                dim_raw = ["时间"]
    elif "占比" in q or "share" in atype or "比例" in q:
        order = "DESC"
    elif "compare" in atype or "对比" in atype or "相比" in q:
        order = "DESC"

    select_parts: list[str] = []
    for raw, col in zip(dim_raw, dim_cols):
        select_parts.append(f"{col} AS {adapter.quote_identifier(raw)}")
    select_parts.append(f"{metric_expr} AS {adapter.quote_identifier(metric.get('name') or 'metric')}")
    sql = f"SELECT {', '.join(select_parts)} FROM {fq} AS f"
    if where_parts:
        sql += " WHERE " + " AND ".join(where_parts)
    if dim_cols:
        sql += " GROUP BY " + ", ".join(dim_cols)
    if order and dim_cols:
        if atype in ("trend",) or "趋势" in atype:
            order_col = dim_cols[0]
        else:
            order_col = metric_expr
        sql += " ORDER BY " + order_col + f" {order}"
    if limit:
        sql += f" LIMIT {int(limit)}"
    first_dim = dim_raw[0] if dim_raw else None
    axis = None
    if first_dim:
        if _is_time_dim(first_dim):
            axis = {"key": first_dim, "kind": "time", "grain": _dim_time_grain(first_dim) or str(time_constraint.get("timeGrain") or "day")}
        else:
            axis = {"key": first_dim, "kind": "category", "grain": None}

    return sql, {
        "compiled": True,
        "metricId": metric.get("id"),
        "metric": metric.get("name"),
        "expression": metric_expr,
        "timeField": metric.get("physicalTimeField"),
        "periods": periods,
        "groupBy": dim_raw,
        "analysisType": atype,
        "outputHint": out_hint,
        "sourceType": source.get("type"),
        "factTable": fq,
        "axis": axis,
    }


def compile_intent(
    intent: Any,
    semantic_context: dict[str, Any],
    dialect: str | None = None,
) -> tuple[str, dict[str, Any]] | None:
    """基于 AgentIntent 渲染 SQL。

    语义（维度/时间/图/分析类型）已由意图解析确定；这里只做「意图 -> 受治理子图可消费的
    task」这一适配，然后复用 compile_query 的 SQL 渲染，避免在编译层再次从自由文本猜测轴/粒度。
    """
    from app.agent.intent_adapter import intent_to_tasks
    from app.agent.intent_contract import AgentIntent

    model = intent if isinstance(intent, AgentIntent) else AgentIntent.model_validate(intent)
    tasks = intent_to_tasks(model)
    if not tasks:
        return None
    return compile_query(semantic_context, tasks[0], dialect=dialect)
