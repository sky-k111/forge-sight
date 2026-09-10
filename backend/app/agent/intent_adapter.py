"""AgentIntent -> 现有 AnalysisTask 形状的兼容适配器。

让现有受治理子图在【不重写】的前提下获得正确语义：下游不再从自由文本正则猜
维度/时间/图/分析类型，而是消费已解析意图。本模块只依赖 intent_contract（无 planner
依赖），避免与 llm_intent / planner 形成循环导入。
"""

from __future__ import annotations

import re
from typing import Any

from app.agent.ids import new_id
from app.agent.intent_contract import AgentIntent, Dimension, Grain, StepIntent, TimeRange, TimeMode

_GRAIN_TOKEN: dict[Grain, str] = {
    "day": "日", "week": "周", "month": "月份", "quarter": "季度", "year": "年",
}


def _dimension_token(dim: Dimension) -> str:
    if dim.semantic == "时间":
        return _GRAIN_TOKEN.get(dim.grain or "day", "月份")
    return dim.semantic


def _time_dimish(dim: str) -> bool:
    """判断维度 token 是否时间类（用于避免对比查询里重复追加时间粒度）。"""
    return any(t in str(dim or "") for t in ("月", "周", "日", "年", "季度", "时间", "日期"))


def _extract_month(label: str) -> int | None:
    if not label:
        return None
    m = re.search(r"(\d{1,2})\s*月", label)
    if m:
        v = int(m.group(1))
        return v if 1 <= v <= 12 else None
    return None


def _time_constraint_from_range(r: TimeRange | None) -> dict[str, Any] | None:
    if r is None:
        return None
    label = r.label or ""
    grain = r.grain or "day"

    def period_dict(p: TimeRange) -> dict[str, Any]:
        d: dict[str, Any] = {"label": p.label or ""}
        if p.start:
            d["startInclusive"] = p.start
        if p.end:
            d["endExclusive"] = p.end
        return d

    if r.mode == TimeMode.COMPARISON:
        # 对比型（如「5月 vs 6月」）：裸月份可由 time_resolution 锚定到数据
        # 地平线，但用户明确给出的 start/end 必须原样进入查询约束。
        members = [r] + list(r.comparisons)
        return {
            "kind": "comparison",
            "label": label,
            "timeGrain": grain,
            "reference": r.reference,
            "periods": [period_dict(m) for m in members],
        }
    if r.mode == TimeMode.ABSOLUTE:
        # 优先按 label 里的「N 月」锚定数据地平线，而不是无条件使用 LLM 猜的绝对日期
        # （LLM 常把“5月”写成 2023-05-01）；这样“对比5月与6月”能落到真实数据年份。
        month = _extract_month(label) if not (r.start and r.end) else None
        if month is not None and r.reference != "now":
            return {"kind": "specific_month", "month": month, "label": label, "timeGrain": grain, "reference": "data_horizon"}
        if r.start or r.end:
            return {"kind": "absolute", "label": label, "timeGrain": grain, "reference": r.reference, "periods": [period_dict(r)]}
        return {"kind": "absolute", "label": label, "timeGrain": grain, "reference": r.reference}
    if r.mode == TimeMode.CURRENT:
        kind = {"month": "current_month", "week": "current_week", "quarter": "current_quarter", "year": "current_year"}.get(grain, "current_month")
        return {"kind": kind, "label": label, "timeGrain": grain, "reference": "now"}
    if r.mode == TimeMode.RECENT:
        return {"kind": "recent", "label": label, "amount": r.amount, "unit": r.unit, "timeGrain": grain, "reference": r.reference}
    if r.mode == TimeMode.RELATIVE:
        kind = {"week": "previous_week", "month": "previous_month", "quarter": "previous_quarter", "year": "previous_year"}.get(grain, "previous_month")
        return {"kind": kind, "label": label, "timeGrain": grain, "reference": r.reference}
    return None


def _analysis_type_from(intent: AgentIntent | StepIntent) -> str:
    if getattr(intent, "comparison", None) is not None:
        return "comparison"
    metrics = getattr(intent, "metrics", None) or []
    agg = {getattr(m, "aggregation", "") for m in metrics}
    if "rank" in agg:
        return "rank"
    if "share" in agg:
        return "share"
    chart = getattr(intent, "chart", None)
    if chart is not None and chart.type == "line":
        return "trend"
    return "query"


def _comparison_periods(intent: AgentIntent) -> list[TimeRange]:
    """取对比周期：优先 Comparison.periods（LLM 常把两段都放这里），否则退回 time.comparisons，
    并按 label 去重，避免把 5月 同时算两次。"""
    periods: list[TimeRange] = []
    if intent.comparison is not None:
        periods = list(intent.comparison.periods)
    if not periods and intent.time is not None and intent.time.mode == TimeMode.COMPARISON:
        periods = list(intent.time.comparisons)
    seen: set[str] = set()
    out: list[TimeRange] = []
    for p in periods:
        key = p.label or f"{p.start}-{p.end}"
        if key in seen:
            continue
        seen.add(key)
        # 裸周期由后续时间解析锚定；显式 start/end 属于用户的绝对约束，不能覆盖。
        out.append(p)
    return out


def _metric_compound_pieces(q: str) -> list[str]:
    """仅当问题由「A指标和B指标 / A以及B」这类并列计量词连接时拆成子问题，否则单题。"""
    from app.agent.intent import split_metric_compound
    pieces = split_metric_compound(q)
    return pieces if len(pieces) > 1 else [q]


_CONTEXT_METRIC_RE = None


def _metric_first_index(text: str) -> int:
    """返回问题中第一个指标词的起始下标，用于切出共享的时间/维度前缀。"""
    import re as _re
    global _CONTEXT_METRIC_RE
    if _CONTEXT_METRIC_RE is None:
        _CONTEXT_METRIC_RE = _re.compile(
            r"不良率|返工率|合格率|良率|停机时长|合格数量|不良数量|库存|产值|成本|价格|薪资|占比|产量|数量|时长|金额"
        )
    m = _CONTEXT_METRIC_RE.search(text)
    return m.start() if m else -1


_CONTEXT_TOKEN_RE = None


def _has_context(piece: str) -> bool:
    """片段是否已自带时间或维度上下文，无需再补前缀。"""
    import re as _re
    global _CONTEXT_TOKEN_RE
    if _CONTEXT_TOKEN_RE is None:
        _CONTEXT_TOKEN_RE = _re.compile(r"月|周|年|今年|季度|日|每天|产线|产品|工序|车间|设备|班次|缺陷")
    return bool(_CONTEXT_TOKEN_RE.search(piece))


def _augment_pieces(q: str, pieces: list[str]) -> list[str]:
    """把共享的时间/维度前缀（如「6月份各产线的」）补到后续裸指标片段上，
    使「产量以及良率」拆出的「良率」变成「6月份各产线的良率」，保留维度/时间继承。"""
    idx = _metric_first_index(q)
    prefix = q[:idx].strip() if 0 < idx < len(q) else ""
    if not prefix:
        return pieces
    out = [pieces[0]]
    for piece in pieces[1:]:
        if piece and not _has_context(piece):
            piece = (prefix + piece).strip()
        elif piece and piece.startswith(prefix):
            piece = piece
        out.append(piece)
    return out


_METRIC_HINT_RE = None


def _metric_hint_of(piece: str) -> str:
    """从片段中提取最具体的指标词（优先非子串匹配，如 不良率 优先于 良率）。"""
    import re as _re
    text = str(piece or "")
    for core in ("不良率", "返工率", "合格率", "良率", "停机时长", "合格数量", "不良数量",
                 "产量", "库存", "产值", "成本", "价格", "薪资", "占比", "数量", "时长", "金额"):
        if core in text:
            return core
    return text.strip()[:20]


def _resolve_tc(time_range, q: str) -> dict[str, Any] | None:
    """优先取 LLM 意图解析的时间；缺失时用确定性时间正则兜底（如裸“2020年”）。"""
    tc = _time_constraint_from_range(time_range)
    if tc is not None:
        return tc
    from app.agent.intent import _time_constraint as _det_tc
    return _det_tc(q)


def intent_to_tasks(intent: AgentIntent, *, question: str | None = None) -> list[dict[str, Any]]:
    """把 AgentIntent 翻译成下游受治理子图能直接消费的 AnalysisTask 列表。"""
    q = question or intent.question
    if intent.stepsHint:
        tasks = []
        for index, step in enumerate(intent.stepsHint):
            child = AgentIntent.model_validate({**intent.model_dump(),
                **step.model_dump(), "stepsHint": [], "relation": "new",
                "question": q, "comparison": step.comparison,
            })
            for task in intent_to_tasks(child, question=q):
                task["order"] = len(tasks) + 1
                task["requirementId"] = f"R{index + 1}"
                tasks.append(task)
        return tasks
    if intent.relation != "new":
        q = "；".join([q, "指标：" + "、".join(m.semantic for m in intent.metrics)])
    base_dim_tokens = [_dimension_token(d) for d in intent.dimensions]
    chart = intent.chart.type if intent.chart else None
    atype = _analysis_type_from(intent)
    metric_hint = intent.metrics[0].semantic if intent.metrics else None

    periods = _comparison_periods(intent)

    def _task(idx: int, tc: dict[str, Any] | None, mode: str, depends: list[str],
              hint: str | None = None, nq: str | None = None) -> dict[str, Any]:
        text = nq or q
        return {
            "id": new_id("task"),
            "order": idx + 1,
            "title": text[:80],
            "normalizedQuestion": text,
            "originalQuestion": text,
            "mode": mode,
            "analysisType": atype,
            "requestedChart": chart,
            "timeConstraint": tc,
            "groupBy": list(dict.fromkeys(base_dim_tokens)),
            "dependsOn": depends,
            "metricHint": hint if hint is not None else metric_hint,
            "filters": intent.filters,
            "sort": intent.sort,
            "topN": intent.topN,
            "understanding": intent.understanding,
            "relation": intent.relation,
            "operation": intent.operation,
            "inheritedFrom": intent.inheritedFrom,
            "requirementId": "R1",
            "semanticIntent": intent.model_dump(mode="json", by_alias=True),
        }

    if periods:
        task = _task(0, {"kind": "comparison", "label": intent.comparison.basis if intent.comparison else "周期比较",
                         "timeGrain": (intent.time.grain if intent.time else None) or "month", "reference": periods[0].reference,
                         "periods": [{"label": p.label, "reference": p.reference,
                                      **({"startInclusive": p.start} if p.start else {}),
                                      **({"endExclusive": p.end} if p.end else {})} for p in periods]}, "single", [])
        if not any(_time_dimish(d) for d in task["groupBy"]):
            task["groupBy"].append("月份")
        return [task]

    if intent.producer == "llm":
        return [_task(0, _time_constraint_from_range(intent.time), "single", [], hint=metric_hint, nq=q)]

    pieces = _metric_compound_pieces(q)
    if len(pieces) > 1:
        from app.agent.intent import _group_dimensions as _group_dims, _time_breakdown_dimensions as _time_dims
        augmented = _augment_pieces(q, pieces)
        tasks = []
        for i, piece in enumerate(augmented):
            dims = list(dict.fromkeys(list(base_dim_tokens) + _group_dims(piece) + _time_dims(piece)))
            tasks.append(_task(i, _resolve_tc(intent.time, piece), "parallel", [],
                               hint=_metric_hint_of(piece), nq=piece))
            tasks[i]["groupBy"] = dims
        return tasks
    return [_task(0, _resolve_tc(intent.time, q), "single", [],
                  hint=metric_hint, nq=q)]


def task_from_planner_arguments(arguments: dict[str, Any] | None, fallback_task: dict[str, Any] | None = None) -> dict[str, Any]:
    """把 bounded-planner 步骤的 arguments（dimensions/time/metrics/comparison）
    翻译回受治理子图可消费的 task（groupBy/timeConstraint/analysisType/metricHint）。

    planner 生成的 arguments.task 常有空的 groupBy/timeConstraint，真正的语义在
    arguments.dimensions/time/metrics/comparison 里；这里把它们回填到 task，使
    compile_query 能真正按维度/时间分组，而不是退回整段聚合标量。
    """
    args = arguments or {}
    task = dict(fallback_task or {})
    dims = args.get("dimensions") or []
    metrics = args.get("metrics") or []
    time = args.get("time") or {}
    comparison = args.get("comparison") or {}

    group_by = list(task.get("groupBy") or [])
    if not group_by and dims:
        for d in dims:
            # planner 的 dimensions 有时是纯字符串（如 "车间"/"产线"），
            # 直接喂给 Dimension.model_validate 会抛 ValidationError，导致维度回填整体失败。
            if isinstance(d, Dimension):
                dim = d
            elif isinstance(d, dict):
                try:
                    dim = Dimension.model_validate(d)
                except Exception:  # noqa: BLE001 - 未知结构时退回字符串兜底
                    dim = Dimension(semantic=str(d.get("semantic") or d.get("label") or ""), label=str(d))
            else:
                dim = Dimension(semantic=str(d), label=str(d))
            token = _dimension_token(dim)
            if token and token not in group_by:
                group_by.append(token)

    tc = task.get("timeConstraint")
    if tc is None and time:
        if isinstance(time, TimeRange):
            model = time
        elif isinstance(time, dict):
            tdict = dict(time)
            # planner 的 time 常缺 mode（有 start/end/grain）；有起止即视为绝对区间。
            if not tdict.get("mode"):
                tdict["mode"] = "absolute"
                tdict.setdefault("reference", "data_horizon")
            try:
                model = TimeRange.model_validate(tdict)
            except Exception:  # noqa: BLE001 - 结构异常时跳过时间约束，不阻塞查询
                model = None
        else:
            model = None
        tc = _time_constraint_from_range(model) if model is not None else None

    atype = task.get("analysisType") or "query"
    if comparison or (tc and tc.get("kind") == "comparison"):
        atype = "comparison"

    if metrics and isinstance(metrics[0], dict):
        first_metric = metrics[0].get("semantic")
    elif metrics:
        first_metric = str(metrics[0])
    else:
        first_metric = None
    metric_hint = task.get("metricHint") or first_metric

    # 对比型查询补时间粒度维度，使 SQL 按 月份/周 拆分，而不是把两段时期单独 AND。
    if tc and tc.get("kind") == "comparison":
        grain = tc.get("timeGrain") or "month"
        ttoken = _GRAIN_TOKEN.get(grain, "月份")
        if not any(_time_dimish(g) for g in group_by):
            group_by.append(ttoken)

    task["groupBy"] = list(dict.fromkeys(group_by))
    task["timeConstraint"] = tc
    task["analysisType"] = atype
    if metric_hint:
        task["metricHint"] = metric_hint
    return task
