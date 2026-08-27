"""Durable, idempotent weekly-report PDF delivery for enterprise IM channels."""

from __future__ import annotations

import hashlib
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.agent.ids import new_id
from app.db.models import (
    DeliveryAttempt,
    DingTalkBinding,
    FeishuBinding,
    Message,
    MessageRecipient,
    Report,
    ReportDelivery,
    ReportFile,
    ReportVersion,
)
from app.delivery.im_provider import OutgoingPdf, PdfProvider
from app.delivery.provider import ProviderFailure, ProviderResult
from app.delivery.service import _report_access_allowed, _retry_delay

IM_CHANNELS = ("feishu", "dingtalk")
MASKED_FAILURES = {
    "configuration": "平台文件投递配置无效",
    "network": "平台文件投递网络异常",
    "rate_limited": "平台文件投递暂时限流",
    "provider_error": "平台文件投递响应异常",
    "invalid_address": "平台账号不可用",
    "invalid_message": "周报消息不可用",
    "attachment": "周报 PDF 无效",
}


def claim_pdf_deliveries(
    session: Session,
    worker_id: str,
    channels: tuple[str, ...],
    *,
    now: datetime | None = None,
    lease_seconds: int = 60,
    limit: int = 1,
) -> list[ReportDelivery]:
    current = now or datetime.now()
    enabled_channels = tuple(channel for channel in channels if channel in IM_CHANNELS)
    if not enabled_channels:
        return []
    due = or_(ReportDelivery.next_retry_at.is_(None), ReportDelivery.next_retry_at <= current)
    eligible = or_(
        and_(
            ReportDelivery.status.in_(("queued", "retry_wait", "waiting_attachment")),
            due,
        ),
        and_(
            ReportDelivery.status == "sending",
            ReportDelivery.lease_expires_at.is_not(None),
            ReportDelivery.lease_expires_at <= current,
        ),
    )
    query = (
        session.query(ReportDelivery)
        .filter(ReportDelivery.channel.in_(enabled_channels), eligible)
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


def _suppress(delivery: ReportDelivery, reason: str, now: datetime) -> None:
    delivery.status = "suppressed"
    delivery.error_category = reason
    delivery.error_json = {"reason": reason}
    delivery.next_retry_at = None
    delivery.lease_owner = None
    delivery.lease_expires_at = None
    delivery.updated_at = now


def _identity_still_valid(session: Session, delivery: ReportDelivery) -> bool:
    if delivery.channel == "feishu":
        binding = session.query(FeishuBinding).filter_by(
            user_id=delivery.recipient_user_id, bind_status="bound"
        ).first()
        return bool(
            binding
            and binding.open_id
            and binding.open_id == delivery.recipient_address_snapshot
        )
    binding = session.query(DingTalkBinding).filter_by(
        user_id=delivery.recipient_user_id, bind_status="bound"
    ).first()
    return bool(
        binding
        and binding.union_id
        and binding.union_id == delivery.recipient_address_snapshot
    )


def _verified_pdf(
    session: Session,
    delivery: ReportDelivery,
    storage_root: str | Path,
    now: datetime,
) -> OutgoingPdf | None:
    message = session.get(Message, delivery.message_id)
    recipient = session.query(MessageRecipient).filter_by(
        message_id=delivery.message_id, user_id=delivery.recipient_user_id
    ).first()
    report = session.get(Report, delivery.report_id)
    version = session.get(ReportVersion, delivery.version_id)
    if recipient is None:
        _suppress(delivery, "recipient_revoked", now)
        return None
    if not _identity_still_valid(session, delivery):
        _suppress(delivery, "binding_changed", now)
        return None
    if (
        message is None
        or message.status != "published"
        or message.message_type != "weekly_report"
        or (message.expires_at is not None and message.expires_at <= now)
    ):
        _suppress(delivery, "message_unavailable", now)
        return None
    if (
        report is None
        or version is None
        or version.report_id != report.id
        or version.status != "published"
        or report.current_version_id != version.id
    ):
        raise ProviderFailure(
            "invalid_message", "Published report version is unavailable", retryable=False
        )
    if not _report_access_allowed(session, report, delivery.recipient_user_id, now):
        _suppress(delivery, "data_scope_denied", now)
        return None
    pdf = session.query(ReportFile).filter_by(
        version_id=version.id, format="pdf"
    ).first()
    if pdf is None or pdf.status != "ready":
        delivery.status = "waiting_attachment"
        delivery.error_category = "attachment"
        delivery.error_json = {
            "reason": "pdf_missing" if pdf is None else f"pdf_{pdf.status}"
        }
        delivery.next_retry_at = now + timedelta(seconds=30)
        delivery.lease_owner = None
        delivery.lease_expires_at = None
        delivery.updated_at = now
        return None
    if version.content_checksum != pdf.source_content_checksum:
        raise ProviderFailure(
            "attachment", "PDF does not match the published version", retryable=False
        )
    if not pdf.storage_uri or pdf.file_size is None or not pdf.checksum:
        raise ProviderFailure(
            "attachment", "PDF metadata is incomplete", retryable=False
        )
    root = Path(storage_root).resolve()
    path = (root / pdf.storage_uri).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ProviderFailure(
            "attachment", "PDF path is outside storage root", retryable=False
        ) from exc
    if not path.is_file():
        delivery.status = "waiting_attachment"
        delivery.error_category = "attachment"
        delivery.error_json = {"reason": "pdf_file_missing"}
        delivery.next_retry_at = now + timedelta(seconds=30)
        delivery.lease_owner = None
        delivery.lease_expires_at = None
        delivery.updated_at = now
        return None
    content = path.read_bytes()
    if len(content) != pdf.file_size or hashlib.sha256(content).hexdigest() != pdf.checksum:
        raise ProviderFailure(
            "attachment", "PDF integrity check failed", retryable=False
        )
    delivery.attachment_file_id = pdf.id
    safe_title = re.sub(r'[\\/:*?"<>|\r\n]+', "-", report.title).strip("-.")
    return OutgoingPdf(
        channel=delivery.channel,
        recipient_id=delivery.recipient_address_snapshot or "",
        filename=f"{safe_title or 'ForgeSight-weekly'}.pdf",
        content=content,
        title=report.title,
        idempotency_key=delivery.idempotency_key or delivery.id,
    )


def _record_failure(
    delivery: ReportDelivery,
    attempt: DeliveryAttempt,
    failure: ProviderFailure,
    now: datetime,
    max_attempts: int,
) -> None:
    exhausted = not failure.retryable or delivery.attempt_count >= max_attempts
    delivery.status = "failed" if exhausted else "retry_wait"
    delivery.next_retry_at = (
        None
        if exhausted
        else now + timedelta(seconds=_retry_delay(delivery.id, delivery.attempt_count))
    )
    delivery.error_category = failure.category
    delivery.error_json = {
        "message": MASKED_FAILURES.get(failure.category, "平台 PDF 投递失败")
    }
    delivery.lease_owner = None
    delivery.lease_expires_at = None
    delivery.updated_at = now
    attempt.status = "failed"
    attempt.error_category = failure.category
    attempt.error_json = delivery.error_json
    attempt.finished_at = now


def _prepare(
    session: Session,
    delivery_id: str,
    worker_id: str,
    storage_root: str | Path,
    now: datetime,
    max_attempts: int,
) -> tuple[OutgoingPdf | None, str | None]:
    delivery = session.query(ReportDelivery).filter_by(id=delivery_id).with_for_update().one()
    if delivery.status != "sending" or delivery.lease_owner != worker_id:
        raise ValueError("PDF 投递未被当前 Worker 领取")
    try:
        outgoing = _verified_pdf(session, delivery, storage_root, now)
        if outgoing is None:
            return None, None
        delivery.attempt_count += 1
        attempt = DeliveryAttempt(
            id=new_id("dla"),
            delivery_id=delivery.id,
            attempt_number=delivery.attempt_count,
            status="unknown",
            started_at=now,
        )
        session.add(attempt)
        delivery.status = "unknown"
        delivery.updated_at = now
        session.flush()
        return outgoing, attempt.id
    except ProviderFailure as failure:
        delivery.attempt_count += 1
        attempt = DeliveryAttempt(
            id=new_id("dla"),
            delivery_id=delivery.id,
            attempt_number=delivery.attempt_count,
            status="failed",
            started_at=now,
            finished_at=now,
        )
        session.add(attempt)
        _record_failure(delivery, attempt, failure, now, max_attempts)
        return None, None


def _finalize(
    session: Session,
    delivery_id: str,
    attempt_id: str,
    *,
    outcome: ProviderResult | None,
    failure: ProviderFailure | None,
    duration_ms: int,
    max_attempts: int,
    now: datetime,
) -> None:
    delivery = session.query(ReportDelivery).filter_by(id=delivery_id).with_for_update().one()
    attempt = session.query(DeliveryAttempt).filter_by(id=attempt_id).with_for_update().one()
    if delivery.status != "unknown" or attempt.status != "unknown":
        return
    delivery.last_duration_ms = duration_ms
    attempt.duration_ms = duration_ms
    if failure is not None:
        _record_failure(delivery, attempt, failure, now, max_attempts)
        return
    assert outcome is not None
    delivery.status = "sent"
    delivery.provider_message_id = outcome.provider_message_id
    delivery.response_id = outcome.provider_message_id
    delivery.sent_at = now
    delivery.next_retry_at = None
    delivery.error_category = None
    delivery.error_json = None
    delivery.lease_owner = None
    delivery.lease_expires_at = None
    delivery.updated_at = now
    attempt.status = "sent"
    attempt.provider_message_id = outcome.provider_message_id
    attempt.finished_at = now


def run_pdf_delivery_worker_once(
    session_factory,
    worker_id: str,
    providers: dict[str, PdfProvider],
    *,
    storage_root: str | Path,
    now: datetime | None = None,
    max_attempts: int = 3,
    lease_seconds: int = 60,
) -> bool:
    current = now or datetime.now()
    with session_factory() as session:
        rows = claim_pdf_deliveries(
            session,
            worker_id,
            tuple(providers),
            now=current,
            lease_seconds=lease_seconds,
            limit=1,
        )
        session.commit()
        if not rows:
            return False
        delivery_id = rows[0].id
        channel = rows[0].channel
    with session_factory() as session:
        outgoing, attempt_id = _prepare(
            session,
            delivery_id,
            worker_id,
            storage_root,
            current,
            max_attempts,
        )
        session.commit()
    if outgoing is None or attempt_id is None:
        return True
    provider = providers[channel]
    started = time.monotonic()
    try:
        outcome = provider.send(outgoing)
        failure = None
    except ProviderFailure as exc:
        outcome = None
        failure = exc
    except Exception as exc:  # do not persist provider details or secrets
        outcome = None
        failure = ProviderFailure(
            "provider_error", type(exc).__name__, retryable=False, uncertain=True
        )
    duration = max(0, int((time.monotonic() - started) * 1000))
    with session_factory() as session:
        _finalize(
            session,
            delivery_id,
            attempt_id,
            outcome=outcome,
            failure=failure,
            duration_ms=duration,
            max_attempts=max_attempts,
            now=current,
        )
        session.commit()
    return True


__all__ = ["claim_pdf_deliveries", "run_pdf_delivery_worker_once"]
