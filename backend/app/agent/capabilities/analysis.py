from __future__ import annotations

from app.agent.capability_registry import (
    CapabilityContext,
    CapabilityDescriptor,
    CapabilityResult,
    FunctionCapability,
)
from app.agent.plan_schema import EffectLevel
from app.agent.progress import result_quality_from_task_results


def analysis_capabilities() -> list[FunctionCapability]:
    return [
        FunctionCapability(
            CapabilityDescriptor(
                name="analysis.inspect_context",
                description="检索指标、口径、Schema、字段和业务规则",
                effectLevel=EffectLevel.READ_ONLY,
                requiredScopes=["analysis:read"],
                costClass="retrieval",
                delegated=True,
            ),
            _delegated,
        ),
        FunctionCapability(
            CapabilityDescriptor(
                name="analysis.query_data",
                description="通过不可绕过的 SQL 安全子图完成一次只读业务分析",
                effectLevel=EffectLevel.READ_ONLY,
                requiredScopes=["analysis:read"],
                timeoutSeconds=120,
                costClass="database_and_llm",
                delegated=True,
            ),
            _delegated,
        ),
        FunctionCapability(
            CapabilityDescriptor(
                name="analysis.inspect_result_quality",
                description="检查查询结果的样本量、覆盖度和限制",
                effectLevel=EffectLevel.PURE,
                requiredScopes=["analysis:read"],
                costClass="pure",
            ),
            _quality,
        ),
        FunctionCapability(
            CapabilityDescriptor(
                name="analysis.build_visualization",
                description="根据已校验结果生成图表规格",
                effectLevel=EffectLevel.PURE,
                requiredScopes=["analysis:read"],
                costClass="pure",
            ),
            _visualization,
        ),
    ]


def _delegated(_context: CapabilityContext, _arguments: dict) -> CapabilityResult:
    return CapabilityResult(status="failed", summary="该能力必须由受治理 LangGraph 子图执行", reason_code="DELEGATED_CAPABILITY")


def _quality(context: CapabilityContext, _arguments: dict) -> CapabilityResult:
    quality = result_quality_from_task_results(context.state.get("task_results") or [])
    limitations = quality.get("limitations") or []
    summary = (
        f"结果共返回 {quality.get('sampleCount', 0)} 条记录，覆盖检查通过。"
        if not limitations
        else "结果质量检查完成，但存在限制：" + "；".join(limitations)
    )
    return CapabilityResult(
        status="succeeded",
        summary=summary,
        quality=quality,
        reason_code="OK" if not limitations else "RESULT_HAS_LIMITATIONS",
        suggested_next=["compose_report"] if not limitations else ["return_with_limitations"],
        payload={"quality": quality},
    )


def _visualization(context: CapabilityContext, arguments: dict) -> CapabilityResult:
    task_results = context.state.get("task_results") or []
    latest = task_results[-1] if task_results else {}
    execution = latest.get("execution") or {}
    columns = execution.get("columns") or []
    rows = execution.get("rows") or []
    requested = str(arguments.get("chartType") or "auto")
    chart_type = requested if requested != "auto" else ("line" if columns and any("date" in str(item).lower() or "time" in str(item).lower() for item in columns) else "bar")
    payload = {"chartType": chart_type, "columns": columns, "rowCount": len(rows)}
    return CapabilityResult(status="succeeded", summary=f"已生成 {chart_type} 图表规格。", payload=payload)
