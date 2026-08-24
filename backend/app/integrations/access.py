"""外部 IM 入口共用的用户与数据源授权边界。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import or_

from app.db.models import DataScopePolicy, DataSource, PlatformUser


def resolve_business_user(session, user_reference: str) -> PlatformUser | None:
    """同时兼容不可变 user id 与旧版登录名配置，并返回规范用户对象。"""

    return (
        session.query(PlatformUser)
        .filter(
            or_(
                PlatformUser.id == user_reference,
                PlatformUser.username == user_reference,
            )
        )
        .first()
    )


def allowed_source_ids(
    session,
    user_id: str,
    now: datetime | None = None,
) -> set[int]:
    """合并分析相关显式授权；deny 始终覆盖 allow。"""

    now = now or datetime.now()
    policies = (
        session.query(DataScopePolicy)
        .filter(
            DataScopePolicy.subject_type == "user",
            DataScopePolicy.subject_id == user_id,
            DataScopePolicy.business_key.in_(["analysis", "weekly_report"]),
            DataScopePolicy.enabled.is_(True),
            or_(DataScopePolicy.effective_from.is_(None), DataScopePolicy.effective_from <= now),
            or_(DataScopePolicy.effective_to.is_(None), DataScopePolicy.effective_to > now),
        )
        .all()
    )
    allowed: set[int] = set()
    denied: set[int] = set()
    for policy in policies:
        source_ids = {
            int(item)
            for item in (policy.data_source_ids_json or [])
            if str(item).isdigit()
        }
        if policy.effect == "deny":
            denied.update(source_ids)
        elif policy.effect == "allow":
            allowed.update(source_ids)
    return allowed - denied


def source_exists(session, source_id: int) -> bool:
    return session.get(DataSource, source_id) is not None
