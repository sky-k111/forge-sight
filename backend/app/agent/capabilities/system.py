from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.agent.capability_registry import (
    CapabilityContext,
    CapabilityDescriptor,
    CapabilityResult,
    FunctionCapability,
)
from app.agent.plan_schema import EffectLevel
from app.db.models import AnalysisRun, DataSource
from app.db.platform import PlatformSession


def system_capabilities() -> list[FunctionCapability]:
    return [
        FunctionCapability(
            CapabilityDescriptor(
                name="system.get_time",
                description="读取 Asia/Shanghai 当前日期、时间、星期和月份边界",
                effectLevel=EffectLevel.PURE,
                requiredScopes=[],
                costClass="pure",
            ),
            _get_time,
        ),
        FunctionCapability(
            CapabilityDescriptor(
                name="system.describe_capabilities",
                description="说明 ForgeSight 当前支持的业务级能力",
                effectLevel=EffectLevel.PURE,
                requiredScopes=[],
                costClass="pure",
            ),
            _describe,
        ),
        FunctionCapability(
            CapabilityDescriptor(
                name="scope.get_current",
                description="读取当前已冻结账号、租户、角色与数据源范围",
                effectLevel=EffectLevel.READ_ONLY,
                requiredScopes=["scope:read"],
                costClass="metadata",
            ),
            _scope,
        ),
        FunctionCapability(
            CapabilityDescriptor(
                name="task.get_status",
                description="查询当前分析、报告或建模任务状态",
                effectLevel=EffectLevel.READ_ONLY,
                requiredScopes=["task:read"],
                costClass="metadata",
            ),
            _task_status,
        ),
    ]


def _get_time(_context: CapabilityContext, _arguments: dict) -> CapabilityResult:
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    payload = {
        "timezone": "Asia/Shanghai",
        "now": now.isoformat(),
        "date": now.date().isoformat(),
        "weekday": now.isoweekday(),
        "monthStart": month_start.isoformat(),
    }
    return CapabilityResult(status="succeeded", summary=f"当前时间为 {now:%Y-%m-%d %H:%M:%S}（Asia/Shanghai）", payload=payload)


def _describe(_context: CapabilityContext, _arguments: dict) -> CapabilityResult:
    return CapabilityResult(
        status="succeeded",
        summary="支持制造业只读问析、结果质量检查、调查延续、报告草稿、已发布模型复用与受确认建模。",
        payload={
            "domains": ["生产", "质量", "设备", "库存"],
            "databases": ["MySQL", "MariaDB", "PostgreSQL", "SQL Server", "Oracle"],
            "safety": ["只读 SQL", "表白名单", "方言校验", "成本门控", "有限重试"],
        },
    )


def _scope(context: CapabilityContext, _arguments: dict) -> CapabilityResult:
    with PlatformSession() as session:
        sources = session.query(DataSource).filter(DataSource.id.in_(context.source_ids)).all()
    payload = {
        "tenantId": context.tenant_id,
        "userId": context.user_id,
        "role": context.role,
        "sources": [{"id": row.id, "name": row.name, "type": row.type, "status": row.status} for row in sources],
        "capabilityScopes": sorted(context.capability_scopes),
    }
    return CapabilityResult(status="succeeded", summary=f"当前冻结了 {len(sources)} 个授权数据源，所有查询保持只读。", payload=payload)


def _task_status(context: CapabilityContext, arguments: dict) -> CapabilityResult:
    run_id = str(arguments.get("runId") or context.run_id)
    with PlatformSession() as session:
        row = session.query(AnalysisRun).filter_by(id=run_id).first()
    if row is None:
        return CapabilityResult(status="failed", summary="没有找到指定任务", reason_code="TASK_NOT_FOUND")
    return CapabilityResult(
        status="succeeded",
        summary=f"任务当前状态：{row.status}",
        payload={"runId": row.id, "status": row.status, "currentStepKey": row.current_step_key, "errorCode": row.error_code},
    )
