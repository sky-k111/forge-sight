"""Authorization, budget, idempotency and audit envelope for capabilities."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from app.agent.capability_registry import (
    AuthorizationDecision,
    CapabilityContext,
    CapabilityRegistry,
    CapabilityResult,
    authorize_descriptor,
)
from app.agent.plan_schema import EffectLevel, PlanBudget


@dataclass(frozen=True)
class BudgetDecision:
    allowed: bool
    reason_code: str = "OK"
    message: str = ""


def normalized_arguments_hash(arguments: dict[str, Any]) -> str:
    payload = json.dumps(arguments, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def capability_idempotency_key(
    run_id: str,
    plan_revision: int,
    step_id: str,
    capability_version: str,
    arguments: dict[str, Any],
) -> str:
    raw = ":".join(
        (
            run_id,
            str(plan_revision),
            step_id,
            capability_version,
            normalized_arguments_hash(arguments),
        )
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def check_capability_budget(
    budget: PlanBudget,
    calls: list[dict[str, Any]],
    capability_name: str,
    effect_level: EffectLevel,
) -> BudgetDecision:
    completed = [call for call in calls if call.get("status") in {"succeeded", "accepted", "failed"}]
    if len(completed) >= budget.max_capability_calls:
        return BudgetDecision(False, "CAPABILITY_BUDGET_EXHAUSTED", "已达到本次能力调用上限")
    database_calls = sum(
        1
        for call in completed
        if call.get("capability") == "analysis.query_data"
    )
    if capability_name == "analysis.query_data" and database_calls >= budget.max_database_executions:
        return BudgetDecision(False, "DATABASE_BUDGET_EXHAUSTED", "已达到本次数据库执行上限")
    async_calls = sum(1 for call in completed if call.get("status") == "accepted")
    if effect_level == EffectLevel.LONG_RUNNING and async_calls >= budget.max_async_jobs:
        return BudgetDecision(False, "ASYNC_JOB_BUDGET_EXHAUSTED", "已达到本次异步任务上限")
    return BudgetDecision(True)


class CapabilityExecutor:
    def __init__(self, registry: CapabilityRegistry):
        self.registry = registry

    def authorize(
        self,
        context: CapabilityContext,
        capability_name: str,
        arguments: dict[str, Any],
        budget: PlanBudget,
        calls: list[dict[str, Any]],
    ) -> AuthorizationDecision:
        capability = self.registry.get(capability_name)
        base = authorize_descriptor(capability.descriptor, context, arguments)
        if not base.allowed or base.requires_approval:
            return base
        custom = capability.authorize(context, arguments)
        if not custom.allowed or custom.requires_approval:
            return custom
        budget_decision = check_capability_budget(
            budget,
            calls,
            capability_name,
            capability.descriptor.effect_level,
        )
        return AuthorizationDecision(
            budget_decision.allowed,
            budget_decision.reason_code,
            budget_decision.message,
        )

    def execute(
        self,
        context: CapabilityContext,
        capability_name: str,
        arguments: dict[str, Any],
        previous_calls: list[dict[str, Any]],
    ) -> tuple[CapabilityResult, dict[str, Any]]:
        capability = self.registry.get(capability_name)
        key = context.idempotency_key
        for call in previous_calls:
            if call.get("idempotencyKey") == key and call.get("status") in {"succeeded", "accepted"}:
                cached = call.get("result") or {}
                return (
                    CapabilityResult(
                        status=cached.get("status", call["status"]),
                        summary=cached.get("summary", "复用已完成的幂等能力调用"),
                        artifact_refs=list(cached.get("artifactRefs") or []),
                        quality=dict(cached.get("quality") or {}),
                        reason_code=cached.get("reasonCode", "IDEMPOTENT_REPLAY"),
                        suggested_next=list(cached.get("suggestedNext") or []),
                        usage=dict(cached.get("usage") or {}),
                        payload=dict(cached.get("payload") or {}),
                    ),
                    call,
                )
        result = capability.execute(context, arguments)
        record = {
            "stepId": context.step_id,
            "capability": capability_name,
            "capabilityVersion": capability.descriptor.version,
            "idempotencyKey": key,
            "status": result.status,
            "argumentsHash": normalized_arguments_hash(arguments),
            "result": {
                "status": result.status,
                "summary": result.summary,
                "artifactRefs": list(result.artifact_refs),
                "quality": dict(result.quality),
                "reasonCode": result.reason_code,
                "suggestedNext": list(result.suggested_next),
                "usage": dict(result.usage),
                "payload": dict(result.payload),
            },
        }
        return result, record
