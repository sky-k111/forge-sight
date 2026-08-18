"""Independent durable worker for semantic index synchronization."""

from __future__ import annotations

import logging
import socket
import threading
from datetime import datetime, timedelta

from sqlalchemy import or_

from app.config import settings
from app.db.models import SemanticIndexJob
from app.db.platform import PlatformSession
from app.retrieval.semantic import rebuild_source_index

log = logging.getLogger(__name__)
_stop = threading.Event()
_thread: threading.Thread | None = None
_worker_id = f"{socket.gethostname()}:{id(_stop)}"


def _claim_job() -> str | None:
    now = datetime.now()
    with PlatformSession() as session:
        query = (
            session.query(SemanticIndexJob)
            .filter(
                SemanticIndexJob.next_attempt_at <= now,
                or_(
                    SemanticIndexJob.status == "queued",
                    (SemanticIndexJob.status == "running")
                    & or_(SemanticIndexJob.lease_expires_at.is_(None), SemanticIndexJob.lease_expires_at < now),
                ),
            )
            .order_by(SemanticIndexJob.requested_at)
        )
        if session.bind and session.bind.dialect.name == "postgresql":
            query = query.with_for_update(skip_locked=True)
        job = query.first()
        if job is None:
            return None
        job.status = "running"
        job.attempt += 1
        job.started_at = job.started_at or now
        job.lease_owner = _worker_id
        job.lease_expires_at = now + timedelta(seconds=max(30, settings.semantic_index_worker_lease_seconds))
        job.updated_at = now
        session.commit()
        return job.id


def _complete(job_id: str, result: dict) -> None:
    with PlatformSession() as session:
        job = session.query(SemanticIndexJob).filter_by(id=job_id).first()
        if job is None:
            return
        now = datetime.now()
        job.status = "succeeded"
        job.result_json = result
        job.error = ""
        job.finished_at = now
        job.updated_at = now
        job.lease_owner = None
        job.lease_expires_at = None
        session.commit()


def _fail(job_id: str, exc: Exception) -> None:
    with PlatformSession() as session:
        job = session.query(SemanticIndexJob).filter_by(id=job_id).first()
        if job is None:
            return
        now = datetime.now()
        job.error = f"{type(exc).__name__}: {exc}"[:4000]
        job.updated_at = now
        job.lease_owner = None
        job.lease_expires_at = None
        if job.attempt < job.max_attempts:
            job.status = "queued"
            job.next_attempt_at = now + timedelta(seconds=min(60, 2 ** job.attempt))
        else:
            job.status = "failed"
            job.finished_at = now
        session.commit()


def _run(job_id: str) -> None:
    with PlatformSession() as session:
        job = session.query(SemanticIndexJob).filter_by(id=job_id).first()
        if job is None:
            return
        source_id, mode = job.source_id, job.mode
    try:
        with PlatformSession() as session:
            result = rebuild_source_index(session, source_id, force=mode == "full")
        _complete(job_id, result)
    except Exception as exc:  # structured state is persisted for retry and UI diagnostics
        log.exception("Semantic index job failed: %s", job_id)
        _fail(job_id, exc)


def _loop() -> None:
    while not _stop.is_set():
        job_id = _claim_job()
        if job_id is None:
            _stop.wait(settings.semantic_index_worker_poll_seconds)
            continue
        _run(job_id)


def start_worker() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="forge-sight-semantic-index-worker", daemon=True)
    _thread.start()


def stop_worker() -> None:
    _stop.set()
    if _thread and _thread.is_alive():
        _thread.join(timeout=5)
