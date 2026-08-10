"""审计日志服务：写入与查询。任何敏感动作（执行 SQL、拦截、口径变更）都必须经过这里。"""

from app.db.models import RESULT_TONES, AuditLog
from app.db.platform import PlatformSession


def write_audit(
    action: str,
    resource: str = "",
    result: str = "允许",
    detail: str = "",
    user_name: str = "演示用户",
    role: str = "业务人员",
) -> None:
    with PlatformSession() as session:
        session.add(
            AuditLog(
                user_name=user_name,
                role=role,
                action=action[:255],
                resource=resource[:255],
                result=result,
                detail=detail,
            )
        )
        session.commit()


def list_audit_logs(result: str | None = None, keyword: str = "", limit: int = 100) -> list[dict]:
    """返回与前端 SecurityPage 对齐的行结构。"""
    with PlatformSession() as session:
        query = session.query(AuditLog).order_by(AuditLog.id.desc()).limit(limit)
        rows = query.all()

    items = []
    for row in rows:
        if result and row.result != result:
            continue
        if keyword and keyword not in (row.user_name + row.action + row.resource):
            continue
        items.append(
            {
                "time": row.created_at.strftime("%H:%M:%S"),
                "user": row.user_name,
                "role": row.role,
                "action": row.action,
                "resource": row.resource,
                "result": row.result,
                "tone": RESULT_TONES.get(row.result, "blue"),
            }
        )
    return items
