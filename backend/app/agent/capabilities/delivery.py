from __future__ import annotations

from app.agent.capability_registry import (
    CapabilityContext,
    CapabilityDescriptor,
    CapabilityResult,
    FunctionCapability,
)
from app.agent.plan_schema import EffectLevel


def delivery_capabilities() -> list[FunctionCapability]:
    return [
        FunctionCapability(
            CapabilityDescriptor(
                name="delivery.push",
                description="把已发布的报告/消息投递到飞书、钉钉或邮箱渠道（需显式确认）",
                effectLevel=EffectLevel.EXTERNAL_EFFECT,
                requiredScopes=["delivery:send"],
                approvalPolicy="always",
                timeoutSeconds=30,
                costClass="external_effect",
            ),
            _push,
        ),
    ]


def _push(context: CapabilityContext, arguments: dict) -> CapabilityResult:
    channel = str(arguments.get("channel") or "feishu")
    report_id = str(arguments.get("reportId") or "")
    recipient = str(arguments.get("recipientUserId") or context.user_id)
    if channel not in {"feishu", "dingtalk", "email"}:
        return CapabilityResult(
            status="failed",
            summary="投递渠道仅支持飞书、钉钉或邮件。",
            reason_code="DELIVERY_CHANNEL_INVALID",
        )
    if not report_id:
        return CapabilityResult(
            status="waiting_approval",
            summary="投递需要先确认报告版本与接收人。",
            reason_code="DELIVERY_CONTEXT_MISSING",
            suggested_next=["compose_report", "publish_report"],
        )
    from app.db.platform import PlatformSession

    with PlatformSession() as session:
        try:
            if channel in {"feishu", "dingtalk"}:
                from app.delivery.service import enqueue_report_pdf_delivery

                delivery = enqueue_report_pdf_delivery(session, report_id, recipient, channel)
            else:
                session.rollback()
                return CapabilityResult(
                    status="failed",
                    summary="邮件渠道尚未启用，当前仅支持飞书与钉钉。",
                    reason_code="DELIVERY_CHANNEL_NOT_ENABLED",
                )
            session.commit()
            return CapabilityResult(
                status="accepted",
                summary=f"已创建 {channel} 投递任务，后台将异步发送。",
                reason_code="OK",
                payload={"deliveryId": str(getattr(delivery, "id", "")), "channel": channel},
                suggested_next=["notify_on_terminal"],
            )
        except Exception as exc:  # noqa: BLE001 - 对外仅记录，不暴露内部细节
            session.rollback()
            return CapabilityResult(
                status="failed",
                summary="无法创建投递任务：请确认报告已发布且接收人存在。",
                reason_code="DELIVERY_ENQUEUE_FAILED",
            )
