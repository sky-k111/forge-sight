"""Durable semantic-index job queue, status projection and retry operations."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import func, text

from app.config import settings
from app.db.models import DataSource, DataTable, SemanticIndexJob


STATUS_LABELS = {
    "not_started": "尚未建立索引",
    "queued": "等待索引",
    "running": "语义索引中",
    "succeeded": "索引已就绪",
    "failed": "索引失败",
}


def serialize_job(job: SemanticIndexJob | None) -> dict | None:
    if job is None:
        return None
    status_label = "等待自动重试" if job.status == "queued" and job.attempt > 0 and job.error else STATUS_LABELS.get(job.status, job.status)
    return {
        "id": job.id,
        "sourceId": job.source_id,
        "mode": job.mode,
        "reason": job.reason,
        "status": job.status,
        "statusLabel": status_label,
        "attempt": job.attempt,
        "maxAttempts": job.max_attempts,
        "error": job.error,
        "result": job.result_json or {},
        "embeddingModel": job.embedding_model,
        "embeddingDimensions": job.embedding_dimensions,
        "requestedAt": job.requested_at.isoformat() if job.requested_at else None,
        "startedAt": job.started_at.isoformat() if job.started_at else None,
        "finishedAt": job.finished_at.isoformat() if job.finished_at else None,
        "updatedAt": job.updated_at.isoformat() if job.updated_at else None,
    }


def latest_job(session, source_id: int) -> SemanticIndexJob | None:
    return (
        session.query(SemanticIndexJob)
        .filter_by(source_id=source_id)
        .order_by(SemanticIndexJob.requested_at.desc(), SemanticIndexJob.id.desc())
        .first()
    )


def enqueue_index_job(session, source_id: int, *, mode: str, reason: str) -> SemanticIndexJob:
    """Enqueue inside the caller's transaction; queued work is coalesced per source.

    A full rebuild dominates an already queued incremental job. Running jobs are not
    coalesced because their document snapshot may already have been collected.
    """

    if mode not in {"incremental", "full"}:
        raise ValueError(f"Unsupported semantic index mode: {mode}")
    session.flush()
    if session.query(DataSource.id).filter_by(id=source_id).first() is None:
        raise KeyError(f"数据源 {source_id} 不存在")
    queued = (
        session.query(SemanticIndexJob)
        .filter_by(source_id=source_id, status="queued")
        .order_by(SemanticIndexJob.requested_at.desc())
        .first()
    )
    now = datetime.now()
    if queued is not None:
        if mode == "full":
            queued.mode = "full"
        queued.reason = reason
        queued.error = ""
        queued.next_attempt_at = now
        queued.updated_at = now
        return queued
    job = SemanticIndexJob(
        id=str(uuid4()),
        source_id=source_id,
        mode=mode,
        reason=reason,
        status="queued",
        max_attempts=max(1, settings.semantic_index_max_attempts),
        embedding_model=settings.embedding_model,
        embedding_dimensions=settings.embedding_dimensions,
        requested_at=now,
        next_attempt_at=now,
        updated_at=now,
    )
    session.add(job)
    session.flush()
    return job


def enqueue_all_sources(
    session,
    *,
    mode: str = "incremental",
    reason: str,
    tenant_id: str | None = None,
) -> list[SemanticIndexJob]:
    query = session.query(DataSource.id)
    if tenant_id:
        query = query.filter(DataSource.tenant_id == tenant_id)
    source_ids = [row[0] for row in query.order_by(DataSource.id).all()]
    return [enqueue_index_job(session, source_id, mode=mode, reason=reason) for source_id in source_ids]


def retry_failed_job(session, job_id: str) -> SemanticIndexJob:
    failed = session.query(SemanticIndexJob).filter_by(id=job_id).first()
    if failed is None:
        raise KeyError(f"索引任务 {job_id} 不存在")
    if failed.status != "failed":
        raise ValueError("只有最终失败的索引任务可以重试")
    return enqueue_index_job(
        session,
        failed.source_id,
        mode=failed.mode,
        reason=f"手动重试：{failed.reason or failed.id}",
    )


def source_index_status(session, source_id: int) -> dict:
    source = session.query(DataSource).filter_by(id=source_id).first()
    if source is None:
        raise KeyError(f"数据源 {source_id} 不存在")
    job = latest_job(session, source_id)
    status = job.status if job else "not_started"
    registered_tables = session.query(func.count(DataTable.id)).filter_by(
        source_id=source_id, registered=True
    ).scalar() or 0
    result = dict(job.result_json or {}) if job else {}
    document_counts = _document_counts(session, source_id)
    serialized = serialize_job(job)
    return {
        "sourceId": source_id,
        "sourceName": source.name,
        "enabled": settings.retrieval_semantic_enabled,
        "status": status,
        "statusLabel": (
            "语义索引未启用"
            if not settings.retrieval_semantic_enabled
            else serialized["statusLabel"] if serialized else STATUS_LABELS["not_started"]
        ),
        "latestJob": serialized,
        "registeredTables": int(registered_tables),
        "documents": int(sum(document_counts.values()) or result.get("documents", 0)),
        "documentCounts": document_counts,
        "embeddingModel": settings.embedding_model,
        "embeddingDimensions": settings.embedding_dimensions,
    }


def _document_counts(session, source_id: int) -> dict[str, int]:
    if session.get_bind().dialect.name != "postgresql":
        return {}
    try:
        with session.begin_nested():
            rows = session.execute(
                text(
                    "SELECT object_type, COUNT(*) FROM semantic_search_document "
                    "WHERE source_id=:source_id AND enabled=TRUE AND embedding_model=:model "
                    "GROUP BY object_type"
                ),
                {"source_id": source_id, "model": settings.embedding_model},
            ).all()
        return {str(kind): int(count) for kind, count in rows}
    except Exception:  # table/extension may not have been initialized yet
        return {}
