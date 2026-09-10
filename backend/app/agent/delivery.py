"""Immutable request requirements and deterministic delivery accounting.

The ledger is derived from the accepted request contract, never from a mutable
planner output.  New business capabilities register their deliverable here so
the finalizer can account for them without adding another control-flow branch.
"""
from typing import Any

from app.agent.intent_adapter import intent_to_tasks
from app.agent.intent_contract import AgentIntent


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
        ledger.append({"id": rid, "kind": "query", "taskRequirementId": rid,
                       "label": task.get("title") or task.get("normalizedQuestion") or rid})
        chart = str(task.get("requestedChart") or "").lower()
        if chart and chart not in {"auto", "table", "none"}:
            ledger.append({"id": f"{rid}:chart", "kind": "chart", "taskRequirementId": rid,
                           "chartType": chart, "label": f"{rid} 的{chart}图表"})
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
                    and not item.get("error")]
        if requirement.get("kind") == "query" and matching:
            delivered.append(rid)
        elif requirement.get("kind") == "chart" and any(
            str(((item.get("interpretation") or {}).get("chart") or {}).get("type") or "").lower()
            == requirement.get("chartType") for item in matching
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
