from __future__ import annotations

from app.agent.capability_registry import (
    CapabilityContext,
    CapabilityDescriptor,
    CapabilityResult,
    FunctionCapability,
)
from app.agent.plan_schema import EffectLevel


def investigation_capabilities() -> list[FunctionCapability]:
    return [
        FunctionCapability(
            CapabilityDescriptor(
                name="investigation.continue",
                description="基于已有检查点调整时间、维度或对比条件并建立调查分支",
                effectLevel=EffectLevel.CREATE_DRAFT,
                requiredScopes=["analysis:read"],
                costClass="analysis_branch",
            ),
            _continue,
        ),
        FunctionCapability(
            CapabilityDescriptor(
                name="investigation.create_action",
                description="把调查结论转为带负责人和截止时间的行动项",
                effectLevel=EffectLevel.EXTERNAL_EFFECT,
                requiredScopes=["investigation:write"],
                approvalPolicy="always",
                costClass="external_effect",
            ),
            _approval_only,
        ),
    ]


def _continue(context: CapabilityContext, arguments: dict) -> CapabilityResult:
    checkpoint_id = str(arguments.get("checkpointId") or "")
    if not checkpoint_id:
        return CapabilityResult(
            status="failed",
            summary="继续调查需要明确的证据链检查点。",
            reason_code="CHECKPOINT_REQUIRED",
            suggested_next=["select_checkpoint"],
        )
    return CapabilityResult(
        status="accepted",
        summary="已形成继续调查的分支草案，数据源和指标口径保持继承。",
        payload={"checkpointId": checkpoint_id, "adjustments": arguments.get("adjustments") or {}, "requiresBranchCreation": True},
    )


def _approval_only(_context: CapabilityContext, _arguments: dict) -> CapabilityResult:
    return CapabilityResult(status="waiting_approval", summary="行动项需要确认负责人、内容和截止时间", reason_code="APPROVAL_REQUIRED")
