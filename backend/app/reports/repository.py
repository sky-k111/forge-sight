"""周报领域的最小事务仓储与统一接收范围解析。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, or_
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.agent.ids import new_id
from app.db.models import (
    DataScopePolicy,
    DomainEventOutbox,
    Message,
    OrganizationUnit,
    Report,
    ReportDelivery,
    ReportFile,
    ReportVersion,
    UserMembership,
)
from app.reports.domain import PublishedVersionImmutable, ensure_version_mutable
from app.reports.schema import content_checksum as compute_content_checksum


@dataclass(frozen=True)
class ResolvedAudienceScope:
    """下游任务可直接使用的接收人和数据范围。"""

    user_ids: tuple[str, ...]
    data_source_ids: tuple[int, ...]
    dimensions: dict[str, tuple[str, ...]]


def _active_at(model, now: datetime):
    return and_(
        model.enabled.is_(True),
        or_(model.effective_from.is_(None), model.effective_from <= now),
        or_(model.effective_to.is_(None), model.effective_to > now),
    )


def _expand_organization_ids(session: Session, requested: set[str]) -> set[str]:
    if not requested:
        return set()
    rows = session.query(OrganizationUnit.id, OrganizationUnit.parent_id).all()
    selected = set(requested)
    changed = True
    while changed:
        changed = False
        for org_id, parent_id in rows:
            if parent_id in selected and org_id not in selected:
                selected.add(org_id)
                changed = True
    return selected


def resolve_audience_scope(
    session: Session,
    business_key: str,
    audience: dict[str, Any],
    *,
    now: datetime | None = None,
) -> ResolvedAudienceScope:
    """把显式用户、组织、业务角色和岗位统一解析为下游可执行范围。"""

    now = now or datetime.now()
    explicit_users = set(audience.get("userIds") or [])
    organization_ids = _expand_organization_ids(
        session, set(audience.get("organizationIds") or [])
    )
    business_roles = set(audience.get("businessRoles") or [])
    positions = set(audience.get("positions") or [])

    memberships = session.query(UserMembership).filter(_active_at(UserMembership, now)).all()
    matched = [
        row
        for row in memberships
        if row.organization_id in organization_ids
        or row.business_role in business_roles
        or row.position_code in positions
        or row.user_id in explicit_users
    ]
    user_ids = explicit_users | {row.user_id for row in matched}

    subjects = {
        "user": user_ids,
        "organization": organization_ids | {row.organization_id for row in matched},
        "business_role": {row.business_role for row in matched},
        "position": {row.position_code for row in matched},
    }
    policies = (
        session.query(DataScopePolicy)
        .filter(DataScopePolicy.business_key == business_key, _active_at(DataScopePolicy, now))
        .all()
    )
    allowed_sources: set[int] = set()
    denied_sources: set[int] = set()
    dimension_values: dict[str, set[str]] = {}
    for policy in policies:
        if policy.subject_id not in subjects.get(policy.subject_type, set()):
            continue
        target = denied_sources if policy.effect == "deny" else allowed_sources
        target.update(policy.data_source_ids_json or [])
        if policy.effect != "deny":
            for dimension, values in (policy.dimensions_json or {}).items():
                dimension_values.setdefault(dimension, set()).update(values)

    return ResolvedAudienceScope(
        user_ids=tuple(sorted(user_ids)),
        data_source_ids=tuple(sorted(allowed_sources - denied_sources)),
        dimensions={key: tuple(sorted(values)) for key, values in sorted(dimension_values.items())},
    )


def get_or_create_message(session: Session, **values) -> Message:
    """按业务幂等键返回既有消息或创建新消息。"""

    return _insert_or_get(session, Message, {"idempotency_key": values["idempotency_key"]}, values)


def _insert_or_get(session: Session, model, lookup: dict[str, Any], values: dict[str, Any]):
    """利用数据库唯一约束完成并发安全的 insert-or-get。"""

    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        statement = postgresql_insert(model).values(**values).on_conflict_do_nothing()
    elif dialect == "sqlite":
        statement = sqlite_insert(model).values(**values).on_conflict_do_nothing()
    else:
        existing = session.query(model).filter_by(**lookup).first()
        if existing is not None:
            return existing
        row = model(**values)
        session.add(row)
        session.flush()
        return row
    session.execute(statement)
    return session.query(model).filter_by(**lookup).one()


def get_or_create_report(session: Session, **values) -> Report:
    """按订阅、调度周期和受众范围幂等创建报告。"""

    lookup = {
        "subscription_id": values["subscription_id"],
        "schedule_key": values["schedule_key"],
        "audience_scope_hash": values["audience_scope_hash"],
    }
    return _insert_or_get(session, Report, lookup, values)


def get_or_create_delivery(session: Session, **values) -> ReportDelivery:
    """按业务投递幂等键返回既有记录或创建新记录。"""

    return _insert_or_get(
        session,
        ReportDelivery,
        {"idempotency_key": values["idempotency_key"]},
        values,
    )


def publish_version(
    session: Session,
    *,
    report_id: str,
    version_id: str,
    event_id: str,
    idempotency_key: str,
    event_type: str = "report.published",
    event_payload: dict[str, Any] | None = None,
) -> ReportVersion:
    """在调用方事务内原子发布版本并记录 Outbox 事件。"""

    report = session.query(Report).filter_by(id=report_id).with_for_update().one()
    version = session.query(ReportVersion).filter_by(id=version_id).with_for_update().one()
    if report.current_version_id != version.id or version.report_id != report.id:
        raise ValueError("只能发布报告当前版本")
    now = datetime.now()
    checksum = compute_content_checksum(version.content_json or {})
    report.status = "published"
    version.status = "published"
    version.content_checksum = checksum
    version.frozen_at = version.frozen_at or now
    version.published_at = version.published_at or now
    if session.query(DomainEventOutbox).filter_by(idempotency_key=idempotency_key).first() is None:
        payload = {
            "reportId": report_id,
            "versionId": version_id,
            "contentChecksum": checksum,
        }
        if event_payload is not None:
            payload.update(
                {
                    "eventId": event_id,
                    **event_payload,
                    "publishedAt": version.published_at.isoformat(),
                }
            )
        session.add(
            DomainEventOutbox(
                id=event_id,
                event_type=event_type,
                aggregate_id=report_id,
                idempotency_key=idempotency_key,
                payload_json=payload,
            )
        )
    export_formats = (
        ("pdf", "xlsx")
        if report.report_type == "weekly"
        and (version.content_json or {}).get("schemaVersion") == "weekly-report/v1"
        else ()
    )
    for format_name in export_formats:
        if (
            session.query(ReportFile)
            .filter_by(version_id=version.id, format=format_name)
            .first()
            is None
        ):
            session.add(
                ReportFile(
                    id=new_id("rpf"),
                    report_id=report.id,
                    version_id=version.id,
                    format=format_name,
                    status="queued",
                    source_content_checksum=checksum,
                )
            )
    session.flush()
    return version


def replace_current_version(
    session: Session,
    report_id: str,
    replacement_version_id: str,
) -> ReportVersion:
    """原子建立替代关系并切换报告当前版本指针。"""

    report = session.query(Report).filter_by(id=report_id).with_for_update().one()
    replacement = (
        session.query(ReportVersion)
        .filter_by(id=replacement_version_id)
        .with_for_update()
        .one()
    )
    if replacement.report_id != report.id:
        raise ValueError("替代版本不属于当前报告")
    if report.current_version_id == replacement.id:
        return replacement
    replacement.supersedes_version_id = report.current_version_id
    report.current_version_id = replacement.id
    session.flush()
    return replacement


def update_report_version(session: Session, version_id: str, **changes) -> ReportVersion:
    """更新未发布版本的正文或生成快照。"""

    version = session.query(ReportVersion).filter_by(id=version_id).with_for_update().one()
    ensure_version_mutable(version.status)
    allowed = {
        "summary",
        "content_json",
        "data_snapshot_json",
        "validation_json",
        "model_snapshot_json",
        "data_quality_json",
    }
    for key, value in changes.items():
        if key not in allowed:
            raise ValueError(f"不允许修改版本字段 {key}")
        setattr(version, key, value)
    session.flush()
    return version


def update_report_file(session: Session, file_id: str, **changes) -> ReportFile:
    """更新生成中的文件；ready 后 URI 与校验值保持不可变。"""

    report_file = session.query(ReportFile).filter_by(id=file_id).with_for_update().one()
    immutable_fields = {
        "storage_uri",
        "mime_type",
        "file_size",
        "checksum",
        "source_content_checksum",
    }
    if report_file.status == "ready" and immutable_fields.intersection(changes):
        raise PublishedVersionImmutable("已就绪报告文件不可替换")
    allowed = immutable_fields | {
        "status",
        "generation_attempt",
        "error_json",
        "generated_at",
        "next_retry_at",
        "lease_owner",
        "lease_expires_at",
    }
    for key, value in changes.items():
        if key not in allowed:
            raise ValueError(f"不允许修改文件字段 {key}")
        setattr(report_file, key, value)
    session.flush()
    return report_file


def claim_outbox_events(
    session: Session,
    worker_id: str,
    *,
    now: datetime | None = None,
    lease_seconds: int = 60,
    limit: int = 20,
    event_types: tuple[str, ...] | None = None,
) -> list[DomainEventOutbox]:
    """领取可处理事件；过期 processing 租约可以被其他 Worker 恢复。"""

    now = now or datetime.now()
    eligible = or_(
        and_(
            DomainEventOutbox.status.in_(("pending", "retry_wait")),
            or_(DomainEventOutbox.next_retry_at.is_(None), DomainEventOutbox.next_retry_at <= now),
        ),
        and_(
            DomainEventOutbox.status == "processing",
            DomainEventOutbox.lease_expires_at <= now,
        ),
    )
    query = session.query(DomainEventOutbox).filter(eligible)
    if event_types:
        query = query.filter(DomainEventOutbox.event_type.in_(event_types))
    rows = (
        query
        .order_by(DomainEventOutbox.created_at, DomainEventOutbox.id)
        .with_for_update(skip_locked=True)
        .limit(limit)
        .all()
    )
    for row in rows:
        row.status = "processing"
        row.lease_owner = worker_id
        row.lease_expires_at = now + timedelta(seconds=lease_seconds)
        row.attempt_count += 1
    session.flush()
    return rows


def _claimed_event(session: Session, event_id: str, worker_id: str) -> DomainEventOutbox:
    row = session.query(DomainEventOutbox).filter_by(id=event_id).with_for_update().one()
    if row.status != "processing" or row.lease_owner != worker_id:
        raise ValueError("Outbox 事件未被当前 Worker 领取")
    return row


def complete_outbox_event(
    session: Session,
    event_id: str,
    worker_id: str,
    *,
    now: datetime | None = None,
) -> DomainEventOutbox:
    """确认当前 Worker 已处理事件。"""

    row = _claimed_event(session, event_id, worker_id)
    row.status = "completed"
    row.acknowledged_at = now or datetime.now()
    row.lease_owner = None
    row.lease_expires_at = None
    row.next_retry_at = None
    row.error_summary = None
    session.flush()
    return row


def fail_outbox_event(
    session: Session,
    event_id: str,
    worker_id: str,
    error_summary: str,
    *,
    now: datetime | None = None,
    max_attempts: int = 5,
    retry_seconds: int = 60,
) -> DomainEventOutbox:
    """记录失败；达到上限转入死信，否则等待下一次重试。"""

    now = now or datetime.now()
    row = _claimed_event(session, event_id, worker_id)
    row.status = "dead_letter" if row.attempt_count >= max_attempts else "retry_wait"
    row.next_retry_at = None if row.status == "dead_letter" else now + timedelta(seconds=retry_seconds)
    row.error_summary = error_summary[:500]
    row.lease_owner = None
    row.lease_expires_at = None
    session.flush()
    return row


def recover_dead_letter_event(session: Session, event_id: str) -> DomainEventOutbox:
    """由管理员把死信恢复为待处理状态。"""

    row = session.query(DomainEventOutbox).filter_by(id=event_id).with_for_update().one()
    if row.status != "dead_letter":
        raise ValueError("只有死信事件可以恢复")
    row.status = "pending"
    row.next_retry_at = None
    row.error_summary = None
    row.lease_owner = None
    row.lease_expires_at = None
    session.flush()
    return row
