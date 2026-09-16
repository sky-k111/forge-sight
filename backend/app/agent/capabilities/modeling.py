from __future__ import annotations

from app.agent.capability_registry import (
    CapabilityContext,
    CapabilityDescriptor,
    CapabilityResult,
    FunctionCapability,
)
from app.agent.plan_schema import EffectLevel


def modeling_capabilities() -> list[FunctionCapability]:
    return [
        FunctionCapability(
            CapabilityDescriptor(
                name="modeling.find_reusable",
                description="查找同数据源、任务类型和语义相符的已发布模型",
                effectLevel=EffectLevel.READ_ONLY,
                requiredScopes=["modeling:read"],
                costClass="metadata",
            ),
            _find_reusable,
        ),
        FunctionCapability(
            CapabilityDescriptor(
                name="modeling.propose",
                description="形成建模目标、特征、候选算法和白名单取数 SQL 草案",
                effectLevel=EffectLevel.CREATE_DRAFT,
                requiredScopes=["modeling:draft"],
                costClass="database_and_llm",
                delegated=True,
            ),
            _delegated,
        ),
        FunctionCapability(
            CapabilityDescriptor(
                name="modeling.predict",
                description="使用同租户同数据源且已发布的模型执行推理",
                effectLevel=EffectLevel.READ_ONLY,
                requiredScopes=["modeling:predict"],
                costClass="model_inference",
            ),
            _predict,
        ),
        FunctionCapability(
            CapabilityDescriptor(
                name="modeling.start_training",
                description="根据已确认建模草案启动异步训练",
                effectLevel=EffectLevel.LONG_RUNNING,
                requiredScopes=["modeling:train"],
                approvalPolicy="always",
                timeoutSeconds=30,
                costClass="long_running",
            ),
            _approval_only,
        ),
    ]


def _find_reusable(context: CapabilityContext, arguments: dict) -> CapabilityResult:
    from app.modeling.service import find_reusable_models

    question = str(arguments.get("question") or context.state.get("user_question") or "")
    task_type = str(arguments.get("taskType") or "")
    models = find_reusable_models(question, context.source_id, task_type, viewer_id=context.user_id)
    return CapabilityResult(
        status="succeeded",
        summary=(f"找到 {len(models)} 个可复用的已发布模型。" if models else "没有找到契约匹配的已发布模型。"),
        reason_code="OK" if models else "NO_REUSABLE_MODEL",
        suggested_next=["predict"] if models else ["propose_modeling"],
        payload={"models": models},
    )


def _delegated(_context: CapabilityContext, _arguments: dict) -> CapabilityResult:
    return CapabilityResult(status="failed", summary="建模提案必须经过语义检索与取数 SQL 校验子图", reason_code="DELEGATED_CAPABILITY")


def _predict(context: CapabilityContext, arguments: dict) -> CapabilityResult:
    from app.modeling.service import predict

    task_id = str(arguments.get("taskId") or "")
    records = list(arguments.get("records") or [])
    if not task_id or not records:
        return CapabilityResult(status="failed", summary="模型推理缺少 taskId 或 records", reason_code="PREDICTION_ARGUMENTS_MISSING")
    result = predict(task_id, records, context.user_id)
    return CapabilityResult(status="succeeded", summary=f"已完成 {len(records)} 条记录的模型推理。", payload=result)


def _approval_only(_context: CapabilityContext, _arguments: dict) -> CapabilityResult:
    return CapabilityResult(status="waiting_approval", summary="模型训练需要用户确认", reason_code="APPROVAL_REQUIRED")
