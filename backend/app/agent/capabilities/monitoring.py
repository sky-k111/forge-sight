from __future__ import annotations

from datetime import datetime

from app.agent.capability_registry import (
    CapabilityContext,
    CapabilityDescriptor,
    CapabilityResult,
    FunctionCapability,
)
from app.agent.plan_schema import EffectLevel
from app.db.models import MonitoringCursor, MonitoringRule
from app.db.platform import PlatformSession


def monitoring_capabilities() -> list[FunctionCapability]:
    return [
        FunctionCapability(
            CapabilityDescriptor(
                name="monitoring.list_rules",
                description="列出当前数据源已启用的监控规则与最近状态",
                effectLevel=EffectLevel.READ_ONLY,
                requiredScopes=["monitoring:read"],
                costClass="metadata",
            ),
            _list_rules,
        ),
        FunctionCapability(
            CapabilityDescriptor(
                name="monitoring.scan",
                description="对当前数据源触发一次监控规则扫描并生成告警事件",
                effectLevel=EffectLevel.LONG_RUNNING,
                requiredScopes=["monitoring:scan"],
                timeoutSeconds=60,
                costClass="long_running",
            ),
            _scan,
        ),
    ]


def _list_rules(context: CapabilityContext, arguments: dict) -> CapabilityResult:
    with PlatformSession() as session:
        rows = (
            session.query(MonitoringRule)
            .filter(MonitoringRule.source_id == context.source_id, MonitoringRule.status == "published")
            .order_by(MonitoringRule.created_at)
            .limit(20)
            .all()
        )
    rules = [
        {
            "id": row.id,
            "name": row.name,
            "status": row.status,
            "scheduleSeconds": row.schedule_seconds,
        }
        for row in rows
    ]
    return CapabilityResult(
        status="succeeded",
        summary=f"当前数据源共有 {len(rules)} 条启用的监控规则。",
        payload={"rules": rules},
        suggested_next=["scan_monitoring"] if rules else [],
    )


def _scan(context: CapabilityContext, arguments: dict) -> CapabilityResult:
    from app.monitoring.worker import enqueue_rule_task, completed_windows_since

    with PlatformSession() as session:
        rules = (
            session.query(MonitoringRule)
            .filter(MonitoringRule.source_id == context.source_id, MonitoringRule.status == "published")
            .all()
        )
        enqueued = 0
        for rule in rules:
            cursor = session.query(MonitoringCursor).filter_by(rule_id=rule.id).first()
            for logical_period, start, end in completed_windows_since(
                rule,
                datetime.now(),
                cursor.watermark_at if cursor else None,
            ):
                enqueue_rule_task(
                    session,
                    rule,
                    logical_period=logical_period,
                    window_start=start,
                    window_end=end,
                    now=datetime.now(),
                )
                enqueued += 1
        session.commit()
    return CapabilityResult(
        status="accepted",
        summary=f"已触发 {enqueued} 个监控窗口扫描任务。",
        payload={"enqueuedWindows": enqueued},
        suggested_next=["notify_on_terminal"],
    )
