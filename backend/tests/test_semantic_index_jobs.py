"""Durable semantic index queue behavior."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import Base, DataSource, SemanticIndexJob
from app.retrieval.index_jobs import enqueue_index_job, retry_failed_job, source_index_status


def _session_factory():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def test_full_job_dominates_queued_incremental_job():
    factory = _session_factory()
    with factory() as session:
        source = DataSource(name="test", type="mysql", db_name="biz", username="reader")
        session.add(source)
        session.flush()
        first = enqueue_index_job(session, source.id, mode="incremental", reason="metric changed")
        second = enqueue_index_job(session, source.id, mode="full", reason="new source scanned")
        session.commit()
        assert first.id == second.id
        assert second.mode == "full"
        assert session.query(SemanticIndexJob).count() == 1


def test_running_job_does_not_swallow_new_change():
    factory = _session_factory()
    with factory() as session:
        source = DataSource(name="test", type="mysql", db_name="biz", username="reader")
        session.add(source)
        session.flush()
        running = enqueue_index_job(session, source.id, mode="incremental", reason="first")
        running.status = "running"
        follow_up = enqueue_index_job(session, source.id, mode="incremental", reason="second")
        session.commit()
        assert running.id != follow_up.id
        assert session.query(SemanticIndexJob).count() == 2


def test_failed_job_can_be_retried_and_status_is_projected():
    factory = _session_factory()
    with factory() as session:
        source = DataSource(name="test", type="mysql", db_name="biz", username="reader")
        session.add(source)
        session.flush()
        failed = enqueue_index_job(session, source.id, mode="full", reason="manual")
        failed.status = "failed"
        failed.error = "provider unavailable"
        session.flush()
        retried = retry_failed_job(session, failed.id)
        session.commit()
        projected = source_index_status(session, source.id)
        assert retried.id != failed.id
        assert projected["status"] == "queued"
        assert projected["latestJob"]["reason"].startswith("手动重试")
