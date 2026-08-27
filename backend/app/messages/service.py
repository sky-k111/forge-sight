"""消息卡片、收件箱与公告生命周期服务。"""

import base64
import hashlib
import json
import re
from datetime import datetime, timedelta

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.agent.ids import new_id
from app.db.models import (
    Announcement,
    DomainEventOutbox,
    Message,
    MessageRecipient,
    PlatformUser,
    Report,
    ReportVersion,
)
from app.reports.repository import (
    claim_outbox_events,
    complete_outbox_event,
    fail_outbox_event,
    get_or_create_message,
    resolve_audience_scope,
)

SOURCE_LABELS = {
    "report-agent": "周报 Agent",
    "admin": "管理员公告",
    "monitoring": "智能异常监控",
}


class AnnouncementConflict(RuntimeError):
    """Announcement lifecycle does not allow the requested mutation."""


def _scope_hash(audience: dict) -> str:
    canonical = json.dumps(
        audience, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _validate_announcement(
    *,
    title: str,
    body: str,
    priority: str,
    audience: dict,
    valid_from: datetime | None,
    expires_at: datetime | None,
) -> None:
    if not title.strip() or len(title.strip()) > 200:
        raise ValueError("公告标题长度必须为 1-200 个字符")
    if not body.strip() or len(body) > 10000:
        raise ValueError("公告正文长度必须为 1-10000 个字符")
    if re.search(r"(?is)<\s*/?\s*[a-z][^>]*>", body):
        raise ValueError("公告正文只允许纯文本")
    if priority not in {"normal", "high", "urgent"}:
        raise ValueError("公告优先级无效")
    if not isinstance(audience, dict):
        raise ValueError("公告接收范围必须是对象")
    allowed_audience_keys = {
        "all",
        "userIds",
        "organizationIds",
        "businessRoles",
        "positions",
    }
    unknown_keys = set(audience) - allowed_audience_keys
    if unknown_keys:
        raise ValueError(f"公告接收范围包含未知字段：{sorted(unknown_keys)}")
    if "all" in audience and audience["all"] is not True:
        raise ValueError("公告接收范围 all 只能为 true")
    for key, values in audience.items():
        if key == "all":
            continue
        if not isinstance(values, list) or any(
            not isinstance(value, str) or not value.strip() for value in values
        ):
            raise ValueError(f"公告接收范围 {key} 必须是非空字符串数组")
    if not audience.get("all") and not any(
        audience.get(key)
        for key in allowed_audience_keys - {"all"}
    ):
        raise ValueError("公告接收范围不能为空")
    if valid_from and expires_at and expires_at <= valid_from:
        raise ValueError("公告到期时间必须晚于生效时间")


def create_announcement(
    session: Session,
    *,
    title: str,
    body: str,
    audience: dict,
    created_by: str,
    priority: str = "normal",
    is_pinned: bool = False,
    valid_from: datetime | None = None,
    expires_at: datetime | None = None,
) -> Announcement:
    _validate_announcement(
        title=title,
        body=body,
        priority=priority,
        audience=audience,
        valid_from=valid_from,
        expires_at=expires_at,
    )
    row = Announcement(
        id=new_id("ann"),
        revision=1,
        title=title.strip(),
        body=body.strip(),
        priority=priority,
        is_pinned=is_pinned,
        audience_json=audience,
        audience_scope_hash=_scope_hash(audience),
        status="draft",
        created_by=created_by,
        valid_from=valid_from,
        expires_at=expires_at,
    )
    session.add(row)
    session.flush()
    return row


def update_announcement(
    session: Session, announcement_id: str, **changes
) -> Announcement:
    row = (
        session.query(Announcement)
        .filter_by(id=announcement_id)
        .with_for_update()
        .one()
    )
    if row.status != "draft":
        raise AnnouncementConflict("只有公告草稿可以修改")
    allowed = {
        "title",
        "body",
        "priority",
        "is_pinned",
        "audience",
        "valid_from",
        "expires_at",
    }
    unknown = set(changes) - allowed
    if unknown:
        raise ValueError(f"不允许修改公告字段：{sorted(unknown)}")
    audience = changes.get("audience", row.audience_json or {})
    values = {
        "title": changes.get("title", row.title),
        "body": changes.get("body", row.body),
        "priority": changes.get("priority", row.priority),
        "audience": audience,
        "valid_from": changes.get("valid_from", row.valid_from),
        "expires_at": changes.get("expires_at", row.expires_at),
    }
    _validate_announcement(**values)
    for key, value in changes.items():
        if key == "audience":
            row.audience_json = value
            row.audience_scope_hash = _scope_hash(value)
        else:
            setattr(row, key, value.strip() if key in {"title", "body"} else value)
    session.flush()
    return row


def _eligible_audience_users(
    session: Session, audience: dict, *, now: datetime
) -> tuple[list[str], dict[str, str]]:
    resolved = resolve_audience_scope(
        session, "admin_announcement", audience, now=now
    )
    eligible: set[str] = set()
    skipped: dict[str, str] = {}
    for identity in resolved.user_ids:
        user = _platform_user(session, identity)
        if user is None:
            skipped[identity] = "user_not_found"
        elif not user.enabled:
            skipped[identity] = "user_disabled"
        else:
            eligible.add(user.id)
    return sorted(eligible), skipped


def publish_announcement(
    session: Session,
    announcement_id: str,
    *,
    published_by: str,
    now: datetime | None = None,
) -> Announcement:
    now = now or datetime.now()
    row = (
        session.query(Announcement)
        .filter_by(id=announcement_id)
        .with_for_update()
        .one()
    )
    if row.status == "published":
        return row
    if row.status != "draft":
        raise AnnouncementConflict("只有公告草稿可以发布")
    eligible, skipped = _eligible_audience_users(
        session, row.audience_json or {}, now=now
    )
    if not eligible:
        raise AnnouncementConflict("公告接收范围内没有有效用户")
    row.status = "published"
    row.published_by = published_by
    row.published_at = now
    event_key = f"announcement.published:{row.id}:r{row.revision}"
    event = session.query(DomainEventOutbox).filter_by(idempotency_key=event_key).first()
    if event is None:
        session.add(
            DomainEventOutbox(
                id=new_id("evt"),
                event_type="announcement.published",
                aggregate_id=row.id,
                idempotency_key=event_key,
                payload_json={
                    "announcementId": row.id,
                    "revision": row.revision,
                    "audienceScopeHash": row.audience_scope_hash,
                    "estimatedRecipientCount": len(eligible),
                    "skipped": skipped,
                    "publishedAt": now.isoformat(),
                },
            )
        )
    session.flush()
    return row


def withdraw_announcement(
    session: Session,
    announcement_id: str,
    *,
    withdrawn_by: str,
    reason: str,
    now: datetime | None = None,
) -> Announcement:
    if not reason.strip():
        raise ValueError("撤回原因不能为空")
    now = now or datetime.now()
    row = (
        session.query(Announcement)
        .filter_by(id=announcement_id)
        .with_for_update()
        .one()
    )
    if row.status == "withdrawn":
        return row
    if row.status != "published":
        raise AnnouncementConflict("只有已发布公告可以撤回")
    row.status = "withdrawn"
    row.withdrawn_by = withdrawn_by
    row.withdrawn_at = now
    row.withdrawal_reason = reason.strip()[:500]
    for message in session.query(Message).filter_by(
        related_type="announcement", related_id=row.id
    ):
        message.status = "withdrawn"
    session.flush()
    return row


def expire_announcements(session: Session, *, now: datetime | None = None) -> int:
    now = now or datetime.now()
    rows = (
        session.query(Announcement)
        .filter(
            Announcement.status == "published",
            Announcement.expires_at.is_not(None),
            Announcement.expires_at <= now,
        )
        .all()
    )
    for row in rows:
        row.status = "expired"
        for message in session.query(Message).filter_by(
            related_type="announcement", related_id=row.id
        ):
            message.status = "expired"
    session.flush()
    return len(rows)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _effective_status(message, now: datetime) -> str:
    if message.status == "published" and message.expires_at and message.expires_at <= now:
        return "expired"
    return message.status


def _summary_lines(value: str) -> list[str]:
    lines = [line.strip() for line in (value or "").splitlines() if line.strip()]
    return lines[:2]


def _primary_action(message) -> dict | None:
    if message.message_type == "weekly_report":
        return {
            "type": "open_report",
            "label": "查看周报",
            "resourceId": message.related_id,
            "versionId": message.report_version_id,
        }
    if message.message_type == "admin_announcement":
        return {
            "type": "open_announcement",
            "label": "查看公告",
            "resourceId": message.related_id,
        }
    if message.message_type == "monitoring_alert":
        return {
            "type": "open_monitoring_alert",
            "label": "查看警报",
            "resourceId": message.related_id,
        }
    return None


def serialize_message_card(message, recipient, *, now: datetime) -> dict:
    """Return a complete, plain-text card contract without storage addresses."""

    status = _effective_status(message, now)
    return {
        "id": message.id,
        "type": message.message_type,
        "sourceLabel": SOURCE_LABELS.get(message.source, "系统消息"),
        "title": message.title,
        "summaryLines": _summary_lines(message.summary),
        "priority": message.priority,
        "isPinned": bool(message.is_pinned and status == "published"),
        "status": status,
        "isRead": recipient.is_read,
        "readAt": _iso(recipient.read_at),
        "publishedAt": _iso(message.published_at),
        "expiresAt": _iso(message.expires_at),
        "primaryAction": _primary_action(message),
        "secondaryAction": None,
        "renderMode": "plain_text",
    }


def _platform_user(session: Session, identity: str) -> PlatformUser | None:
    return (
        session.query(PlatformUser)
        .filter(or_(PlatformUser.id == identity, PlatformUser.username == identity))
        .first()
    )


def _get_or_create_recipient(
    session: Session, message_id: str, user_id: str
) -> MessageRecipient:
    existing = (
        session.query(MessageRecipient)
        .filter_by(message_id=message_id, user_id=user_id)
        .first()
    )
    if existing is not None:
        return existing
    try:
        with session.begin_nested():
            row = MessageRecipient(
                id=new_id("mrc"),
                message_id=message_id,
                user_id=user_id,
                message_status_snapshot="published",
            )
            session.add(row)
            session.flush()
            return row
    except IntegrityError:
        return (
            session.query(MessageRecipient)
            .filter_by(message_id=message_id, user_id=user_id)
            .one()
        )


def _report_scope_allows(
    session: Session,
    report: Report,
    identity_keys: set[str],
    *,
    now: datetime,
) -> bool:
    resolved = resolve_audience_scope(
        session,
        "weekly_report",
        {"userIds": sorted(identity_keys)},
        now=now,
    )
    requested_dimensions = (report.scope_json or {}).get("dimensions") or {}
    dimensions_allowed = all(
        set(values if isinstance(values, list) else [values])
        <= set(resolved.dimensions.get(dimension, ()))
        for dimension, values in requested_dimensions.items()
    )
    return report.source_id in resolved.data_source_ids and dimensions_allowed


def consume_published_event(
    session: Session,
    *,
    event_id: str,
    worker_id: str,
    now: datetime | None = None,
    recipient_batch_size: int = 200,
    lease_seconds: int = 60,
) -> Message:
    """Expand one claimed publication event in resumable idempotent batches."""

    now = now or datetime.now()
    recipient_batch_size = max(1, min(recipient_batch_size, 1000))
    event = (
        session.query(DomainEventOutbox)
        .filter_by(id=event_id)
        .with_for_update()
        .one()
    )
    if event.status != "processing" or event.lease_owner != worker_id:
        raise ValueError("Outbox 事件未被当前消息 Worker 领取")
    event.lease_expires_at = now + timedelta(seconds=max(10, lease_seconds))
    if event.event_type not in {
        "report.version.published",
        "announcement.published",
    }:
        raise ValueError("消息 Worker 暂不支持此发布事件")

    payload = event.payload_json or {}
    report = None
    version = None
    announcement = None
    if event.event_type == "report.version.published":
        report = session.get(Report, payload.get("reportId") or event.aggregate_id)
        version = session.get(ReportVersion, payload.get("versionId"))
        if report is None or version is None or version.report_id != report.id:
            raise ValueError("周报发布事件缺少有效报告版本")
        idempotency_key = f"inbox:report-version:{version.id}"
        message_status = "published"
    else:
        announcement = session.get(
            Announcement, payload.get("announcementId") or event.aggregate_id
        )
        if announcement is None or announcement.status not in {
            "published",
            "withdrawn",
            "expired",
        }:
            raise ValueError("公告发布事件缺少有效公告版本")
        idempotency_key = (
            f"inbox:announcement:{announcement.id}:r{announcement.revision}"
        )
        message_status = announcement.status
        if (
            message_status == "published"
            and announcement.expires_at
            and announcement.expires_at <= now
        ):
            message_status = "expired"

    message = session.query(Message).filter_by(idempotency_key=idempotency_key).first()
    if message is None:
        if report is not None and version is not None:
            message_values = dict(
                id=new_id("msg"),
                message_type="weekly_report",
                source="report-agent",
                title=report.title,
                summary=version.summary or "周报已发布，可查看本期完整内容。",
                related_type="report",
                related_id=report.id,
                report_version_id=version.id,
                priority="normal",
                is_pinned=False,
                status=message_status,
                idempotency_key=idempotency_key,
                valid_from=now,
                published_at=version.published_at or now,
            )
        else:
            assert announcement is not None
            message_values = dict(
                id=new_id("msg"),
                message_type="admin_announcement",
                source="admin",
                title=announcement.title,
                summary=announcement.body,
                related_type="announcement",
                related_id=announcement.id,
                priority=announcement.priority,
                is_pinned=announcement.is_pinned,
                status=message_status,
                idempotency_key=idempotency_key,
                valid_from=announcement.valid_from or announcement.published_at or now,
                expires_at=announcement.expires_at,
                published_at=announcement.published_at or now,
            )
        message = get_or_create_message(session, **message_values)
    elif message_status in {"withdrawn", "expired"}:
        message.status = message_status

    if report is not None:
        audience = (report.scope_json or {}).get("audience") or {
            "userIds": [report.owner_id]
        }
        business_key = "weekly_report"
    else:
        assert announcement is not None
        audience = announcement.audience_json or {}
        business_key = "admin_announcement"
    resolved = resolve_audience_scope(session, business_key, audience, now=now)
    previous_progress = payload.get("messageProcessing") or {}
    processed_identity_keys = {
        str(value) for value in previous_progress.get("processedIdentityKeys") or []
    }
    skipped = dict(previous_progress.get("skipped") or {})
    pending_identity_keys = [
        identity
        for identity in resolved.user_ids
        if identity not in processed_identity_keys
    ]

    for identity in pending_identity_keys[:recipient_batch_size]:
        try:
            with session.begin_nested():
                user = _platform_user(session, identity)
                if user is None:
                    skipped[identity] = "user_not_found"
                elif not user.enabled:
                    skipped[identity] = "user_disabled"
                elif report is not None and not _report_scope_allows(
                    session,
                    report,
                    {user.id},
                    now=now,
                ):
                    skipped[identity] = "data_scope_denied"
                else:
                    _get_or_create_recipient(session, message.id, user.id)
        except Exception as exc:  # noqa: BLE001 - one recipient cannot abort the batch
            skipped[identity] = f"recipient_error:{type(exc).__name__}"
        processed_identity_keys.add(identity)

    remaining_count = sum(
        identity not in processed_identity_keys for identity in resolved.user_ids
    )
    recipient_count = (
        session.query(MessageRecipient).filter_by(message_id=message.id).count()
    )
    event.payload_json = {
        **payload,
        "messageProcessing": {
            "messageId": message.id,
            "recipientCount": recipient_count,
            "processedIdentityKeys": sorted(processed_identity_keys),
            "remainingCount": remaining_count,
            "skipped": skipped,
        },
    }
    if remaining_count:
        event.status = "pending"
        event.lease_owner = None
        event.lease_expires_at = None
        event.next_retry_at = now
        event.error_summary = None
        event.attempt_count = max(0, event.attempt_count - 1)
        session.flush()
        return message

    if _effective_status(message, now) == "published":
        downstream_key = f"message.published:{message.id}"
        if (
            session.query(DomainEventOutbox)
            .filter_by(idempotency_key=downstream_key)
            .first()
            is None
        ):
            session.add(
                DomainEventOutbox(
                    id=new_id("evt"),
                    event_type="message.published",
                    aggregate_id=message.id,
                    idempotency_key=downstream_key,
                    payload_json={
                        "messageId": message.id,
                        "messageType": message.message_type,
                        "recipientCount": recipient_count,
                        "publishedAt": message.published_at.isoformat(),
                    },
                )
            )
    complete_outbox_event(session, event.id, worker_id, now=now)
    return message


def _message_sort_key(message: Message) -> tuple[bool, datetime, str]:
    return bool(message.is_pinned), message.published_at, message.id


def _encode_cursor(message: Message) -> str:
    payload = {
        "isPinned": bool(message.is_pinned),
        "publishedAt": message.published_at.isoformat(),
        "messageId": message.id,
    }
    return base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode()
    ).rstrip(b"=").decode()


def _decode_cursor(cursor: str | None) -> tuple[bool, datetime, str] | None:
    if not cursor:
        return None
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        payload = json.loads(raw)
        if not isinstance(payload["isPinned"], bool):
            raise ValueError("invalid pin value")
        return (
            payload["isPinned"],
            datetime.fromisoformat(str(payload["publishedAt"])),
            str(payload["messageId"]),
        )
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise ValueError("消息分页游标无效") from exc


def _recipient_rows(
    session: Session, identity_keys: set[str]
) -> list[tuple[Message, MessageRecipient]]:
    if not identity_keys:
        return []
    rows = (
        session.query(Message, MessageRecipient)
        .join(MessageRecipient, MessageRecipient.message_id == Message.id)
        .filter(MessageRecipient.user_id.in_(identity_keys))
        .all()
    )
    unique: dict[str, tuple[Message, MessageRecipient]] = {}
    for message, recipient in rows:
        unique.setdefault(message.id, (message, recipient))
    return list(unique.values())


def list_user_messages(
    session: Session,
    *,
    identity_keys: set[str],
    scope_identity_keys: set[str] | None = None,
    message_type: str | None = None,
    read: bool | None = None,
    priority: str | None = None,
    keyword: str = "",
    published_from: datetime | None = None,
    published_to: datetime | None = None,
    cursor: str | None = None,
    limit: int = 20,
    now: datetime | None = None,
) -> dict:
    """List one user's stable, server-interpreted inbox cards."""

    now = now or datetime.now()
    rows = _recipient_rows(session, identity_keys)
    filtered = []
    scope_keys = scope_identity_keys or identity_keys
    for message, recipient in rows:
        if message.valid_from and message.valid_from > now:
            continue
        if message.message_type == "weekly_report":
            report = session.get(Report, message.related_id)
            if report is None or not _report_scope_allows(
                session, report, scope_keys, now=now
            ):
                continue
        if message_type and message.message_type != message_type:
            continue
        if read is not None and recipient.is_read is not read:
            continue
        if priority and message.priority != priority:
            continue
        if published_from and message.published_at < published_from:
            continue
        if published_to and message.published_at >= published_to:
            continue
        searchable = f"{message.title}\n{message.summary}".casefold()
        if keyword.strip() and keyword.strip().casefold() not in searchable:
            continue
        filtered.append((message, recipient))
    filtered.sort(key=lambda item: _message_sort_key(item[0]), reverse=True)
    after_key = _decode_cursor(cursor)
    if after_key:
        filtered = [
            item for item in filtered if _message_sort_key(item[0]) < after_key
        ]
    page = filtered[: max(1, min(limit, 100))]
    has_more = len(filtered) > len(page)
    return {
        "items": [
            serialize_message_card(message, recipient, now=now)
            for message, recipient in page
        ],
        "nextCursor": _encode_cursor(page[-1][0]) if page and has_more else None,
    }


def unread_count(
    session: Session,
    identity_keys: set[str],
    *,
    scope_identity_keys: set[str] | None = None,
    now: datetime | None = None,
) -> int:
    now = now or datetime.now()
    scope_keys = scope_identity_keys or identity_keys
    count = 0
    for message, recipient in _recipient_rows(session, identity_keys):
        if (
            recipient.is_read
            or message.status != "published"
            or (message.valid_from is not None and message.valid_from > now)
            or (message.expires_at is not None and message.expires_at <= now)
        ):
            continue
        if message.message_type == "weekly_report":
            report = session.get(Report, message.related_id)
            if report is None or not _report_scope_allows(
                session, report, scope_keys, now=now
            ):
                continue
        count += 1
    return count


def mark_message_read(
    session: Session,
    message_id: str,
    identity_keys: set[str],
    *,
    now: datetime | None = None,
) -> dict:
    rows = (
        session.query(MessageRecipient)
        .filter(
            MessageRecipient.message_id == message_id,
            MessageRecipient.user_id.in_(identity_keys),
        )
        .with_for_update()
        .all()
    )
    if not rows:
        raise PermissionError("无权读取或标记此消息")
    first_read_at = next((row.read_at for row in rows if row.read_at), None) or now or datetime.now()
    for row in rows:
        row.is_read = True
        row.read_at = row.read_at or first_read_at
    session.flush()
    return {
        "messageId": message_id,
        "isRead": True,
        "readAt": _iso(first_read_at),
    }


def get_user_message(
    session: Session,
    message_id: str,
    identity_keys: set[str],
    *,
    scope_identity_keys: set[str] | None = None,
    now: datetime | None = None,
    mark_read: bool = True,
) -> dict:
    now = now or datetime.now()
    row = next(
        (
            item
            for item in _recipient_rows(session, identity_keys)
            if item[0].id == message_id
        ),
        None,
    )
    if row is None:
        raise PermissionError("无权读取此消息")
    message, recipient = row
    if message.valid_from and message.valid_from > now:
        raise PermissionError("消息尚未生效")
    if message.message_type == "weekly_report":
        report = session.get(Report, message.related_id)
        version = session.get(ReportVersion, message.report_version_id)
        if report is None or version is None or version.report_id != report.id:
            raise PermissionError("周报版本不存在或不可访问")
        if not _report_scope_allows(
            session,
            report,
            scope_identity_keys or identity_keys,
            now=now,
        ):
            raise PermissionError("当前数据权限不允许读取此周报消息")
    if mark_read:
        mark_message_read(session, message_id, identity_keys, now=now)
    card = serialize_message_card(message, recipient, now=now)
    detail = {
        **card,
        "content": message.summary if message.message_type in {"admin_announcement", "monitoring_alert"} else None,
        "relatedType": message.related_type,
        "relatedId": message.related_id,
        "reportVersionId": message.report_version_id,
        "withdrawalReason": None,
    }
    if message.message_type == "admin_announcement" and message.related_id:
        announcement = session.get(Announcement, message.related_id)
        if announcement is not None:
            detail["content"] = announcement.body
            detail["withdrawalReason"] = announcement.withdrawal_reason
    return detail


def announcement_dict(
    session: Session, row: Announcement, *, now: datetime | None = None
) -> dict:
    now = now or datetime.now()
    eligible, skipped = _eligible_audience_users(
        session, row.audience_json or {}, now=now
    )
    status = (
        "expired"
        if row.status == "published" and row.expires_at and row.expires_at <= now
        else row.status
    )
    return {
        "id": row.id,
        "revision": row.revision,
        "title": row.title,
        "body": row.body,
        "priority": row.priority,
        "isPinned": bool(row.is_pinned and status == "published"),
        "audience": row.audience_json or {},
        "audienceScopeHash": row.audience_scope_hash,
        "estimatedRecipientCount": len(eligible),
        "skippedRecipients": skipped,
        "status": status,
        "createdBy": row.created_by,
        "publishedBy": row.published_by,
        "withdrawnBy": row.withdrawn_by,
        "validFrom": _iso(row.valid_from),
        "expiresAt": _iso(row.expires_at),
        "publishedAt": _iso(row.published_at),
        "withdrawnAt": _iso(row.withdrawn_at),
        "withdrawalReason": row.withdrawal_reason,
        "renderMode": "plain_text",
    }


def list_announcements(
    session: Session, *, status: str | None = None, now: datetime | None = None
) -> list[dict]:
    query = session.query(Announcement)
    if status:
        query = query.filter(Announcement.status == status)
    rows = query.order_by(Announcement.created_at.desc(), Announcement.id.desc()).all()
    return [announcement_dict(session, row, now=now) for row in rows]


def run_message_worker_once(
    *,
    session_factory,
    worker_id: str,
    now: datetime | None = None,
    recipient_batch_size: int = 200,
    max_attempts: int = 5,
    retry_seconds: int = 60,
) -> dict[str, int]:
    """Claim only message-owned events and persist per-event retry outcomes."""

    now = now or datetime.now()
    with session_factory() as session:
        claimed = claim_outbox_events(
            session,
            worker_id,
            now=now,
            limit=1,
            event_types=("report.version.published", "announcement.published"),
        )
        event_ids = [row.id for row in claimed]
        session.commit()

    processed = 0
    failed = 0
    for event_id in event_ids:
        with session_factory() as session:
            try:
                consume_published_event(
                    session,
                    event_id=event_id,
                    worker_id=worker_id,
                    now=now,
                    recipient_batch_size=recipient_batch_size,
                )
                completed = session.get(DomainEventOutbox, event_id)
                is_completed = completed is not None and completed.status == "completed"
                session.commit()
                if is_completed:
                    processed += 1
            except Exception as exc:  # noqa: BLE001 - event retry state is the boundary
                session.rollback()
                fail_outbox_event(
                    session,
                    event_id,
                    worker_id,
                    f"{type(exc).__name__}: {exc}",
                    now=now,
                    max_attempts=max_attempts,
                    retry_seconds=retry_seconds,
                )
                session.commit()
                failed += 1
    return {"processed": processed, "failed": failed}
