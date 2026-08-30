"""Deterministic progress, stop and bounded-replan decisions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.agent.plan_schema import AgentPlan


@dataclass(frozen=True)
class ProgressDecision:
    action: str
    reason_code: str
    summary: str


def completed_step_ids(observations: list[dict[str, Any]]) -> set[str]:
    return {
        str(item.get("stepId"))
        for item in observations
        if item.get("status") in {"succeeded", "accepted", "skipped"}
    }


def failed_step_ids(observations: list[dict[str, Any]]) -> set[str]:
    return {
        str(item.get("stepId"))
        for item in observations
        if item.get("status") == "failed"
    }


def select_next_step(plan: AgentPlan, observations: list[dict[str, Any]]) -> dict[str, Any] | None:
    done = completed_step_ids(observations)
    failed = failed_step_ids(observations)
    observed = done | failed
    reusable_found = any(
        item.get("capability") == "modeling.find_reusable"
        and bool((item.get("payload") or {}).get("models"))
        for item in observations
    )
    for step in plan.steps:
        if step.id in observed:
            continue
        if step.arguments.get("onlyIfNoReusable") and reusable_found:
            return {
                "step": step.model_dump(by_alias=True, mode="json"),
                "skip": True,
                "skipReason": "已找到可复用的已发布模型，无需重复生成训练提案",
            }
        if set(step.depends_on).issubset(done):
            return {"step": step.model_dump(by_alias=True, mode="json"), "skip": False}
    return None


def evaluate_progress(
    plan: AgentPlan,
    observations: list[dict[str, Any]],
    *,
    replan_count: int,
) -> ProgressDecision:
    failed = failed_step_ids(observations)
    if failed and replan_count < plan.budget.max_replans:
        retryable = any(
            item.get("stepId") in failed
            and item.get("reasonCode")
            not in {"ROLE_DENIED", "SCOPE_DENIED", "SOURCE_SCOPE_DENIED", "APPROVAL_REQUIRED"}
            for item in observations
        )
        if retryable:
            return ProgressDecision("replan", "RECOVERABLE_CAPABILITY_FAILURE", "能力失败，可进行一次受限计划调整")
    next_step = select_next_step(plan, observations)
    if next_step is not None:
        return ProgressDecision("continue", "PENDING_STEP_AVAILABLE", "仍有依赖已满足的计划步骤")
    if failed:
        return ProgressDecision("finalize", "PARTIAL_RESULT", "部分能力未完成，返回已有证据与限制")
    return ProgressDecision("finalize", "GOAL_SATISFIED", "计划内目标已完成")


def result_quality_from_task_results(task_results: list[dict[str, Any]]) -> dict[str, Any]:
    if not task_results:
        return {
            "coverage": 0.0,
            "sampleCount": 0,
            "limitations": ["尚无可检查的查询结果"],
        }
    row_counts = [
        int((item.get("execution") or {}).get("row_count") or 0)
        for item in task_results
    ]
    limitations: list[str] = []
    if not any(row_counts):
        limitations.append("查询没有返回记录")
    for item in task_results:
        confidence = item.get("confidence") or {}
        limitations.extend(str(value) for value in confidence.get("limitations") or [])
    return {
        "coverage": 1.0 if all(value > 0 for value in row_counts) else 0.5 if any(row_counts) else 0.0,
        "sampleCount": sum(row_counts),
        "limitations": list(dict.fromkeys(limitations)),
    }
