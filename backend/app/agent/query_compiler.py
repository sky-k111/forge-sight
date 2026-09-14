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
from app.gateway.connectors.base import PhysicalTableRef


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
        bounds = []
        s = p.get("startInclusive")
        e = p.get("endExclusive")
        if s:
            bounds.append(f"f.{quoted} >= '{str(s).replace(chr(39), chr(39) * 2)}'")
        if e:
            bounds.append(f"f.{quoted} < '{str(e).replace(chr(39), chr(39) * 2)}'")
        if bounds:
            clauses.append("(" + " AND ".join(bounds) + ")")
    return "(" + " OR ".join(clauses) + ")" if clauses else None


def _period_bucket(metric: dict[str, Any], periods: list[dict[str, Any]], dialect: str) -> str | None:
    """Render accepted comparison members as one stable categorical dimension."""
    tfield = metric.get("physicalTimeField")
    if not tfield or len(periods) < 2:
        return None
    adapter = get_adapter(dialect)
    quoted = adapter.quote_identifier(tfield)
    cases: list[str] = []
    for index, period in enumerate(periods, 1):
        start = period.get("startInclusive")
        end = period.get("endExclusive")
        if not start or not end:
            return None
        label = str(period.get("label") or f"比较周期{index}").replace("'", "''")
        start_sql = str(start).replace("'", "''")
        end_sql = str(end).replace("'", "''")
        cases.append(
            f"WHEN f.{quoted} >= '{start_sql}' AND f.{quoted} < '{end_sql}' THEN '{label}'"
        )
    return "CASE " + " ".join(cases) + " END"


def _sql_literal(value: Any) -> str:
    """Render values, never identifiers, for the governed filter subset."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def _render_bound_filter(binding: dict[str, Any], adapter) -> str | None:
    if binding.get("status") != "bound" or not binding.get("field") or not binding.get("alias"):
        return None
    column = f"{binding['alias']}.{adapter.quote_identifier(str(binding['field']))}"
    operator = str(binding.get("operator") or "eq").strip().lower()
    operator = {
        "=": "eq", "==": "eq", "!=": "ne", "<>": "ne",
        ">": "gt", ">=": "gte", "<": "lt", "<=": "lte",
    }.get(operator, operator)
    value = binding.get("value")
    if operator in {"eq", "ne"} and value is None:
        return f"{column} IS {'NOT ' if operator == 'ne' else ''}NULL"
    binary = {"eq": "=", "ne": "<>", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}
    if operator in binary:
        return f"{column} {binary[operator]} {_sql_literal(value)}"
    if operator in {"in", "not_in"} and isinstance(value, list) and value:
        values = ", ".join(_sql_literal(item) for item in value)
        return f"{column} {'NOT ' if operator == 'not_in' else ''}IN ({values})"
    if operator == "between" and isinstance(value, (list, tuple)) and len(value) == 2:
        return f"{column} BETWEEN {_sql_literal(value[0])} AND {_sql_literal(value[1])}"
    return None


def _time_group(dialect: str, metric: dict[str, Any], grain: str) -> str | None:
    tfield = metric.get("physicalTimeField")
    if not tfield:
        return None
    adapter = get_adapter(dialect)
    quoted = adapter.quote_identifier(tfield)
    return adapter.render_time_bucket(f"f.{quoted}", grain)


def _is_time_dim(dim: str) -> bool:
    lowered = str(dim or "").lower()
    if lowered == "比较周期":
        return False
    return any(t in lowered for t in (
        "日期", "时间", "天", "日", "星期", "月份", "月", "周", "季度", "季", "年",
        "date", "time", "day", "weekday", "week", "month", "quarter", "year",
    ))




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
    contract = (task or {}).get("queryBinding") or {}
    structured = bool((task or {}).get("semanticIntent"))
    metric = contract.get("metric")
    if not metric and not structured:
        metric = _pick_metric(metrics, qtext, (task or {}).get("metricHint") or None)
    if not metric:
        return None
    dialect = (dialect or (semantic_context or {}).get("source", {}).get("type") or "mysql").lower()
    adapter = get_adapter(dialect)
    source = (semantic_context or {}).get("source") or {}

    tables = (semantic_context or {}).get("tables") or []
    fact = None
    bound_fact = str(contract.get("factTable") or "")
    if bound_fact:
        fact = next((t for t in tables if bound_fact in {str(t.get("qualifiedName") or ""), str(t.get("name") or "")}), None)
    if not fact:
        src = set(metric.get("sourceTables") or [])
        fact = next((t for t in tables if {str(t.get("name") or ""), str(t.get("qualifiedName") or "")} & src), None)
    if not fact:
        fact = next((t for t in tables or [] if "fact" in str(t.get("objectType", "")).lower()), None)
    if not fact:
        return None

    def qualified_table(table):
        return adapter.qualify_table(PhysicalTableRef(
            schema=str(table.get("schemaName") or ""),
            name=str(table.get("name") or ""),
        ))

    fq = qualified_table(fact)
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
    filter_bindings = contract.get("filters") or (task or {}).get("filterBindings") or []
    if (task.get("filters") or []) and len(filter_bindings) != len(task.get("filters") or []):
        return None
    for binding in filter_bindings:
        rendered = _render_bound_filter(binding, adapter)
        if not rendered:
            return None
        where_parts.append(rendered)

    group_by = (task or {}).get("groupBy") or []
    bindings = {str(item.get("semantic")): item for item in (task or {}).get("dimensionBindings") or []}
    dim_cols: list[str] = []
    dim_raw: list[str] = []
    for dim in group_by:
        if dim == "比较周期":
            col = _period_bucket(metric, periods, dialect)
            if not col:
                return None
            dim_cols.append(col)
            dim_raw.append(dim)
            continue
        if _is_time_dim(dim):
            time_metric = dict(metric)
            if (bindings.get(str(dim)) or {}).get("field"):
                time_metric["physicalTimeField"] = bindings[str(dim)]["field"]
            col = _time_group(dialect, time_metric, (_dim_time_grain(dim) or str(time_constraint.get("timeGrain") or "day")))
            if not col:
                # 用户明确要求“按月份/按周/按天/逐月”拆分，但当前指标没有登记时间字段，
                # 不能静默退化为整段聚合（会把“各月份”变成“全年求和”）。回退给 LLM 生成。
                return None
            dim_cols.append(col)
            dim_raw.append(dim)
            continue
        binding = bindings.get(str(dim)) or {}
        if binding.get("status") == "unresolved_join":
            return None
        col = binding.get("field")
        if not col and not structured:
            col = _resolve_dimension(dim, fact, dialect)
        if not col:
            return None
        alias = str(binding.get("alias") or "f")
        dim_cols.append(f"{alias}.{adapter.quote_identifier(col)}")
        dim_raw.append(dim)

    atype = (task or {}).get("analysisType") or ""
    q = str((task or {}).get("normalizedQuestion") or (task or {}).get("originalQuestion") or "")
    limit: int | None = task.get("topN")
    order: str | None = None
    out_hint = "chart"
    if "rank" in atype or (not structured and ("top" in atype or "排行" in atype or "前" in q)):
        order = "DESC"
        m = re.search(r"前\s*(\d+)", q) if not structured else None
        if m and not limit:
            limit = int(m.group(1))
        if not dim_raw:
            if structured:
                return None
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
            col = _time_group(dialect, metric, str(time_constraint.get("timeGrain") or "day"))
            if col:
                dim_cols = [col]
                dim_raw = ["时间"]
    elif "share" in atype or (not structured and ("占比" in q or "比例" in q)):
        order = "DESC"
    elif "compare" in atype or "comparison" in atype or (not structured and ("对比" in q or "相比" in q)):
        order = "DESC"

    select_parts: list[str] = []
    for raw, col in zip(dim_raw, dim_cols):
        select_parts.append(f"{col} AS {adapter.quote_identifier(raw)}")
    select_parts.append(f"{metric_expr} AS {adapter.quote_identifier(metric.get('name') or 'metric')}")
    sql = f"SELECT {', '.join(select_parts)} FROM {fq} AS f"
    table_by_identity = {}
    for item in tables:
        table_by_identity[str(item.get("qualifiedName") or item.get("name") or "")] = item
        table_by_identity.setdefault(str(item.get("name") or ""), item)
    aliases = {str(contract.get("factTable") or ""): "f"}
    for join in contract.get("joins") or []:
        target = table_by_identity.get(str(join.get("toTable") or ""))
        source_alias = aliases.get(str(join.get("fromTable") or ""))
        target_alias = str(join.get("alias") or f"d{len(aliases)}")
        if not target or not source_alias or not join.get("fromField") or not join.get("toField"):
            return None
        sql += (
            f" JOIN {qualified_table(target)} AS {target_alias} ON "
            f"{source_alias}.{adapter.quote_identifier(str(join['fromField']))} = "
            f"{target_alias}.{adapter.quote_identifier(str(join['toField']))}"
        )
        aliases[str(join.get("toTable"))] = target_alias
    if where_parts:
        sql += " WHERE " + " AND ".join(where_parts)
    if dim_cols:
        sql += " GROUP BY " + ", ".join(dim_cols)
    if task.get("sort"):
        ordering = []
        for spec in task["sort"]:
            field = str(spec.get("field") or "")
            direction = str(spec.get("direction") or "asc").upper()
            if direction not in {"ASC", "DESC"} or field not in [*dim_raw, metric.get("name")]:
                return None
            ordering.append(f"{adapter.quote_identifier(field)} {direction}")
        sql += " ORDER BY " + ", ".join(ordering)
    elif order and dim_cols:
        if atype in ("trend",) or "趋势" in atype:
            order_col = dim_cols[0]
        else:
            order_col = metric_expr
        sql += " ORDER BY " + order_col + f" {order}"
    if limit:
        sql = adapter.apply_row_limit(sql, int(limit))
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
        "joins": contract.get("joins") or [],
        "axis": axis,
        "resultSchema": [
            {"key": d, "role": "time" if _is_time_dim(d) else "dimension",
             "grain": _dim_time_grain(d) if _is_time_dim(d) else None}
            for d in dim_raw
        ] + [{"key": metric.get("name") or "metric", "role": "measure", "unit": metric.get("unit")}],
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
    from app.agent.query_requirements import bind_query_requirements
    bound = bind_query_requirements(tasks[0], semantic_context)
    return compile_query(semantic_context, bound, dialect=dialect)
