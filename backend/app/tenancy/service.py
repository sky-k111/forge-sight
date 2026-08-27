"""Tenant provisioning, membership validation and source-bound isolation."""

from __future__ import annotations

import re
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException
from sqlalchemy import func

from app.agent.ids import new_id
from app.auth.deps import optional_user
from app.db.models import (
    AnalysisCheckpoint,
    AnalysisInteraction,
    AnalysisRun,
    AnalysisSession,
    DataSource,
    PlatformUser,
    Tenant,
    TenantMembership,
)
from app.db.platform import PlatformSession

DEFAULT_TENANT_ID = "tenant-default"
TENANT_HEADER = "X-Tenant-ID"
ADMIN_ROLES = {"owner", "admin"}


@dataclass(frozen=True)
class TenantContext:
    tenant_id: str
    tenant_name: str
    tenant_slug: str
    member_role: str
    user_id: str | None

    @property
    def can_administer(self) -> bool:
        return self.member_role in ADMIN_ROLES


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return normalized[:60] or "enterprise"


def seed_default_tenant() -> Tenant:
    """Create the compatibility tenant before legacy rows are backfilled."""

    with PlatformSession() as session:
        tenant = session.get(Tenant, DEFAULT_TENANT_ID)
        if tenant is None:
            tenant = Tenant(
                id=DEFAULT_TENANT_ID,
                slug="forgesight-demo",
                name="ForgeSight 演示企业",
                status="active",
            )
            session.add(tenant)
            session.commit()
            session.refresh(tenant)
        return tenant


def seed_legacy_memberships() -> None:
    """Attach pre-migration accounts and sources to the compatibility tenant."""

    with PlatformSession() as session:
        tenant = session.get(Tenant, DEFAULT_TENANT_ID)
        if tenant is None:
            tenant = Tenant(
                id=DEFAULT_TENANT_ID,
                slug="forgesight-demo",
                name="ForgeSight 演示企业",
            )
            session.add(tenant)
            session.flush()
        session.query(DataSource).filter(
            (DataSource.tenant_id.is_(None)) | (DataSource.tenant_id == "")
        ).update({DataSource.tenant_id: DEFAULT_TENANT_ID}, synchronize_session=False)
        for user in session.query(PlatformUser).all():
            exists = session.query(TenantMembership.id).filter_by(
                tenant_id=DEFAULT_TENANT_ID,
                user_id=user.id,
            ).first()
            if exists:
                continue
            session.add(
                TenantMembership(
                    id=new_id("tm"),
                    tenant_id=DEFAULT_TENANT_ID,
                    user_id=user.id,
                    member_role="owner" if user.role == "admin" else "member",
                    is_default=True,
                )
            )
        session.commit()


def provision_private_tenant(session, user: PlatformUser, display_name: str = "") -> TenantMembership:
    """Give a newly registered account an isolated workspace without sharing legacy data."""

    base_slug = _slug(user.username)
    slug = base_slug
    suffix = 1
    while session.query(Tenant.id).filter_by(slug=slug).first():
        suffix += 1
        slug = f"{base_slug}-{suffix}"
    tenant = Tenant(
        id=new_id("tnt"),
        slug=slug,
        name=(display_name.strip() or user.display_name or user.username)[:140] + "的企业空间",
    )
    membership = TenantMembership(
        id=new_id("tm"),
        tenant_id=tenant.id,
        user_id=user.id,
        member_role="owner",
        is_default=True,
    )
    session.add_all([tenant, membership])
    return membership


def list_user_tenants(user_id: str) -> list[dict]:
    with PlatformSession() as session:
        rows = (
            session.query(TenantMembership, Tenant)
            .join(Tenant, Tenant.id == TenantMembership.tenant_id)
            .filter(
                TenantMembership.user_id == user_id,
                TenantMembership.enabled.is_(True),
                Tenant.status == "active",
            )
            .order_by(TenantMembership.is_default.desc(), Tenant.name)
            .all()
        )
        return [
            {
                "id": tenant.id,
                "slug": tenant.slug,
                "name": tenant.name,
                "role": membership.member_role,
                "isDefault": membership.is_default,
            }
            for membership, tenant in rows
        ]


def _legacy_default_context(session, user: dict | None) -> TenantContext:
    tenant = session.get(Tenant, DEFAULT_TENANT_ID)
    if tenant is None:
        tenant = Tenant(
            id=DEFAULT_TENANT_ID,
            slug="forgesight-demo",
            name="ForgeSight 演示企业",
        )
        session.add(tenant)
        session.commit()
    return TenantContext(
        tenant_id=tenant.id,
        tenant_name=tenant.name,
        tenant_slug=tenant.slug,
        member_role="owner" if user and user.get("role") == "admin" else "member",
        user_id=user.get("id") if user else None,
    )


def resolve_tenant_context(user: dict | None, requested_tenant_id: str | None = None) -> TenantContext:
    requested = str(requested_tenant_id or "").strip() or None
    if user is None:
        if requested and requested != DEFAULT_TENANT_ID:
            raise HTTPException(status_code=403, detail="演示模式不能访问其他企业空间")
        return TenantContext(
            tenant_id=DEFAULT_TENANT_ID,
            tenant_name="ForgeSight 演示企业",
            tenant_slug="forgesight-demo",
            member_role="owner",
            user_id=None,
        )
    with PlatformSession() as session:
        memberships = (
            session.query(TenantMembership, Tenant)
            .join(Tenant, Tenant.id == TenantMembership.tenant_id)
            .filter(
                TenantMembership.user_id == user["id"],
                TenantMembership.enabled.is_(True),
                Tenant.status == "active",
            )
            .all()
        )
        # Migration compatibility only: startup backfill normally makes this path unreachable.
        if not memberships:
            if requested and requested != DEFAULT_TENANT_ID:
                raise HTTPException(status_code=403, detail="当前账号不属于该企业空间")
            return _legacy_default_context(session, user)
        selected = None
        if requested:
            selected = next((row for row in memberships if row[1].id == requested), None)
            if selected is None:
                raise HTTPException(status_code=403, detail="当前账号不属于该企业空间")
        else:
            selected = next((row for row in memberships if row[0].is_default), memberships[0])
        membership, tenant = selected
        return TenantContext(
            tenant_id=tenant.id,
            tenant_name=tenant.name,
            tenant_slug=tenant.slug,
            member_role=membership.member_role,
            user_id=user["id"],
        )


def tenant_context(
    x_tenant_id: str | None = Header(default=None, alias=TENANT_HEADER),
    user: dict | None = Depends(optional_user),
) -> TenantContext:
    return resolve_tenant_context(user, x_tenant_id)


def require_tenant_admin(context: TenantContext = Depends(tenant_context)) -> TenantContext:
    if not context.can_administer:
        raise HTTPException(status_code=403, detail="需要当前企业的管理员权限")
    return context


def tenant_source_ids(session, tenant_id: str) -> set[int]:
    return {
        int(source_id)
        for (source_id,) in session.query(DataSource.id).filter_by(tenant_id=tenant_id).all()
    }


def ensure_source_access(session, source_id: int, tenant_id: str) -> DataSource:
    source = session.query(DataSource).filter_by(id=source_id, tenant_id=tenant_id).first()
    if source is None:
        # Do not reveal whether the source exists in another tenant.
        raise HTTPException(status_code=404, detail="数据源不存在或不属于当前企业")
    return source


def ensure_analysis_session_access(session, session_id: str, tenant_id: str) -> AnalysisSession:
    row = session.query(AnalysisSession).filter_by(id=session_id, tenant_id=tenant_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="分析会话不存在或不属于当前企业")
    return row


def ensure_analysis_run_access(session, run_id: str, tenant_id: str) -> AnalysisRun:
    row = (
        session.query(AnalysisRun)
        .join(AnalysisSession, AnalysisSession.id == AnalysisRun.session_id)
        .filter(AnalysisRun.id == run_id, AnalysisSession.tenant_id == tenant_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="分析运行不存在或不属于当前企业")
    return row


def ensure_checkpoint_access(session, checkpoint_id: str, tenant_id: str) -> AnalysisCheckpoint:
    row = (
        session.query(AnalysisCheckpoint)
        .join(AnalysisRun, AnalysisRun.id == AnalysisCheckpoint.run_id)
        .join(AnalysisSession, AnalysisSession.id == AnalysisRun.session_id)
        .filter(AnalysisCheckpoint.id == checkpoint_id, AnalysisSession.tenant_id == tenant_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="分析检查点不存在或不属于当前企业")
    return row


def ensure_interaction_access(session, interaction_id: str, tenant_id: str) -> AnalysisInteraction:
    row = (
        session.query(AnalysisInteraction)
        .join(AnalysisRun, AnalysisRun.id == AnalysisInteraction.run_id)
        .join(AnalysisSession, AnalysisSession.id == AnalysisRun.session_id)
        .filter(AnalysisInteraction.id == interaction_id, AnalysisSession.tenant_id == tenant_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="交互请求不存在或不属于当前企业")
    return row


def tenant_summary(context: TenantContext) -> dict:
    with PlatformSession() as session:
        member_count = session.query(func.count(TenantMembership.id)).filter_by(
            tenant_id=context.tenant_id,
            enabled=True,
        ).scalar()
        source_count = session.query(func.count(DataSource.id)).filter_by(
            tenant_id=context.tenant_id
        ).scalar()
    return {
        "id": context.tenant_id,
        "slug": context.tenant_slug,
        "name": context.tenant_name,
        "role": context.member_role,
        "canAdminister": context.can_administer,
        "memberCount": int(member_count or 0),
        "dataSourceCount": int(source_count or 0),
        "isolation": "tenant-and-source",
    }


def list_tenant_members(tenant_id: str) -> list[dict]:
    with PlatformSession() as session:
        rows = (
            session.query(TenantMembership, PlatformUser)
            .join(PlatformUser, PlatformUser.id == TenantMembership.user_id)
            .filter(TenantMembership.tenant_id == tenant_id)
            .order_by(TenantMembership.joined_at)
            .all()
        )
        return [
            {
                "id": membership.id,
                "userId": user.id,
                "username": user.username,
                "displayName": user.display_name or user.username,
                "role": membership.member_role,
                "enabled": membership.enabled,
                "joinedAt": membership.joined_at.isoformat(),
            }
            for membership, user in rows
        ]
