"""Idempotent mail jobs, leases, attachments, retries, and administrative recovery."""

from __future__ import annotations

import hashlib
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import and_, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.agent.ids import new_id
from app.db.models import (
    Announcement,
    DeliveryAttempt,
    DomainEventOutbox,
    Message,
    MessageRecipient,
    PlatformUser,
    Report,
    ReportDelivery,
    ReportFile,
    ReportVersion,
)
from app.delivery.provider import MailAttachment, MailProvider, OutgoingMail, ProviderFailure
from app.delivery.templates import build_announcement_mail, build_weekly_report_mail
from app.reports.repository import (
    claim_outbox_events,
    complete_outbox_event,
    fail_outbox_event,
    resolve_audience_scope,
)


DELIVERY_STATUSES = {
    "queued",
    "sending",
    "waiting_attachment",
    "retry_wait",
    "sent",
    "failed",
    "suppressed",
    "unknown",
    "blocked_configuration",
}
MASKED_ERROR_SUMMARIES = {
    "configuration": "邮件服务配置无效",
    "network": "邮件服务网络异常",
    "rate_limited": "邮件服务暂时限流",
    "provider_error": "邮件服务响应异常",
    "invalid_address": "接收地址被拒绝",
    "invalid_message": "邮件内容无效",
    "attachment": "周报附件无效",
}


def _report_access_allowed(session: Session, report: Report, user_id: str, now: datetime) -> bool:
    scope = resolve_audience_scope(
        session, "weekly_report", {"userIds": [user_id]}, now=now
    )
    requested = (report.scope_json or {}).get("dimensions") or {}
    dimensions_allowed = all(
        set(values if isinstance(values, list) else [values])
        <= set(scope.dimensions.get(dimension, ()))
        for dimension, values in requested.items()
    )
    return report.source_id in scope.data_source_ids and dimensions_allowed


def _suppression_reason(
    session: Session, message: Message, user: PlatformUser | None, now: datetime
) -> str | None:
    if user is None:
        return "user_not_found"
    if not user.enabled:
        return "user_disabled"
    if not user.email or user.email_verified_at is None:
        return "email_unverified"
    if message.status != "published" or (message.expires_at and message.expires_at <= now):
        return "message_unavailable"
    if message.message_type == "weekly_report":
        report = session.get(Report, message.related_id)
        if report is not None and (report.scope_json or {}).get("emailEnabled", True) is False:
            return "email_disabled"
        if report is None or not _report_access_allowed(session, report, user.id, now):
            return "data_scope_denied"
    return None


def _existing_or_create_delivery(
    session: Session, message: Message, recipient: MessageRecipient, now: datetime
) -> ReportDelivery:
    key = f"email:{message.id}:{recipient.user_id}"
    existing = session.query(ReportDelivery).filter_by(idempotency_key=key).first()
    if existing is not None:
        return existing
    user = session.get(PlatformUser, recipient.user_id)
    reason = _suppression_reason(session, message, user, now)
    values = dict(
        id=new_id("dlv"),
        message_id=message.id,
        report_id=message.related_id if message.message_type == "weekly_report" else None,
        version_id=message.report_version_id,
        channel="email",
        status="suppressed" if reason else "queued",
        attempt=1,
        attempt_count=0,
        recipient_user_id=recipient.user_id,
        recipient_address_snapshot=user.email if user and user.email_verified_at else None,
        idempotency_key=key,
        error_category=reason,
        error_json={"reason": reason} if reason else None,
        created_at=now,
        updated_at=now,
    )
    try:
        with session.begin_nested():
            row = ReportDelivery(**values)
            session.add(row)
            session.flush()
            return row
    except IntegrityError:
        return session.query(ReportDelivery).filter_by(idempotency_key=key).one()


def consume_message_published_event(
    session: Session,
    event_id: str,
    worker_id: str,
    *,
    now: datetime | None = None,
) -> int:
    """Expand N's single publication event into per-recipient email jobs."""

    current = now or datetime.now()
    event = session.query(DomainEventOutbox).filter_by(id=event_id).with_for_update().one()
    if event.status != "processing" or event.lease_owner != worker_id:
        raise ValueError("Outbox 事件未被当前邮件 Worker 领取")
    if event.event_type != "message.published":
        raise ValueError("邮件 Worker 只消费 message.published")
    message_id = (event.payload_json or {}).get("messageId") or event.aggregate_id
    message = session.get(Message, message_id)
    if message is None:
        raise ValueError("消息发布事件引用的消息不存在")
    recipients = (
        session.query(MessageRecipient)
        .filter_by(message_id=message.id)
        .order_by(MessageRecipient.id)
        .all()
    )
    for recipient in recipients:
        _existing_or_create_delivery(session, message, recipient, current)
    complete_outbox_event(session, event.id, worker_id, now=current)
    return len(recipients)


def run_delivery_event_worker_once(
    session_factory,
    worker_id: str,
    *,
    now: datetime | None = None,
    max_attempts: int = 5,
    retry_seconds: int = 30,
) -> dict[str, int]:
    """Claim one N event; unrelated publication and file events remain untouched."""

    current = now or datetime.now()
    with session_factory() as session:
        events = claim_outbox_events(
            session,
            worker_id,
            now=current,
            limit=1,
            event_types=("message.published",),
        )
        session.commit()
        if not events:
            return {"processed": 0, "failed": 0}
        event_id = events[0].id
    try:
        with session_factory() as session:
            consume_message_published_event(
                session, event_id, worker_id, now=current
            )
            session.commit()
        return {"processed": 1, "failed": 0}
    except Exception as exc:  # event payload errors are retried/dead-lettered, not raised to the loop
        with session_factory() as session:
            fail_outbox_event(
                session,
                event_id,
                worker_id,
                type(exc).__name__,
                now=current,
                max_attempts=max_attempts,
                retry_seconds=retry_seconds,
            )
            session.commit()
        return {"processed": 0, "failed": 1}


def _safe_public_url(public_app_url: str) -> str:
    value = public_app_url.strip().rstrip("/")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        raise ProviderFailure("configuration", "PUBLIC_APP_URL must be an absolute HTTP(S) URL", retryable=False)
    if parsed.query or parsed.fragment:
        raise ProviderFailure("configuration", "PUBLIC_APP_URL cannot contain query or fragment", retryable=False)
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ProviderFailure("configuration", "PUBLIC_APP_URL must use HTTPS outside localhost", retryable=False)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def _link(public_app_url: str, message: Message) -> str:
    root = _safe_public_url(public_app_url)
    if message.message_type == "weekly_report":
        return f"{root}/workspace/reports/{message.related_id}"
    return f"{root}/workspace/messages/{message.id}"


def claim_deliveries(
    session: Session,
    worker_id: str,
    *,
    now: datetime | None = None,
    lease_seconds: int = 60,
    limit: int = 10,
) -> list[ReportDelivery]:
    current = now or datetime.now()
    due = or_(ReportDelivery.next_retry_at.is_(None), ReportDelivery.next_retry_at <= current)
    eligible = or_(
        and_(ReportDelivery.status.in_(("queued", "retry_wait", "waiting_attachment")), due),
        and_(ReportDelivery.status == "sending", ReportDelivery.lease_expires_at <= current),
    )
    query = (
        session.query(ReportDelivery)
        .filter(ReportDelivery.channel == "email", eligible)
        .order_by(ReportDelivery.created_at, ReportDelivery.id)
        .limit(limit)
    )
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        query = query.with_for_update(skip_locked=True)
    rows = query.all()
    for row in rows:
        row.status = "sending"
        row.lease_owner = worker_id
        row.lease_expires_at = current + timedelta(seconds=max(10, lease_seconds))
        row.next_retry_at = None
        row.updated_at = current
    session.flush()
    return rows


def delivery_work_pending(
    session: Session,
    *,
    configuration_fingerprint: str,
    now: datetime | None = None,
) -> bool:
    """Avoid opening an SMTP connection when no delivery can make progress."""

    current = now or datetime.now()
    candidates = session.query(ReportDelivery).filter(
        ReportDelivery.channel == "email",
        ReportDelivery.status.in_(
            ("queued", "retry_wait", "waiting_attachment", "sending", "blocked_configuration")
        ),
    )
    for row in candidates:
        metadata = row.error_json or {}
        verified = metadata.get("verifiedConfigurationFingerprint")
        if verified and verified != configuration_fingerprint:
            continue
        if row.status == "blocked_configuration":
            failed = metadata.get("configurationFingerprint")
            if failed != configuration_fingerprint or (
                row.next_retry_at is not None and row.next_retry_at <= current
            ):
                return True
            continue
        if row.status == "sending":
            if row.lease_expires_at is not None and row.lease_expires_at <= current:
                return True
            continue
        if row.next_retry_at is None or row.next_retry_at <= current:
            return True
    return False


def block_deliveries_for_configuration(
    session: Session,
    _detail: str = "",
    *,
    configuration_fingerprint: str | None = None,
    retry_seconds: int = 300,
    now: datetime | None = None,
) -> int:
    """Pause runnable email jobs with a sanitized, administrator-visible reason."""

    query = session.query(ReportDelivery).filter(
        ReportDelivery.channel == "email",
        ReportDelivery.status.in_(("queued", "retry_wait", "waiting_attachment")),
    )
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        query = query.with_for_update(skip_locked=True)
    rows = query.all()
    current = now or datetime.now()
    blocked = 0
    for row in rows:
        verified = (row.error_json or {}).get("verifiedConfigurationFingerprint")
        if verified and verified != configuration_fingerprint:
            continue
        row.status = "blocked_configuration"
        row.error_category = "configuration"
        row.error_json = {
            "message": MASKED_ERROR_SUMMARIES["configuration"],
            "configurationFingerprint": configuration_fingerprint,
        }
        row.next_retry_at = current + timedelta(seconds=max(30, retry_seconds))
        row.lease_owner = None
        row.lease_expires_at = None
        row.updated_at = current
        blocked += 1
    session.flush()
    return blocked


def resume_configuration_blocked_deliveries(
    session: Session,
    *,
    configuration_verified: bool,
    configuration_fingerprint: str | None = None,
    now: datetime | None = None,
) -> int:
    if not configuration_verified:
        return 0
    query = session.query(ReportDelivery).filter_by(
        channel="email", status="blocked_configuration"
    )
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        query = query.with_for_update(skip_locked=True)
    rows = query.all()
    current = now or datetime.now()
    for row in rows:
        row.status = "queued"
        row.error_category = None
        row.error_json = {
            "verifiedConfigurationFingerprint": configuration_fingerprint
        }
        row.next_retry_at = current
        row.updated_at = current
    session.flush()
    return len(rows)


def _claimed_delivery(session: Session, delivery_id: str, worker_id: str) -> ReportDelivery:
    delivery = session.query(ReportDelivery).filter_by(id=delivery_id).with_for_update().one()
    if delivery.status != "sending" or delivery.lease_owner != worker_id:
        raise ValueError("邮件投递未被当前 Worker 领取")
    return delivery


def _with_verified_configuration(
    delivery: ReportDelivery, values: dict
) -> dict:
    """Keep the healthy-provider assignment across non-terminal transitions."""

    verified = (delivery.error_json or {}).get("verifiedConfigurationFingerprint")
    if verified:
        values["verifiedConfigurationFingerprint"] = verified
    return values


def _release_waiting(delivery: ReportDelivery, now: datetime, reason: str) -> None:
    delivery.status = "waiting_attachment"
    delivery.next_retry_at = now + timedelta(seconds=30)
    delivery.error_category = "attachment"
    delivery.error_json = _with_verified_configuration(delivery, {"reason": reason})
    delivery.lease_owner = None
    delivery.lease_expires_at = None
    delivery.updated_at = now


def _pdf_attachment(
    session: Session,
    delivery: ReportDelivery,
    storage_root: str | Path,
    max_bytes: int,
    now: datetime,
) -> tuple[MailAttachment | None, str | None]:
    pdf = session.query(ReportFile).filter_by(version_id=delivery.version_id, format="pdf").first()
    if pdf is None or pdf.status != "ready":
        reason = "pdf_missing" if pdf is None else f"pdf_{pdf.status}"
        _release_waiting(delivery, now, reason)
        return None, "waiting"
    version = session.get(ReportVersion, delivery.version_id)
    if version is None or version.status != "published" or version.content_checksum != pdf.source_content_checksum:
        raise ProviderFailure("attachment", "PDF does not match the published version", retryable=False)
    if not pdf.storage_uri or pdf.file_size is None or not pdf.checksum:
        raise ProviderFailure("attachment", "PDF metadata is incomplete", retryable=False)
    if pdf.file_size > max_bytes:
        delivery.attachment_omitted = True
        delivery.attachment_file_id = pdf.id
        return None, "附件大小超过邮件服务限制，请通过安全链接在线查看或下载 PDF。"
    root = Path(storage_root).resolve()
    path = (root / pdf.storage_uri).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ProviderFailure("attachment", "PDF path is outside storage root", retryable=False) from exc
    if not path.is_file():
        _release_waiting(delivery, now, "pdf_file_missing")
        return None, "waiting"
    content = path.read_bytes()
    if len(content) != pdf.file_size or hashlib.sha256(content).hexdigest() != pdf.checksum:
        raise ProviderFailure("attachment", "PDF integrity check failed", retryable=False)
    delivery.attachment_file_id = pdf.id
    safe_title = re.sub(r"[\\/:*?\"<>|\r\n]+", "-", "ForgeSight-weekly").strip("-.")
    return MailAttachment(f"{safe_title}.pdf", "application/pdf", content), None


def _build_mail(
    session: Session,
    delivery: ReportDelivery,
    storage_root: str | Path,
    public_app_url: str,
    attachment_max_bytes: int,
    now: datetime,
) -> OutgoingMail | None:
    message = session.get(Message, delivery.message_id)
    user = session.get(PlatformUser, delivery.recipient_user_id)
    recipient = session.query(MessageRecipient).filter_by(
        message_id=delivery.message_id, user_id=delivery.recipient_user_id
    ).first()
    reason = _suppression_reason(session, message, user, now) if message else "message_missing"
    if recipient is None:
        reason = "recipient_revoked"
    if not reason and user and (
        user.email != delivery.recipient_address_snapshot or user.email_verified_at is None
    ):
        reason = "email_changed"
    if reason:
        delivery.status = "suppressed"
        delivery.error_category = reason
        delivery.error_json = {"reason": reason}
        delivery.lease_owner = None
        delivery.lease_expires_at = None
        delivery.updated_at = now
        return None
    assert message is not None and user is not None and delivery.recipient_address_snapshot
    link = _link(public_app_url, message)
    attachments: tuple[MailAttachment, ...] = ()
    if message.message_type == "weekly_report":
        report = session.get(Report, message.related_id)
        version = session.get(ReportVersion, message.report_version_id)
        if (
            report is None
            or version is None
            or version.report_id != report.id
            or version.status != "published"
        ):
            raise ProviderFailure("invalid_message", "weekly report version is unavailable", retryable=False)
        attachment, omitted = _pdf_attachment(
            session, delivery, storage_root, attachment_max_bytes, now
        )
        if omitted == "waiting":
            return None
        if attachment:
            attachments = (attachment,)
        body = build_weekly_report_mail(
            title=report.title,
            period_start=report.period_start,
            period_end=report.period_end,
            version=version.version,
            content=version.content_json or {},
            fallback_summary=version.summary or message.summary,
            link=link,
            attachment_omitted_reason=omitted,
        )
    else:
        announcement = session.get(Announcement, message.related_id)
        if announcement is None:
            raise ProviderFailure("invalid_message", "announcement is unavailable", retryable=False)
        body = build_announcement_mail(
            title=announcement.title,
            body=announcement.body,
            publisher=announcement.published_by or announcement.created_by,
            published_at=announcement.published_at or message.published_at,
            expires_at=announcement.expires_at,
            link=link,
        )
    return OutgoingMail(
        to=delivery.recipient_address_snapshot,
        subject=body.subject,
        idempotency_key=delivery.idempotency_key or delivery.id,
        text_body=body.text_body,
        html_body=body.html_body,
        attachments=attachments,
    )


def _retry_delay(delivery_id: str, attempt: int) -> int:
    jitter = int(hashlib.sha256(delivery_id.encode()).hexdigest()[:2], 16) % 8
    return min(3600, 30 * (2 ** max(0, attempt - 1)) + jitter)


def _record_failure(
    session: Session,
    delivery: ReportDelivery,
    attempt: DeliveryAttempt,
    failure: ProviderFailure,
    now: datetime,
    max_attempts: int,
) -> None:
    exhausted = not failure.retryable or delivery.attempt_count >= max_attempts
    delivery.status = "failed" if exhausted else "retry_wait"
    delivery.next_retry_at = None if exhausted else now + timedelta(
        seconds=_retry_delay(delivery.id, delivery.attempt_count)
    )
    delivery.error_category = failure.category
    delivery.error_json = _with_verified_configuration(
        delivery,
        {"message": MASKED_ERROR_SUMMARIES.get(failure.category, "邮件投递失败")},
    )
    delivery.lease_owner = None
    delivery.lease_expires_at = None
    delivery.updated_at = now
    attempt.status = "failed"
    attempt.error_category = failure.category
    attempt.error_json = delivery.error_json
    attempt.finished_at = now


def prepare_claimed_delivery(
    session: Session,
    delivery_id: str,
    worker_id: str,
    *,
    storage_root: str | Path,
    public_app_url: str,
    attachment_max_bytes: int = 10 * 1024 * 1024,
    max_attempts: int = 3,
    now: datetime | None = None,
) -> tuple[ReportDelivery, OutgoingMail | None, str | None]:
    """Persist an uncertainty barrier before any provider side effect."""

    current = now or datetime.now()
    delivery = _claimed_delivery(session, delivery_id, worker_id)
    try:
        mail = _build_mail(
            session,
            delivery,
            storage_root,
            public_app_url,
            max(1, attachment_max_bytes),
            current,
        )
        if mail is None:
            return delivery, None, None
        delivery.attempt_count += 1
        attempt = DeliveryAttempt(
            id=new_id("dla"),
            delivery_id=delivery.id,
            attempt_number=delivery.attempt_count,
            status="unknown",
            started_at=current,
        )
        session.add(attempt)
        delivery.status = "unknown"
        delivery.updated_at = current
        session.flush()
        return delivery, mail, attempt.id
    except ProviderFailure as failure:
        delivery.attempt_count += 1
        attempt = DeliveryAttempt(
            id=new_id("dla"),
            delivery_id=delivery.id,
            attempt_number=delivery.attempt_count,
            status="failed",
            started_at=current,
            finished_at=current,
        )
        session.add(attempt)
        _record_failure(session, delivery, attempt, failure, current, max_attempts)
        return delivery, None, None


def _finalize_provider_result(
    session: Session,
    delivery_id: str,
    attempt_id: str,
    *,
    outcome=None,
    failure: ProviderFailure | None = None,
    duration_ms: int,
    max_attempts: int,
    configuration_fingerprint: str | None,
    now: datetime,
) -> ReportDelivery:
    delivery = session.query(ReportDelivery).filter_by(id=delivery_id).with_for_update().one()
    attempt = session.query(DeliveryAttempt).filter_by(id=attempt_id).with_for_update().one()
    if delivery.status != "unknown" or attempt.status != "unknown":
        return delivery
    attempt.duration_ms = duration_ms
    delivery.last_duration_ms = duration_ms
    if failure is not None:
        if failure.uncertain:
            delivery.error_category = "outcome_unknown"
            delivery.error_json = _with_verified_configuration(
                delivery, {"message": "邮件服务结果不确定，需管理员核对后处理"}
            )
            delivery.updated_at = now
            attempt.error_category = "outcome_unknown"
            attempt.error_json = delivery.error_json
            attempt.finished_at = now
            delivery.lease_owner = None
            delivery.lease_expires_at = None
            return delivery
        if failure.category == "configuration":
            delivery.status = "blocked_configuration"
            delivery.next_retry_at = now + timedelta(seconds=300)
            delivery.error_category = "configuration"
            delivery.error_json = {
                "message": MASKED_ERROR_SUMMARIES["configuration"],
                "configurationFingerprint": configuration_fingerprint,
            }
            delivery.lease_owner = None
            delivery.lease_expires_at = None
            delivery.updated_at = now
            attempt.status = "failed"
            attempt.error_category = "configuration"
            attempt.error_json = delivery.error_json
            attempt.finished_at = now
            block_deliveries_for_configuration(
                session,
                configuration_fingerprint=configuration_fingerprint,
            )
            return delivery
        _record_failure(session, delivery, attempt, failure, now, max_attempts)
        return delivery
    delivery.status = "sent"
    delivery.sent_at = now
    delivery.provider_message_id = outcome.provider_message_id[:160]
    delivery.response_id = outcome.provider_message_id[:120]
    delivery.error_category = None
    delivery.error_json = None
    delivery.next_retry_at = None
    delivery.lease_owner = None
    delivery.lease_expires_at = None
    delivery.updated_at = now
    attempt.status = "sent"
    attempt.provider_message_id = outcome.provider_message_id[:160]
    attempt.finished_at = now
    return delivery


def run_delivery_worker_once(
    session_factory,
    worker_id: str,
    provider: MailProvider,
    *,
    storage_root: str | Path,
    public_app_url: str,
    attachment_max_bytes: int = 10 * 1024 * 1024,
    max_attempts: int = 3,
    lease_seconds: int = 60,
    configuration_fingerprint: str | None = None,
    now: datetime | None = None,
) -> bool:
    current = now or datetime.now()
    with session_factory() as session:
        rows = claim_deliveries(
            session, worker_id, now=current, lease_seconds=lease_seconds, limit=1
        )
        session.commit()
        if not rows:
            return False
        delivery_id = rows[0].id
    with session_factory() as session:
        _delivery, mail, attempt_id = prepare_claimed_delivery(
            session,
            delivery_id,
            worker_id,
            storage_root=storage_root,
            public_app_url=public_app_url,
            attachment_max_bytes=attachment_max_bytes,
            max_attempts=max_attempts,
            now=current,
        )
        session.commit()
    if mail is None or attempt_id is None:
        return True
    started = time.monotonic()
    try:
        outcome = provider.send(mail)
        failure = None
    except ProviderFailure as exc:
        outcome = None
        failure = exc
    except Exception as exc:
        outcome = None
        failure = ProviderFailure(
            "provider_error", type(exc).__name__, retryable=False, uncertain=True
        )
    duration = max(0, int((time.monotonic() - started) * 1000))
    with session_factory() as session:
        _finalize_provider_result(
            session,
            delivery_id,
            attempt_id,
            outcome=outcome,
            failure=failure,
            duration_ms=duration,
            max_attempts=max_attempts,
            configuration_fingerprint=configuration_fingerprint,
            now=current,
        )
        session.commit()
    return True


def serialize_delivery(row: ReportDelivery) -> dict:
    address = row.recipient_address_snapshot or ""
    local, separator, domain = address.partition("@")
    masked = f"{local[:1]}***@{domain}" if separator else ""
    return {
        "id": row.id,
        "messageId": row.message_id,
        "reportId": row.report_id,
        "versionId": row.version_id,
        "recipientUserId": row.recipient_user_id,
        "recipientAddress": masked,
        "channel": row.channel,
        "status": row.status,
        "attemptCount": row.attempt_count,
        "lastErrorCategory": row.error_category,
        "nextRetryAt": row.next_retry_at.isoformat() if row.next_retry_at else None,
        "providerMessageId": row.provider_message_id,
        "attachmentOmitted": row.attachment_omitted,
        "createdAt": row.created_at.isoformat(),
        "sentAt": row.sent_at.isoformat() if row.sent_at else None,
    }


def list_deliveries(
    session: Session,
    *,
    message_id: str | None = None,
    report_id: str | None = None,
    announcement_id: str | None = None,
    content_type: str | None = None,
    recipient_user_id: str | None = None,
    status: str | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    limit: int = 100,
) -> list[dict]:
    query = session.query(ReportDelivery).filter_by(channel="email")
    if message_id:
        query = query.filter_by(message_id=message_id)
    if report_id:
        query = query.filter_by(report_id=report_id)
    if content_type == "report":
        query = query.filter(ReportDelivery.report_id.is_not(None))
    elif content_type == "announcement":
        query = query.filter(
            ReportDelivery.report_id.is_(None),
            ReportDelivery.message_id.is_not(None),
        )
    if announcement_id:
        query = query.join(Message, Message.id == ReportDelivery.message_id).filter(
            Message.related_type == "announcement",
            Message.related_id == announcement_id,
        )
    if recipient_user_id:
        query = query.filter_by(recipient_user_id=recipient_user_id)
    if status:
        query = query.filter_by(status=status)
    if created_from:
        query = query.filter(ReportDelivery.created_at >= created_from)
    if created_to:
        query = query.filter(ReportDelivery.created_at < created_to)
    rows = query.order_by(ReportDelivery.created_at.desc(), ReportDelivery.id.desc()).limit(limit).all()
    return [serialize_delivery(row) for row in rows]


def retry_delivery(
    session: Session,
    delivery_id: str,
    *,
    confirm_unknown: bool = False,
    now: datetime | None = None,
) -> ReportDelivery:
    current = now or datetime.now()
    row = session.query(ReportDelivery).filter_by(id=delivery_id, channel="email").with_for_update().one()
    if row.status not in {"failed", "retry_wait", "waiting_attachment", "unknown"}:
        raise ValueError("只有失败或等待中的邮件投递可以重发")
    if row.status == "unknown":
        if row.lease_expires_at is not None and row.lease_expires_at > current:
            raise ValueError("邮件仍在投递，当前不能重发")
        if not confirm_unknown:
            raise ValueError("投递结果未知，需确认投递结果后再重发")
    row.status = "queued"
    row.next_retry_at = current
    row.error_category = None
    row.error_json = _with_verified_configuration(row, {}) or None
    row.lease_owner = None
    row.lease_expires_at = None
    row.updated_at = current
    session.flush()
    return row
