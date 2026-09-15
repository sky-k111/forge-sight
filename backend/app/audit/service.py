"""审计日志服务：写入与查询。任何敏感动作（执行 SQL、拦截、口径变更）都必须经过这里。"""

from datetime import datetime

from sqlalchemy import func, or_

from app.db.models import RESULT_TONES, AuditLog
from app.db.platform import PlatformSession


def write_audit(
    action: str,
    resource: str = "",
    result: str = "允许",
    detail: str = "",
    user_name: str = "演示用户",
    role: str = "业务人员",
    tenant_id: str = "tenant-default",
) -> None:
    with PlatformSession() as session:
        session.add(
            AuditLog(
                tenant_id=tenant_id,
                user_name=user_name,
                role=role,
                action=action[:255],
                resource=resource[:255],
                result=result,
                detail=detail,
            )
        )
        session.commit()


def _like_pattern(keyword: str) -> str:
    escaped = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def list_audit_logs(
    result: str | None = None,
    keyword: str = "",
    user: str | None = None,
    reviewed: str | None = None,
    limit: int = 200,
    tenant_id: str = "tenant-default",
) -> dict:
    """返回 {items, stats, users}：items 为筛选后的审计行，stats 为全量真实统计。

    reviewed 取值：待复核（已拦截且未复核）/ 已复核（已拦截且已复核）。
    """
    with PlatformSession() as session:
        scoped = session.query(AuditLog).filter(AuditLog.tenant_id == tenant_id)
        counts = dict(
            scoped.with_entities(AuditLog.result, func.count(AuditLog.id)).group_by(AuditLog.result).all()
        )
        unreviewed = (
            scoped.filter(AuditLog.result == "已拦截", AuditLog.reviewed_at.is_(None)).count()
        )
        users = [
            name
            for (name,) in (
                session.query(AuditLog.user_name)
                .filter(AuditLog.tenant_id == tenant_id)
                .distinct()
                .order_by(AuditLog.user_name)
                .all()
            )
        ]

        query = scoped
        if result:
            query = query.filter(AuditLog.result == result)
        if user:
            query = query.filter(AuditLog.user_name == user)
        if reviewed == "待复核":
            query = query.filter(AuditLog.result == "已拦截", AuditLog.reviewed_at.is_(None))
        elif reviewed == "已复核":
            query = query.filter(AuditLog.result == "已拦截", AuditLog.reviewed_at.isnot(None))
        if keyword:
            pattern = _like_pattern(keyword.strip())
            query = query.filter(
                or_(
                    AuditLog.action.like(pattern, escape="\\"),
                    AuditLog.resource.like(pattern, escape="\\"),
                    AuditLog.detail.like(pattern, escape="\\"),
                    AuditLog.user_name.like(pattern, escape="\\"),
                )
            )
        rows = query.order_by(AuditLog.id.desc()).limit(limit).all()

    items = [
        {
            "id": row.id,
            "time": row.created_at.strftime("%m-%d %H:%M"),
            "at": row.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            "user": row.user_name,
            "role": row.role,
            "action": row.action,
            "resource": row.resource,
            "result": row.result,
            "tone": RESULT_TONES.get(row.result, "blue"),
            "detail": (row.detail or "").strip(),
            "reviewedAt": row.reviewed_at.strftime("%m-%d %H:%M") if row.reviewed_at else "",
            "reviewedBy": row.reviewed_by or "",
        }
        for row in rows
    ]
    return {
        "items": items,
        "stats": {
            "total": sum(counts.values()),
            "allowed": counts.get("允许", 0),
            "blocked": counts.get("已拦截", 0),
            "recorded": counts.get("已记录", 0),
            "unreviewed": unreviewed,
        },
        "users": users,
    }


def review_audit_logs_batch(log_ids: list[int], tenant_id: str, reviewer: str) -> dict:
    """批量复核：只处理属于当前租户、已拦截且未复核的记录；返回复核数量。"""
    ids = list(dict.fromkeys(int(item) for item in log_ids if int(item) > 0))[:200]
    if not ids:
        return {"reviewed": 0, "requested": 0}
    with PlatformSession() as session:
        rows = (
            session.query(AuditLog)
            .filter(
                AuditLog.id.in_(ids),
                AuditLog.tenant_id == tenant_id,
                AuditLog.result == "已拦截",
                AuditLog.reviewed_at.is_(None),
            )
            .all()
        )
        now = datetime.now()
        for row in rows:
            row.reviewed_at = now
            row.reviewed_by = reviewer[:50]
        session.commit()
        return {"reviewed": len(rows), "requested": len(ids)}


def review_audit_log(log_id: int, tenant_id: str, reviewer: str) -> dict | None:
    """复核一条拦截记录：写入复核人与时间；记录不存在或不属于当前租户返回 None。"""
    with PlatformSession() as session:
        row = (
            session.query(AuditLog)
            .filter(AuditLog.id == log_id, AuditLog.tenant_id == tenant_id)
            .first()
        )
        if row is None or row.result != "已拦截":
            return None
        row.reviewed_at = datetime.now()
        row.reviewed_by = reviewer[:50]
        session.commit()
        return {
            "id": row.id,
            "reviewedAt": row.reviewed_at.strftime("%m-%d %H:%M"),
            "reviewedBy": row.reviewed_by,
        }
