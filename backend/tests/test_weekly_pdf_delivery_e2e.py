"""Automatic publish-to-PDF-to-enterprise-IM acceptance chain."""

from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import (
    Base,
    DataScopePolicy,
    DingTalkBinding,
    FeishuBinding,
    PlatformUser,
    Report,
    ReportDelivery,
    ReportFile,
    ReportSubscription,
    ReportVersion,
)
from app.delivery.im_provider import CapturePdfProvider
from app.delivery.im_service import run_pdf_delivery_worker_once
from app.delivery.service import run_delivery_event_worker_once
from app.messages.service import run_message_worker_once
from app.reports.files import run_file_worker_once
from app.reports.repository import publish_version


def _content() -> dict:
    return {
        "schemaVersion": "weekly-report/v1",
        "identity": {
            "reportId": "report-e2e",
            "versionId": "version-e2e",
            "title": "自动投递验收周报",
            "reportType": "weekly",
            "periodStart": "2026-08-17T00:00:00",
            "periodEnd": "2026-08-23T23:59:59",
            "timezone": "Asia/Shanghai",
        },
        "managementSummary": {"findings": [], "actions": []},
        "metrics": [],
        "trends": [],
        "charts": [],
        "anomalies": [],
        "progress": [],
        "recommendations": [],
        "audience": {
            "scopeHash": "scope-e2e",
            "dataSourceIds": [1],
            "sensitiveFieldsRedacted": True,
        },
        "dataQuality": {
            "coverage": 100,
            "delayedSources": [],
            "missingMetrics": [],
            "temporaryMetrics": [],
            "failedSections": [],
        },
        "appendix": {"sources": ["MES"], "definitions": []},
    }


def test_published_weekly_report_automatically_reaches_both_im_platforms(tmp_path):
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with factory() as session:
        session.add_all(
            [
                PlatformUser(
                    id="user-e2e",
                    username="user-e2e",
                    password_hash="test-only",
                    display_name="E2E User",
                    enabled=True,
                ),
                FeishuBinding(
                    id="feishu-e2e",
                    user_id="user-e2e",
                    tenant_key="tenant-e2e",
                    open_id="ou-e2e",
                    bind_status="bound",
                ),
                DingTalkBinding(
                    id="dingtalk-e2e",
                    user_id="user-e2e",
                    corp_id="corp-e2e",
                    union_id="union-e2e",
                    bind_status="bound",
                ),
                DataScopePolicy(
                    id="scope-e2e",
                    subject_type="user",
                    subject_id="user-e2e",
                    business_key="weekly_report",
                    data_source_ids_json=[1],
                    dimensions_json={},
                ),
                ReportSubscription(
                    id="subscription-e2e",
                    name="双平台自动周报",
                    template_id="tpl-weekly",
                    source_id=1,
                    owner_id="user-e2e",
                    cadence="weekly",
                    weekday=1,
                    channel="feishu_dingtalk",
                    enabled=True,
                ),
                Report(
                    id="report-e2e",
                    title="自动投递验收周报",
                    template_id="tpl-weekly",
                    source_id=1,
                    report_type="weekly",
                    subscription_id="subscription-e2e",
                    owner_id="user-e2e",
                    status="approved",
                    period_start=datetime(2026, 8, 17),
                    period_end=datetime(2026, 8, 23, 23, 59, 59),
                    current_version_id="version-e2e",
                    scope_json={"audience": {"userIds": ["user-e2e"]}},
                ),
                ReportVersion(
                    id="version-e2e",
                    report_id="report-e2e",
                    version=1,
                    status="approved",
                    schema_version="weekly-report/v1",
                    summary="自动链路验收。",
                    content_json=_content(),
                ),
            ]
        )
        session.flush()
        publish_version(
            session,
            report_id="report-e2e",
            version_id="version-e2e",
            event_id="event-report-e2e",
            idempotency_key="report-version-published:version-e2e",
            event_type="report.version.published",
            event_payload={"publishSource": "automatic"},
        )
        session.commit()

    while run_file_worker_once(factory, "file-worker-e2e", tmp_path):
        pass
    assert run_message_worker_once(
        session_factory=factory,
        worker_id="message-worker-e2e",
    ) == {"processed": 1, "failed": 0}
    assert run_delivery_event_worker_once(
        factory,
        "delivery-event-worker-e2e",
    ) == {"processed": 1, "failed": 0}

    feishu = CapturePdfProvider("feishu")
    dingtalk = CapturePdfProvider("dingtalk")
    providers = {"feishu": feishu, "dingtalk": dingtalk}
    assert run_pdf_delivery_worker_once(
        factory, "im-worker-e2e", providers, storage_root=tmp_path
    )
    assert run_pdf_delivery_worker_once(
        factory, "im-worker-e2e", providers, storage_root=tmp_path
    )

    assert feishu.messages[0].content.startswith(b"%PDF")
    assert dingtalk.messages[0].content == feishu.messages[0].content
    with factory() as session:
        assert session.query(ReportFile).filter_by(format="pdf", status="ready").count() == 1
        rows = session.query(ReportDelivery).order_by(ReportDelivery.channel).all()
        assert [(row.channel, row.status) for row in rows] == [
            ("dingtalk", "sent"),
            ("feishu", "sent"),
        ]
