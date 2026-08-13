"""Report-file generation, authorization, storage, and retry orchestration."""

from __future__ import annotations

import hashlib
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.agent.ids import new_id
from app.db.models import (
    DomainEventOutbox,
    Message,
    MessageRecipient,
    Report,
    ReportFile,
    ReportVersion,
)
from app.reports.exports import render_excel_bytes, render_pdf_bytes


class FileAccessDenied(PermissionError):
    """The user is not allowed to download this report version."""


class FileNotReady(RuntimeError):
    def __init__(self, status: str, error: dict | None = None):
        super().__init__(f"报告文件尚未就绪：{status}")
        self.status = status
        self.error = error


@dataclass(frozen=True)
class Download:
    path: Path
    mime_type: str
    filename: str
    report_id: str
    version_id: str
    file_id: str


MIME_TYPES = {
    "pdf": "application/pdf",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


def _safe_filename(title: str, version: int, extension: str) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|\s]+", "-", title).strip("-. ") or "weekly-report"
    return f"{cleaned}-v{version}.{extension}"


def _is_recipient(session: Session, version_id: str, user_ids: set[str]) -> bool:
    if not user_ids:
        return False
    return (
        session.query(MessageRecipient.id)
        .join(Message, Message.id == MessageRecipient.message_id)
        .filter(
            Message.report_version_id == version_id,
            Message.status == "published",
            MessageRecipient.user_id.in_(user_ids),
            MessageRecipient.message_status_snapshot == "published",
        )
        .first()
        is not None
    )


def resolve_download(
    session: Session,
    file_id: str,
    user: dict,
    storage_root: str | Path,
) -> Download:
    """Authorize a download and resolve an opaque storage URI inside its root."""

    report_file = session.get(ReportFile, file_id)
    if report_file is None:
        raise KeyError("报告文件不存在")
    report = session.get(Report, report_file.report_id)
    version = session.get(ReportVersion, report_file.version_id)
    if report is None or version is None:
        raise KeyError("报告版本不存在")

    user_ids = {str(user.get("id") or ""), str(user.get("username") or "")}
    user_ids.discard("")
    allowed = (
        user.get("role") == "admin"
        or report.owner_id in user_ids
        or _is_recipient(session, version.id, user_ids)
    )
    if not allowed:
        raise FileAccessDenied("无权下载此周报文件")
    if report_file.status != "ready":
        raise FileNotReady(report_file.status, report_file.error_json)
    if not report_file.storage_uri:
        raise FileNotReady("failed", {"code": "FILE_LOCATION_MISSING"})

    root = Path(storage_root).resolve()
    path = (root / report_file.storage_uri).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise FileAccessDenied("文件存储路径无效") from exc
    if not path.is_file():
        raise FileNotReady("failed", {"code": "FILE_NOT_FOUND"})

    extension = "xlsx" if report_file.format == "xlsx" else "pdf"
    return Download(
        path=path,
        mime_type=report_file.mime_type or (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            if extension == "xlsx"
            else "application/pdf"
        ),
        filename=_safe_filename(report.title, version.version, extension),
        report_id=report.id,
        version_id=version.id,
        file_id=report_file.id,
    )


def enqueue_export_jobs(
    session: Session, event: DomainEventOutbox
) -> list[ReportFile]:
    """Expand one published-version event into independent PDF and XLSX jobs."""

    if event.event_type != "report.version.published":
        raise ValueError("只接受 report.version.published 事件")
    payload = event.payload_json or {}
    report_id = payload.get("reportId") or event.aggregate_id
    version_id = payload.get("versionId")
    version = session.get(ReportVersion, version_id)
    report = session.get(Report, report_id)
    if report is None or version is None or version.report_id != report.id:
        raise ValueError("发布事件引用的报告版本不存在")
    if version.status != "published":
        raise ValueError("只能为已发布版本创建文件任务")

    existing = {
        item.format: item
        for item in session.query(ReportFile)
        .filter_by(version_id=version.id)
        .all()
    }
    jobs: list[ReportFile] = []
    for format_name in ("pdf", "xlsx"):
        job = existing.get(format_name)
        if job is None:
            job = ReportFile(
                id=new_id("rpf"),
                report_id=report.id,
                version_id=version.id,
                format=format_name,
                status="queued",
                source_content_checksum=version.content_checksum,
            )
            session.add(job)
            existing[format_name] = job
        jobs.append(job)
    session.flush()
    return jobs


def claim_report_files(
    session: Session,
    worker_id: str,
    *,
    now: datetime | None = None,
    limit: int = 10,
    lease_seconds: int = 60,
    max_attempts: int = 3,
) -> list[ReportFile]:
    """Lease runnable jobs; expired generating jobs are safely reclaimed."""

    current = now or datetime.now()
    exhausted = (
        session.query(ReportFile)
        .filter(
            ReportFile.status == "generating",
            ReportFile.lease_expires_at <= current,
            ReportFile.generation_attempt >= max_attempts,
        )
        .all()
    )
    for row in exhausted:
        row.status = "dead_letter"
        row.error_json = {
            "category": "lease_expired",
            "message": "文件生成租约到期且已达到最大尝试次数",
            "retryable": True,
            "recoverable": True,
            "attempt": row.generation_attempt,
        }
        row.lease_owner = None
        row.lease_expires_at = None
    retryable = or_(ReportFile.next_retry_at.is_(None), ReportFile.next_retry_at <= current)
    eligible = or_(
        ReportFile.status == "queued",
        (ReportFile.status == "failed") & retryable,
        (ReportFile.status == "generating") & (ReportFile.lease_expires_at <= current),
    )
    query = (
        session.query(ReportFile)
        .filter(eligible, ReportFile.generation_attempt < max_attempts)
        .order_by(ReportFile.created_at, ReportFile.format, ReportFile.id)
        .limit(limit)
    )
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        query = query.with_for_update(skip_locked=True)
    rows = query.all()
    for row in rows:
        row.status = "generating"
        row.lease_owner = worker_id
        row.lease_expires_at = current + timedelta(seconds=lease_seconds)
        row.next_retry_at = None
        row.generation_attempt += 1
    session.flush()
    return rows


def generate_claimed_file(
    session: Session,
    file_id: str,
    worker_id: str,
    storage_root: str | Path,
    *,
    now: datetime | None = None,
) -> ReportFile:
    """Render a claimed file to a temporary path and atomically promote it."""

    current = now or datetime.now()
    report_file = session.query(ReportFile).filter_by(id=file_id).with_for_update().one()
    if report_file.status == "ready":
        return report_file
    if report_file.status != "generating" or report_file.lease_owner != worker_id:
        raise ValueError("文件任务未被当前 Worker 领取")
    version = session.get(ReportVersion, report_file.version_id)
    if version is None or version.status != "published" or not version.content_checksum:
        raise ValueError("文件任务缺少已冻结的发布版本")
    if report_file.source_content_checksum not in {None, version.content_checksum}:
        raise ValueError("文件任务与当前冻结版本校验值不一致")

    renderer = render_pdf_bytes if report_file.format == "pdf" else render_excel_bytes
    payload = renderer(version.content_json or {})
    checksum = hashlib.sha256(payload).hexdigest()
    root = Path(storage_root).resolve()
    relative = Path(version.id) / f"{report_file.id}.{report_file.format}"
    target = (root / relative).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + f".{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_bytes(payload)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)

    report_file.status = "ready"
    report_file.storage_uri = relative.as_posix()
    report_file.mime_type = MIME_TYPES[report_file.format]
    report_file.file_size = len(payload)
    report_file.checksum = checksum
    report_file.source_content_checksum = version.content_checksum
    report_file.generated_at = current
    report_file.error_json = None
    report_file.lease_owner = None
    report_file.lease_expires_at = None
    report_file.next_retry_at = None
    event_key = f"report-file-ready:{report_file.id}"
    if session.query(DomainEventOutbox).filter_by(idempotency_key=event_key).first() is None:
        session.add(
            DomainEventOutbox(
                id=new_id("evt"),
                event_type="report.file.ready",
                aggregate_id=report_file.report_id,
                idempotency_key=event_key,
                payload_json={
                    "reportId": report_file.report_id,
                    "versionId": report_file.version_id,
                    "fileId": report_file.id,
                    "format": report_file.format,
                    "checksum": checksum,
                    "fileSize": len(payload),
                    "contentChecksum": version.content_checksum,
                },
            )
        )
    session.flush()
    return report_file


def fail_report_file(
    session: Session,
    file_id: str,
    worker_id: str,
    *,
    error_category: str,
    message: str,
    now: datetime | None = None,
    max_attempts: int = 3,
    retry_seconds: int = 30,
    retryable: bool = True,
) -> ReportFile:
    """Record a classified failure and schedule backoff or recoverable dead letter."""

    current = now or datetime.now()
    row = session.query(ReportFile).filter_by(id=file_id).with_for_update().one()
    if row.status != "generating" or row.lease_owner != worker_id:
        raise ValueError("文件任务未被当前 Worker 领取")
    exhausted = row.generation_attempt >= max_attempts or not retryable
    row.status = "dead_letter" if exhausted else "failed"
    row.next_retry_at = None if exhausted else current + timedelta(seconds=retry_seconds)
    row.error_json = {
        "category": error_category,
        "message": message[:500],
        "retryable": retryable,
        "recoverable": exhausted,
        "attempt": row.generation_attempt,
    }
    row.lease_owner = None
    row.lease_expires_at = None
    session.flush()
    return row


def recover_dead_letter_file(session: Session, file_id: str) -> ReportFile:
    """Return an explicitly selected dead-letter file to the runnable queue."""

    row = session.query(ReportFile).filter_by(id=file_id).with_for_update().one()
    if row.status != "dead_letter":
        raise ValueError("只有死信文件任务可以恢复")
    row.status = "queued"
    row.generation_attempt = 0
    row.error_json = None
    row.next_retry_at = None
    row.lease_owner = None
    row.lease_expires_at = None
    session.flush()
    return row


def run_file_worker_once(
    session_factory,
    worker_id: str,
    storage_root: str | Path,
) -> bool:
    """Claim and execute one job; failures remain independently retryable."""

    with session_factory() as session:
        claimed = claim_report_files(session, worker_id, limit=1)
        session.commit()
        if not claimed:
            return False
        file_id = claimed[0].id
    try:
        with session_factory() as session:
            generate_claimed_file(session, file_id, worker_id, storage_root)
            session.commit()
    except Exception as exc:
        with session_factory() as session:
            row = session.get(ReportFile, file_id)
            if row is not None and row.status == "generating" and row.lease_owner == worker_id:
                fail_report_file(
                    session,
                    file_id,
                    worker_id,
                    error_category=type(exc).__name__,
                    message=str(exc),
                )
                session.commit()
        raise
    return True
