"""预测编排能力：命中可用模型 → 批量推理 → 结果卡；无模型时引导建模提案。"""

from __future__ import annotations

from app.agent.capability_registry import (
    CapabilityContext,
    CapabilityDescriptor,
    CapabilityResult,
    FunctionCapability,
)
from app.agent.plan_schema import EffectLevel


def prediction_capabilities() -> list[FunctionCapability]:
    return [
        FunctionCapability(
            CapabilityDescriptor(
                name="prediction.run",
                description="对已发布或本人个人档模型执行批量预测，返回业务可读结果卡",
                effectLevel=EffectLevel.READ_ONLY,
                requiredScopes=["modeling:predict"],
                costClass="model_inference",
            ),
            _run,
        ),
    ]


def _run(context: CapabilityContext, arguments: dict) -> CapabilityResult:
    from app.modeling.service import ModelNotFoundError, TaskNotTrainedError, find_reusable_models, run_prediction

    question = str(arguments.get("question") or context.state.get("user_question") or "")
    task_type = str(arguments.get("taskType") or "")
    matches = find_reusable_models(question, context.source_id, task_type, viewer_id=context.user_id)
    if not matches:
        return CapabilityResult(
            status="failed",
            summary="没有找到可用的预测模型，建议先建立该预测能力。",
            reason_code="NO_REUSABLE_MODEL",
            suggested_next=["propose_modeling"],
        )
    best = matches[0]
    try:
        if best.get("executionType") == "mcp":
            from app.modeling.remote import context_for_user, invoke
            card = invoke(best["modelId"], context_for_user(context.user_id, context.source_id), source_id=context.source_id)
        else:
            card = run_prediction(best["taskId"], context.user_id)
    except (ModelNotFoundError, TaskNotTrainedError) as exc:
        return CapabilityResult(
            status="failed",
            summary=f"预测未能执行：{exc}",
            reason_code="PREDICTION_UNAVAILABLE",
            suggested_next=["propose_modeling"],
        )
    except Exception as exc:  # noqa: BLE001 - 对外只给业务可读信息
        return CapabilityResult(
            status="failed",
            summary="预测执行失败，请稍后重试或检查数据源状态。",
            reason_code="PREDICTION_FAILED",
        )
    return CapabilityResult(
        status="succeeded",
        summary=card["headline"],
        reason_code="OK",
        payload={"resultCard": card, "matchScore": best.get("matchScore")},
    )
