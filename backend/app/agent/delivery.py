"""Immutable request requirements and deterministic delivery accounting.

The ledger is derived from the accepted request contract, never from a mutable
planner output.  New business capabilities register their deliverable here so
the finalizer can account for them without adding another control-flow branch.
"""
from datetime import datetime
from typing import Any

from app.agent.intent_adapter import intent_to_tasks
from app.agent.intent_contract import AgentIntent
from app.agent.time_resolution import resolve_time_constraint


CAPABILITY_DELIVERABLES: tuple[dict[str, Any], ...] = (
    {
        "id": "M1",
        "intentKinds": {"modeling"},
        "specField": "modeling",
        "capabilities": ["modeling.find_reusable", "modeling.propose"],
        "label": "建模结果",
    },
    {
        "id": "P1",
        "intentKinds": {"report"},
        "specField": "report",
        "capabilities": ["report.compose_from_evidence"],
        "label": "报告草稿",
    },
)


def build_requirement_ledger(intent: AgentIntent, question: str = "") -> list[dict[str, Any]]:
    """Build requirements from user semantics, independently from a mutable plan."""
    ledger: list[dict[str, Any]] = []
    for task in intent_to_tasks(intent, question=question or intent.question):
        rid = str(task.get("requirementId") or f"R{len(ledger) + 1}")
        time_constraint = task.get("timeConstraint")
        if time_constraint and time_constraint.get("reference") == "now":
            time_constraint = resolve_time_constraint(time_constraint)
        contract = {
            key: (time_constraint if key == "timeConstraint" else task.get(key))
            for key in ("metricHint", "timeConstraint", "groupBy", "filters", "sort", "topN", "analysisType")
            if task.get(key) not in (None, [], {})
        }
        ledger.append({"id": rid, "kind": "query", "taskRequirementId": rid,
                       "label": task.get("title") or task.get("normalizedQuestion") or rid,
                       "contract": contract})
        chart = str(task.get("requestedChart") or "").lower()
        if chart and chart not in {"auto", "table", "none"}:
            ledger.append({"id": f"{rid}:chart", "kind": "chart", "taskRequirementId": rid,
                           "chartType": chart, "label": f"{rid} 的{chart}图表", "contract": contract})
    for spec in CAPABILITY_DELIVERABLES:
        if intent.kind.value in spec["intentKinds"] or getattr(intent, spec["specField"], None) is not None:
            ledger.append({
                "id": spec["id"],
                "kind": "capability",
                "capabilities": list(spec["capabilities"]),
                "label": spec["label"],
            })
    return ledger


def ledger_from_tasks(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Compatibility ledger for the legacy parser; it is still frozen before execution."""
    ledger = []
    for task in tasks:
        rid = str(task.get("requirementId") or f"R{len(ledger) + 1}")
        ledger.append({"id": rid, "kind": "query", "taskRequirementId": rid,
                       "label": task.get("title") or rid})
    return ledger


def number_requirements(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach stable request IDs before execution; never infer them from results."""
    return [{**task, "requirementId": task.get("requirementId") or f"R{index}"}
            for index, task in enumerate(tasks, 1)]


def assess_delivery(requirements: list[dict[str, Any]], results: list[dict[str, Any]],
                    observations: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    delivered: list[str] = []
    observations = observations or []
    for requirement in requirements:
        rid = requirement.get("id")
        task_id = requirement.get("taskRequirementId")
        matching = [item for item in results if item.get("task", {}).get("requirementId") == task_id
                    and not item.get("error") and _result_matches_contract(item, requirement.get("contract") or {})]
        if requirement.get("kind") == "query" and matching:
            delivered.append(rid)
        elif requirement.get("kind") == "chart" and any(
            str(((item.get("interpretation") or {}).get("chart") or {}).get("type") or "").lower()
            == requirement.get("chartType")
            and _chart_has_data(item)
            and _matches_sibling_query_requirement(item, task_id, requirements)
            for item in matching
        ):
            delivered.append(rid)
        elif requirement.get("kind") == "capability" and any(
            item.get("status") in {"succeeded", "accepted"}
            and item.get("capability") in set(requirement.get("capabilities") or [])
            for item in observations
        ):
            delivered.append(rid)
    required = [str(item.get("id")) for item in requirements]
    missing = [rid for rid in required if rid not in delivered]
    return {"requirements": requirements, "required": required, "delivered": delivered,
            "missing": missing, "complete": not missing}


def _matches_sibling_query_requirement(result: dict[str, Any], task_id: Any,
                                       requirements: list[dict[str, Any]]) -> bool:
    """A chart cannot outlive the query contract it visualizes."""
    query = next((item for item in requirements
                  if item.get("kind") == "query" and item.get("taskRequirementId") == task_id), None)
    return query is None or _result_matches_contract(result, query.get("contract") or {})


def _period_keys(periods: list[dict[str, Any]]) -> set[tuple[str, str]]:
    return {
        (_canonical_boundary(item.get("startInclusive")), _canonical_boundary(item.get("endExclusive")))
        for item in periods if item.get("startInclusive") and item.get("endExclusive")
    }


def _canonical_boundary(value: Any) -> str:
    """Compare equivalent date and midnight-datetime boundaries consistently."""
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    if parsed.time().isoformat() == "00:00:00":
        return parsed.date().isoformat()
    return parsed.isoformat()


def _same_metric(expected: Any, result: dict[str, Any]) -> bool:
    """Accept a governed metric's canonical name when the request used an alias."""
    expected_text = str(expected or "").strip()
    task = result.get("task") or {}
    resolution = result.get("metricResolution") or {}
    candidates = {
        str(task.get("metricHint") or "").strip(),
        str(resolution.get("metric") or "").strip(),
        str(resolution.get("name") or "").strip(),
    } - {""}
    if expected_text in candidates:
        return True
    return any(expected_text in item or item in expected_text for item in candidates)


def _result_matches_contract(result: dict[str, Any], contract: dict[str, Any]) -> bool:
    """Check immutable request semantics, not merely task/result existence."""
    task = result.get("task") or {}
    if contract.get("metricHint") and not _same_metric(contract.get("metricHint"), result):
        return False
    requested_groups = set(contract.get("groupBy") or [])
    if requested_groups and not requested_groups.issubset(set(task.get("groupBy") or [])):
        return False
    expected_tc = contract.get("timeConstraint") or {}
    actual_tc = task.get("timeConstraint") or {}
    if expected_tc.get("kind") == "comparison" and actual_tc.get("kind") != "comparison":
        return False
    expected_periods = _period_keys(expected_tc.get("periods") or [])
    actual_periods = _period_keys(actual_tc.get("periods") or [])
    if expected_periods and not expected_periods.issubset(actual_periods):
        return False
    execution = result.get("execution")
    return not isinstance(execution, dict) or bool(execution.get("ok", True))


def _chart_has_data(result: dict[str, Any]) -> bool:
    execution = result.get("execution")
    if not isinstance(execution, dict):
        return True  # legacy result shape
    if execution.get("rows"):
        return True
    # A successfully executed query with zero rows is a valid, auditable no-data
    # answer.  Reclassifying it as a missing chart turns an honest empty period into
    # a false execution failure and encourages fabricated points.
    if execution.get("ok") and int(execution.get("row_count") or 0) == 0:
        return True
    chart = (result.get("interpretation") or {}).get("chart") or {}
    return bool(chart.get("data"))
