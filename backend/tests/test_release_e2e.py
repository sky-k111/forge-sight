"""T03: deterministic publication-to-inbox-to-files-to-email acceptance flow."""

from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import (
    Base,
    DataScopePolicy,
    Message,
    MessageRecipient,
    PlatformUser,
    Report,
    ReportDelivery,
    ReportFile,
    ReportVersion,
)
from app.delivery.provider import CaptureMailProvider
from app.delivery.service import run_delivery_event_worker_once, run_delivery_worker_once
from app.messages.service import run_message_worker_once
from app.reports.files import run_file_worker_once
from app.reports.repository import publish_version


def _weekly_content() -> dict:
    return {
        "schemaVersion": "weekly-report/v1",
        "identity": {
            "reportId": "report-e2e",
            "versionId": "version-e2e",
            "title": "生产经营周报",
            "reportType": "weekly",
            "periodStart": "2026-08-03T00:00:00",
            "periodEnd": "2026-08-10T00:00:00",
            "comparisonPeriodStart": "2026-07-27T00:00:00",
            "comparisonPeriodEnd": "2026-08-03T00:00:00",
            "timezone": "Asia/Shanghai",
        },
        "managementSummary": {
            "findings": [{
                "level": "confirmed",
                "text": "本周总产量提升 6.12%。",
                "evidenceArtifactIds": ["artifact-output"],
                "evidenceStatus": "verified",
                "confidence": 94,
            }],
            "actions": [{
                "text": "复核 AOI 参数。",
                "owner": "质量负责人",
                "priority": "high",
                "evidenceArtifactIds": ["artifact-quality"],
                "evidenceStatus": "verified",
            }],
        },
        "metrics": [{
            "key": "output_qty", "name": "总产量", "value": 12840, "unit": "件",
            "previousValue": 12100, "changeRate": 0.0612, "trend": "up",
            "evidenceArtifactIds": ["artifact-output"], "confidence": 94,
        }],
        "trends": [{
            "metricKey": "output_qty", "label": "近四周总产量", "unit": "件",
            "points": [{"label": "W31", "value": 12100}, {"label": "W32", "value": 12840}],
        }],
        "anomalies": [{
            "title": "AOI 工序波动", "description": "需要复核参数。", "severity": "high",
            "metricKey": "output_qty", "evidenceArtifactIds": ["artifact-quality"],
            "evidenceStatus": "verified", "confidence": 86,
        }],
        "progress": [],
        "recommendations": [{
            "title": "优先治理 AOI 波动", "rationale": "影响一次通过率。", "priority": "high",
            "evidenceArtifactIds": ["artifact-quality"], "evidenceStatus": "verified",
        }],
        "dataQuality": {"coverage": 96, "delayedSources": [], "missingMetrics": [], "temporaryMetrics": [], "failedSections": []},
        "audience": {"scopeHash": "scope-e2e", "dataSourceIds": [1], "sensitiveFieldsRedacted": True},
        "appendix": {"sources": ["MES"], "definitions": []},
    }


def test_published_weekly_report_reaches_inbox_files_and_email(tmp_path):
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    now = datetime(2026, 8, 10, 9, 0)
    with factory() as session:
        session.add_all([
            PlatformUser(
                id="user-e2e", username="user-e2e", password_hash="test-only",
                display_name="验收用户", role="business", enabled=True,
                email="recipient@example.com", email_canonical="recipient@example.com",
                email_verified_at=now,
            ),
            DataScopePolicy(
                id="scope-e2e", subject_type="user", subject_id="user-e2e",
                business_key="weekly_report", data_source_ids_json=[1], dimensions_json={},
            ),
            Report(
                id="report-e2e", title="生产经营周报", template_id="tpl-weekly", source_id=1,
                report_type="weekly", status="generating", owner_id="user-e2e",
                period_start=datetime(2026, 8, 3), period_end=datetime(2026, 8, 10),
                current_version_id="version-e2e",
                scope_json={"audience": {"userIds": ["user-e2e"]}, "dataSourceIds": [1]},
            ),
            ReportVersion(
                id="version-e2e", report_id="report-e2e", version=1, status="draft",
                summary="本周总产量提升 6.12%。", content_json=_weekly_content(),
            ),
        ])
        session.flush()
        publish_version(
            session,
            report_id="report-e2e",
            version_id="version-e2e",
            event_id="event-e2e",
            idempotency_key="report.version.published:version-e2e",
            event_type="report.version.published",
        )
        session.commit()

    assert run_message_worker_once(session_factory=factory, worker_id="message-e2e", now=now) == {"processed": 1, "failed": 0}
    assert run_file_worker_once(factory, "file-e2e", tmp_path) is True
    assert run_file_worker_once(factory, "file-e2e", tmp_path) is True
    assert run_delivery_event_worker_once(factory, "mail-event-e2e", now=now) == {"processed": 1, "failed": 0}
    provider = CaptureMailProvider()
    assert run_delivery_worker_once(
        factory,
        "mail-send-e2e",
        provider,
        storage_root=tmp_path,
        public_app_url="http://127.0.0.1:5179",
        now=now,
        configuration_fingerprint="capture-e2e",
    ) is True

    with factory() as session:
        message = session.query(Message).one()
        delivery = session.query(ReportDelivery).one()
        files = session.query(ReportFile).order_by(ReportFile.format).all()
        assert message.report_version_id == "version-e2e"
        assert session.query(MessageRecipient).one().user_id == "user-e2e"
        assert [(item.format, item.status) for item in files] == [("pdf", "ready"), ("xlsx", "ready")]
        assert delivery.status == "sent"
        assert delivery.attachment_file_id == next(item.id for item in files if item.format == "pdf")
    assert len(provider.messages) == 1
    assert provider.messages[0].to == "recipient@example.com"
    assert provider.messages[0].attachments[0].content_type == "application/pdf"
