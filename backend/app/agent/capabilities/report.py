from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

from app.agent.capability_registry import (
    CapabilityContext,
    CapabilityDescriptor,
    CapabilityResult,
    FunctionCapability,
)
from app.agent.plan_schema import EffectLevel


def report_capabilities() -> list[FunctionCapability]:
    return [
        FunctionCapability(
            CapabilityDescriptor(
                name="report.compose_from_evidence",
                description="复用当前 Run 的已校验产物形成报告草稿，不重新查询数据库",
                effectLevel=EffectLevel.CREATE_DRAFT,
                requiredScopes=["report:draft"],
                costClass="artifact_transform",
            ),
            _compose,
        ),
        FunctionCapability(
            CapabilityDescriptor(
                name="report.start_fresh",
                description="按报告模板创建新的持久化异步分析任务",
                effectLevel=EffectLevel.LONG_RUNNING,
                requiredScopes=["report:draft"],
                costClass="long_running",
            ),
            _start_fresh,
        ),
        FunctionCapability(
            CapabilityDescriptor(
                name="report.publish",
                description="发布通过结构化校验和审核的报告版本",
                effectLevel=EffectLevel.GOVERNANCE,
                requiredScopes=["report:publish"],
                approvalPolicy="always",
                costClass="governance",
            ),
            _approval_only,
        ),
        FunctionCapability(
            CapabilityDescriptor(
                name="report.send",
                description="把已发布报告投递到已授权渠道和接收人",
                effectLevel=EffectLevel.EXTERNAL_EFFECT,
                requiredScopes=["report:send"],
                approvalPolicy="always",
                costClass="external_effect",
            ),
            _approval_only,
        ),
    ]


def _compose(context: CapabilityContext, arguments: dict) -> CapabilityResult:
    from app.reports.service import compose_report_from_evidence

    result = compose_report_from_evidence(
        run_id=context.run_id,
        owner_id=context.user_id,
        source_id=context.source_id,
        template_id=str(arguments.get("templateId") or "tpl-operations-weekly"),
        title=str(arguments.get("title") or "本次分析报告")[:200],
    )
    return CapabilityResult(
        status="succeeded",
        summary=f"已复用本次证据生成报告草稿《{result['title']}》。",
        artifact_refs=list(result.get("artifactRefs") or []),
        payload={"report": result},
        suggested_next=["review_report"],
    )


def _start_fresh(context: CapabilityContext, arguments: dict) -> CapabilityResult:
    from app.reports.service import create_report

    end = datetime.now()
    start = end - timedelta(days=max(1, min(int(arguments.get("days") or 7), 366)))
    payload = SimpleNamespace(
        templateId=str(arguments.get("templateId") or "tpl-operations-weekly"),
        sourceId=context.source_id,
        periodStart=start,
        periodEnd=end,
        title=arguments.get("title"),
        ownerId=context.user_id,
        prompt=str(arguments.get("prompt") or context.state.get("user_question") or ""),
        scope={"originRunId": context.run_id, "plannerStepId": context.step_id},
        emailEnabled=False,
    )
    result = create_report(payload)
    return CapabilityResult(
        status="accepted",
        summary=f"已创建异步报告任务《{result['title']}》。",
        payload={"reportId": result["id"], "runId": result.get("analysisRunId"), "status": result.get("status")},
        suggested_next=["notify_on_terminal"],
    )


def _approval_only(_context: CapabilityContext, _arguments: dict) -> CapabilityResult:
    return CapabilityResult(status="waiting_approval", summary="报告发布或发送需要显式确认", reason_code="APPROVAL_REQUIRED")
