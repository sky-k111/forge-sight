"""V08: idempotent report-file jobs, leases, retries, and ready events."""

from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import Base, DomainEventOutbox, Report, ReportFile, ReportVersion
from app.reports.files import (
    claim_report_files,
    enqueue_export_jobs,
    fail_report_file,
    generate_claimed_file,
    recover_dead_letter_file,
)
from app.reports.schema import content_checksum


def _content() -> dict:
    return {
        "schemaVersion": "weekly-report/v1",
        "identity": {
            "reportId": "report-1",
            "versionId": "version-1",
            "title": "生产经营周报",
            "reportType": "weekly",
            "periodStart": "2026-08-03T00:00:00",
            "periodEnd": "2026-08-10T00:00:00",
            "timezone": "Asia/Shanghai",
        },
        "managementSummary": {"findings": [], "actions": []},
        "metrics": [],
        "trends": [],
        "anomalies": [],
        "progress": [],
        "recommendations": [],
        "dataQuality": {
            "coverage": 100,
            "delayedSources": [],
            "missingMetrics": [],
            "temporaryMetrics": [],
            "failedSections": [],
        },
        "appendix": {"sources": ["MES"], "definitions": []},
    }


def _session_factory():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    content = _content()
    with factory() as session:
        session.add_all(
            [
                Report(
                    id="report-1",
                    title="生产经营周报",
                    template_id="tpl-weekly",
                    source_id=1,
                    status="published",
                    current_version_id="version-1",
                    period_start=datetime(2026, 8, 3),
                    period_end=datetime(2026, 8, 10),
                ),
                ReportVersion(
                    id="version-1",
                    report_id="report-1",
                    version=1,
                    status="published",
                    schema_version="weekly-report/v1",
                    content_json=content,
                    content_checksum=content_checksum(content),
                ),
                DomainEventOutbox(
                    id="published-event",
                    event_type="report.version.published",
                    aggregate_id="report-1",
                    idempotency_key="report-version-published:version-1",
                    payload_json={"reportId": "report-1", "versionId": "version-1"},
                ),
            ]
        )
        session.commit()
    return factory


def test_publish_event_creates_exactly_one_job_per_format():
    factory = _session_factory()
    with factory() as session:
        event = session.get(DomainEventOutbox, "published-event")
        first = enqueue_export_jobs(session, event)
        second = enqueue_export_jobs(session, event)
        session.commit()

        assert {item.format for item in first} == {"pdf", "xlsx"}
        assert {item.id for item in second} == {item.id for item in first}
        assert session.query(ReportFile).count() == 2


def test_expired_lease_is_reclaimed_without_creating_duplicate_job():
    factory = _session_factory()
    now = datetime(2026, 8, 13, 9, 0)
    with factory() as session:
        enqueue_export_jobs(session, session.get(DomainEventOutbox, "published-event"))
        session.commit()
        first = claim_report_files(session, "worker-a", now=now, limit=1, lease_seconds=30)
        first_id = first[0].id
        first_attempt = first[0].generation_attempt
        session.commit()
        blocked = claim_report_files(session, "worker-b", now=now + timedelta(seconds=10), limit=1)
        reclaimed = claim_report_files(session, "worker-b", now=now + timedelta(seconds=31), limit=1)

        assert len(first) == 1
        assert first_attempt == 1
        assert len(blocked) == 1  # the other format remains independently runnable
        assert reclaimed[0].id == first_id
        assert reclaimed[0].generation_attempt == 2


def test_expired_final_attempt_becomes_recoverable_dead_letter():
    factory = _session_factory()
    now = datetime(2026, 8, 13, 9, 0)
    with factory() as session:
        enqueue_export_jobs(session, session.get(DomainEventOutbox, "published-event"))
        claimed = claim_report_files(
            session,
            "worker-a",
            now=now,
            limit=1,
            lease_seconds=10,
            max_attempts=1,
        )
        target_id = claimed[0].id
        session.commit()

        assert claim_report_files(
            session,
            "worker-b",
            now=now + timedelta(seconds=11),
            limit=1,
            max_attempts=1,
        )
        expired = session.get(ReportFile, target_id)

        assert expired.status == "dead_letter"
        assert expired.error_json["category"] == "lease_expired"


def test_successful_generation_is_atomic_and_emits_ready_event(tmp_path):
    factory = _session_factory()
    now = datetime(2026, 8, 13, 9, 0)
    with factory() as session:
        enqueue_export_jobs(session, session.get(DomainEventOutbox, "published-event"))
        pdf_job = next(item for item in claim_report_files(session, "worker-a", now=now) if item.format == "pdf")
        session.commit()

        ready = generate_claimed_file(
            session,
            pdf_job.id,
            "worker-a",
            tmp_path / "reports",
            now=now + timedelta(seconds=5),
        )
        session.commit()

        assert ready.status == "ready"
        assert ready.source_content_checksum == session.get(ReportVersion, "version-1").content_checksum
        assert ready.file_size > 1000
        assert ready.storage_uri.endswith(".pdf")
        assert (tmp_path / "reports" / ready.storage_uri).read_bytes().startswith(b"%PDF-")
        event = session.query(DomainEventOutbox).filter_by(event_type="report.file.ready").one()
        assert event.payload_json["fileId"] == ready.id
        assert event.payload_json["format"] == "pdf"
        assert event.payload_json["checksum"] == ready.checksum


def test_retryable_failure_backs_off_then_enters_recoverable_dead_letter():
    factory = _session_factory()
    now = datetime(2026, 8, 13, 9, 0)
    with factory() as session:
        jobs = enqueue_export_jobs(session, session.get(DomainEventOutbox, "published-event"))
        target_id = next(item.id for item in jobs if item.format == "xlsx")
        claim_report_files(session, "worker-a", now=now)
        failed = fail_report_file(
            session,
            target_id,
            "worker-a",
            error_category="storage_unavailable",
            message="disk temporarily unavailable",
            now=now,
            max_attempts=2,
            retry_seconds=30,
        )
        assert failed.status == "failed"
        assert failed.next_retry_at == now + timedelta(seconds=30)

        claim_report_files(session, "worker-b", now=now + timedelta(seconds=31))
        dead = fail_report_file(
            session,
            target_id,
            "worker-b",
            error_category="storage_unavailable",
            message="disk still unavailable",
            now=now + timedelta(seconds=31),
            max_attempts=2,
        )

        assert dead.status == "dead_letter"
        assert dead.error_json["recoverable"] is True

        recovered = recover_dead_letter_file(session, target_id)
        assert recovered.status == "queued"
        assert recovered.generation_attempt == 0
        assert recovered.error_json is None
