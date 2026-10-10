"""Merge an intent patch into a source-scoped successful analysis snapshot."""

from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal
import json
import re
from typing import Any

from app.agent.ids import new_id
from app.agent.intent_contract import AgentIntent, Dimension, IntentKind, Metric, TimeRange


def _json_safe_copy(value: Any) -> Any:
    """Make persisted conversation context safe while retaining numeric roles."""
    def convert(item: Any) -> Any:
        if isinstance(item, Decimal):
            return int(item) if item == item.to_integral_value() else float(item)
        if isinstance(item, (date, datetime)):
            return item.isoformat()
        raise TypeError(f"unsupported context value: {type(item).__name__}")

    return json.loads(json.dumps(value, ensure_ascii=False, default=convert))


def _selected_result(intent: AgentIntent, previous: dict[str, Any] | None) -> dict[str, Any] | None:
    results = list((previous or {}).get("results") or [])
    if intent.targetResultId:
        return next((item for item in results if item.get("resultId") == intent.targetResultId), None)
    if len(results) == 1:
        return results[0]
    return None


def _chart_can_convert(chart: dict[str, Any], target: str) -> bool:
    source_type = str(chart.get("type") or "")
    if source_type == target:
        return True
    data = list(chart.get("data") or [])
    series = chart.get("series") or chart.get("bars") or chart.get("lines") or []
    if target in {"line", "bar"} and source_type in {"line", "bar"}:
        return bool(chart.get("xKey")) and bool(data) and bool(series)
    if target == "donut" and source_type in {"line", "bar", "donut"}:
        has_category_axis = bool(chart.get("xKey")) and (chart.get("xAxis") or {}).get("kind") != "time"
        value_key = series[0].get("key") if len(series) == 1 else chart.get("valueKey")
        return bool(has_category_axis and value_key and data and all(row.get(value_key) is not None for row in data))
    return False


def presentation_reuse_task(intent: AgentIntent, previous: dict[str, Any] | None) -> dict[str, Any] | None:
    """Create a query-free task when an accepted result already contains reusable chart data."""
    if intent.operation != "change_presentation" or not intent.chart:
        return None
    selected = _selected_result(intent, previous)
    interpretation = deepcopy((selected or {}).get("interpretation") or {})
    chart = interpretation.get("chart") or ((interpretation.get("charts") or [None])[0])
    if not chart:
        return None
    target = intent.chart.type
    charts = interpretation.get("charts") or [chart]
    # Every rendered chart must be losslessly convertible.  Partially changing a
    # multi-chart result would misrepresent what the user asked to update.
    if not charts or not all(_chart_can_convert(item, target) for item in charts):
        return None
    prior_task = deepcopy((selected or {}).get("task") or {})
    metric_hint = None
    if intent.metrics:
        metric_hint = intent.metrics[0].semantic
    metric_hint = metric_hint or ((selected or {}).get("metricResolution") or {}).get("name")
    return {
        **prior_task,
        "id": new_id("task"),
        "order": 1,
        "requirementId": "R1",
        "title": intent.question[:80],
        "originalQuestion": intent.question,
        "normalizedQuestion": intent.question,
        "requestedChart": intent.chart.type,
        "metricHint": metric_hint,
        "relation": intent.relation,
        "operation": intent.operation,
        "semanticIntent": intent.model_dump(mode="json", by_alias=True),
        "presentationReuse": {
            "sourceResultId": selected.get("resultId"),
            "sql": selected.get("sql"),
            "metricResolution": deepcopy(selected.get("metricResolution") or {}),
            "execution": deepcopy(selected.get("execution") or {}),
            "interpretation": interpretation,
        },
    }


def apply_presentation_change(task: dict[str, Any]) -> dict[str, Any]:
    """Change only the visual contract; preserve accepted data, SQL and metric semantics."""
    reuse = deepcopy(task.get("presentationReuse") or {})
    interpretation = deepcopy(reuse.get("interpretation") or {})
    target = str(task.get("requestedChart") or "")
    charts = deepcopy(
        interpretation.get("charts")
        or ([interpretation.get("chart")] if interpretation.get("chart") else [])
    )
    converted = []
    for chart in charts:
        chart = deepcopy(chart)
        source_type = str(chart.get("type") or "")
        if source_type == target:
            chart["kicker"] = "展示复用 · 已验证结果"
            chart["note"] = "沿用已验证结果，未重新查询数据库。"
            converted.append(chart)
            continue
        series = deepcopy(chart.get("series") or chart.get("bars") or chart.get("lines") or [])
        chart["type"] = target
        chart["kicker"] = "展示调整 · 复用既有查询结果"
        if target == "line":
            chart["series"] = series
        elif target == "bar":
            chart["bars"] = series
        elif target == "donut":
            value_key = series[0].get("key") if series else chart.get("valueKey")
            chart["valueKey"] = value_key
            chart["data"] = [
                {"name": row.get(chart.get("xKey")), "value": row.get(value_key)}
                for row in chart.get("data") or []
            ]
            values = [row.get("value") for row in chart["data"]]
            numeric = [float(value) for value in values if isinstance(value, (int, float)) and not isinstance(value, bool)]
            chart["centerTitle"] = "合计"
            chart["centerValue"] = sum(numeric) if len(numeric) == len(values) else "—"
            chart["legend"] = [{"label": str(row.get("name") or "未命名")} for row in chart["data"]]
        chart["note"] = "仅调整展示方式，未重新查询数据库。"
        converted.append(chart)
    interpretation["charts"] = converted
    if not converted:
        raise ValueError("没有可复用的图表契约")
    interpretation["chart"] = converted[0]
    interpretation["presentationOnly"] = True
    interpretation["sourceResultId"] = reuse.get("sourceResultId")
    return interpretation


def merge_intent(intent: AgentIntent, previous: dict[str, Any] | None) -> AgentIntent:
    if intent.relation == "new" or not previous:
        return intent
    candidates = list(previous.get("results") or [])
    selected = None
    if candidates:
        if intent.targetResultId:
            selected = next((item for item in candidates if item.get("resultId") == intent.targetResultId), None)
        elif len(candidates) == 1:
            selected = candidates[0]
        else:
            return intent.model_copy(update={
                "kind": IntentKind.CLARIFY,
                "clarify": [{
                    "id": "targetResultId", "label": "要修改的分析结果",
                    "question": "本会话包含多个分析结果，请选择要继续调整哪一个。",
                    "options": [str(item.get("resultId")) for item in candidates],
                    "allowCustom": False,
                }],
                "reasons": [*(intent.reasons or []), "多结果追问缺少明确目标"],
            })
        if selected is None:
            return intent.model_copy(update={
                "kind": IntentKind.CLARIFY,
                "clarify": [{"id": "targetResultId", "label": "要修改的分析结果",
                             "question": "没有找到指定结果，请重新选择。",
                             "options": [str(item.get("resultId")) for item in candidates], "allowCustom": False}],
            })
    prior = (selected or {}).get("agentIntent") or previous.get("agentIntent") or {}
    result = intent.model_copy(deep=True)
    changed = set(intent.changedFields)
    notes = []
    for name in ("metrics", "time", "chart", "comparison", "filters", "sort", "topN"):
        if name not in changed and prior.get(name) is not None:
            setattr(result, name, deepcopy(getattr(AgentIntent.model_validate(prior), name)))
            notes.append({"metrics": "指标", "time": "时间范围", "chart": "展示方式", "comparison": "比较条件", "filters": "筛选条件", "sort": "排序", "topN": "数量限制"}[name])
    old = [Dimension.model_validate(d) for d in prior.get("dimensions", [])]
    key = lambda d: (d.semantic, d.grain)
    # A short "那各工序呢" switches the grouping axis. Model patches can
    # misclassify it as add_dimension and produce an unwanted 产线×工序 matrix.
    explicit_axes = re.findall(r"各(工序|产线|产品|设备|车间|班次|缺陷类型)", intent.question)
    switches_axis = (
        len(explicit_axes) == 1
        and not any(word in intent.question for word in ("同时", "再加上", "以及", "和各", "与各"))
        and bool(old)
    )
    if switches_axis:
        result.dimensions = [Dimension(semantic=explicit_axes[0], label=explicit_axes[0])]
    elif intent.operation == "add_dimension":
        result.dimensions = old + [d for d in intent.dimensions if key(d) not in {key(o) for o in old}]
    elif intent.operation == "remove_dimension":
        result.dimensions = [d for d in old if key(d) not in {key(n) for n in intent.dimensions}]
    elif intent.operation != "replace_dimension" and "dimensions" not in changed:
        result.dimensions = old
    # The model may return a generic metric token even when the user explicitly
    # named a governed dimension-qualified metric ("工序不良率" -> "不良率").
    # Recover only text that is literally present in the current message; this is
    # a generic specificity rule, not a catalogue-specific alias table.
    if "metrics" in changed and result.metrics:
        for metric in result.metrics:
            semantic = str(metric.semantic or "").strip()
            for dimension in result.dimensions:
                qualified = f"{dimension.semantic}{semantic}"
                if semantic and qualified in intent.question:
                    metric.semantic = qualified
                    break
    if intent.operation == "replace_filter":
        new_keys = {f.get("field") or f.get("semantic") for f in intent.filters}
        result.filters = [f for f in prior.get("filters", []) if (f.get("field") or f.get("semantic")) not in new_keys] + intent.filters
    result.inheritedFrom = previous.get("runId")
    if selected:
        result.targetResultId = selected.get("resultId")
    result.stepsHint = []  # A follow-up patches one accepted analysis, not the old plan.
    result.understanding = (["沿用：" + "、".join(notes)] if notes else []) + [
        "本轮分组：" + ("、".join(d.label or d.semantic for d in result.dimensions) or "不分组")
    ]
    return result


def successful_snapshot(state: dict[str, Any]) -> dict[str, Any] | None:
    if not state.get("task_results") or not state.get("agent_intent"):
        return None
    results = []
    for index, item in enumerate(state["task_results"], 1):
        task = deepcopy(item.get("task") or {})
        intent = deepcopy(task.get("semanticIntent") or state["agent_intent"])
        periods = (task.get("timeConstraint") or {}).get("periods") or []
        if len(periods) == 1 and periods[0].get("startInclusive") and periods[0].get("endExclusive"):
            intent["time"] = {"mode": "absolute", "start": periods[0]["startInclusive"],
                              "end": periods[0]["endExclusive"], "reference": "now",
                              "label": (task.get("timeConstraint") or {}).get("label", "")}
        result_id = str(task.get("requirementId") or f"R{index}")
        results.append({"resultId": result_id, "label": task.get("title") or result_id,
                        "agentIntent": intent, "task": task, "sql": item.get("sql"),
                        "resultColumns": (item.get("execution") or {}).get("columns", []),
                        "execution": _json_safe_copy(item.get("execution") or {}),
                        "metricResolution": _json_safe_copy(item.get("metricResolution") or {}),
                        "interpretation": _json_safe_copy(item.get("interpretation") or {})})
    last = results[-1]
    return {"version": 2, "runId": state.get("run_id"), "sourceId": state.get("source_id"),
            "results": results, "primaryResultId": last["resultId"],
            "agentIntent": last["agentIntent"], "task": last["task"], "sql": last["sql"],
            "resultColumns": last["resultColumns"], "metricResolution": last["metricResolution"]}
