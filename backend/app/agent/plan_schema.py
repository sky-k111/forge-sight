"""Stable contracts for ForgeSight's bounded business planner.

The planner may choose business capabilities, but it never receives raw SQL,
credentials, or infrastructure tools.  These Pydantic models are intentionally
strict because a model-produced plan is untrusted input until validation.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EffectLevel(StrEnum):
    PURE = "pure"
    READ_ONLY = "read_only"
    CREATE_DRAFT = "create_draft"
    LONG_RUNNING = "long_running"
    EXTERNAL_EFFECT = "external_effect"
    GOVERNANCE = "governance"


class StepFailurePolicy(StrEnum):
    CLARIFY_OR_STOP = "clarify_or_stop"
    STOP_WITH_LIMITATIONS = "stop_with_limitations"
    RETURN_PARTIAL = "return_partial"
    RETURN_ANALYSIS_ONLY = "return_analysis_only"
    REPLAN_ONCE = "replan_once"


class PlanBudget(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    max_capability_calls: int = Field(default=8, alias="maxCapabilityCalls", ge=1, le=20)
    max_database_executions: int = Field(default=3, alias="maxDatabaseExecutions", ge=0, le=8)
    max_replans: int = Field(default=1, alias="maxReplans", ge=0, le=2)
    max_async_jobs: int = Field(default=1, alias="maxAsyncJobs", ge=0, le=3)


class PlanStep(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str = Field(min_length=1, max_length=64)
    capability: str = Field(pattern=r"^[a-z][a-z0-9_.-]{2,80}$")
    purpose: str = Field(min_length=1, max_length=300)
    arguments: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list, alias="dependsOn", max_length=12)
    success_criteria: list[str] = Field(default_factory=list, alias="successCriteria", max_length=12)
    on_failure: StepFailurePolicy = Field(
        default=StepFailurePolicy.STOP_WITH_LIMITATIONS,
        alias="onFailure",
    )


class AgentPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    goal: str = Field(min_length=1, max_length=1000)
    assumptions: list[str] = Field(default_factory=list, max_length=12)
    steps: list[PlanStep] = Field(min_length=1, max_length=20)
    stop_conditions: list[str] = Field(default_factory=list, alias="stopConditions", max_length=12)
    budget: PlanBudget = Field(default_factory=PlanBudget)

    @model_validator(mode="after")
    def validate_graph(self):
        ids = [step.id for step in self.steps]
        if len(ids) != len(set(ids)):
            raise ValueError("计划步骤 ID 必须唯一")
        known: set[str] = set()
        for step in self.steps:
            missing = set(step.depends_on) - set(ids)
            if missing:
                raise ValueError(f"步骤 {step.id} 引用了不存在的依赖：{sorted(missing)}")
            if step.id in step.depends_on:
                raise ValueError(f"步骤 {step.id} 不能依赖自身")
            # Plans are emitted in execution order. Requiring dependencies to
            # point backwards gives us a deterministic DAG check without a
            # second graph library or model-controlled traversal.
            forward = set(step.depends_on) - known
            if forward:
                raise ValueError(f"步骤 {step.id} 存在前向或循环依赖：{sorted(forward)}")
            known.add(step.id)
        return self


class ObservationQuality(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    coverage: float | None = Field(default=None, ge=0, le=1)
    freshness: str | None = None
    sample_count: int | None = Field(default=None, alias="sampleCount", ge=0)
    limitations: list[str] = Field(default_factory=list)


class CapabilityObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    step_id: str = Field(alias="stepId")
    capability: str
    status: str = Field(pattern=r"^(succeeded|failed|accepted|waiting_approval|skipped)$")
    summary: str = Field(default="", max_length=2000)
    artifact_refs: list[str] = Field(default_factory=list, alias="artifactRefs")
    quality: ObservationQuality = Field(default_factory=ObservationQuality)
    reason_code: str = Field(default="OK", alias="reasonCode", max_length=80)
    suggested_next: list[str] = Field(default_factory=list, alias="suggestedNext")
    usage: dict[str, int | float] = Field(default_factory=dict)
    payload: dict[str, Any] = Field(default_factory=dict)


class PlanValidationResult(BaseModel):
    valid: bool
    checks: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    degraded: bool = False
    reason_code: str = Field(default="OK", alias="reasonCode")


def dump_contract(model: BaseModel) -> dict[str, Any]:
    """Serialize all public planner contracts in the frontend's camelCase form."""

    return model.model_dump(by_alias=True, mode="json")
