"""外部 IM 入口共用的用户与数据源授权边界。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import or_

from app.db.models import DataScopePolicy, DataSource, PlatformUser, TenantMembership


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
    tenant_ids = {
        tenant_id for (tenant_id,) in session.query(TenantMembership.tenant_id).filter_by(
            user_id=user_id, enabled=True
        ).all()
    }
    if not tenant_ids:
        tenant_ids = {"tenant-default"}
    policies = (
        session.query(DataScopePolicy)
        .filter(
            DataScopePolicy.subject_type == "user",
            DataScopePolicy.subject_id == user_id,
            DataScopePolicy.tenant_id.in_(tenant_ids),
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
    tenant_sources = {
        int(source_id) for (source_id,) in session.query(DataSource.id).filter(
            DataSource.tenant_id.in_(tenant_ids)
        ).all()
    }
    return (allowed - denied) & tenant_sources


def source_exists(session, source_id: int) -> bool:
    return session.get(DataSource, source_id) is not None


def tenant_id_for_source(session, source_id: int) -> str:
    tenant_id = session.query(DataSource.tenant_id).filter_by(id=source_id).scalar()
    if not tenant_id:
        raise KeyError("数据源不存在")
    return str(tenant_id)
