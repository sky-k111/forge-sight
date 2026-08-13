"""周报、消息、组织权限与 Outbox 的持久化约束。"""

from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import (
    Announcement,
    Base,
    DataScopePolicy,
    DeliveryAttempt,
    DomainEventOutbox,
    Message,
    MessageRecipient,
    OrganizationUnit,
    Report,
    ReportDelivery,
    ReportFile,
    ReportSubscription,
    ReportVersion,
    UserMembership,
)


@pytest.fixture()
def domain_session():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _report(report_id: str, **overrides) -> Report:
    values = {
        "id": report_id,
        "title": "经营周报",
        "template_id": "tpl-weekly",
        "source_id": 1,
        "period_start": datetime(2026, 8, 3),
        "period_end": datetime(2026, 8, 10),
        "subscription_id": "sub-1",
        "schedule_key": "2026-W32",
        "audience_scope_hash": "scope-a",
        "publish_source": "report-agent",
    }
    values.update(overrides)
    return Report(**values)


def test_scheduled_report_idempotency_is_enforced_by_database(domain_session):
    with domain_session() as session:
        session.add(_report("report-1"))
        session.commit()
        session.add(_report("report-2"))

        with pytest.raises(IntegrityError):
            session.commit()


def test_report_version_keeps_generation_and_supersession_snapshots(domain_session):
    with domain_session() as session:
        session.add(_report("report-1"))
        session.add_all(
            [
                ReportVersion(
                    id="version-1",
                    report_id="report-1",
                    version=1,
                    status="published",
                    validation_json={"ok": True},
                    workflow_version="weekly-v1",
                    model_snapshot_json={"model": "chat"},
                    data_quality_json={"coverage": 96},
                ),
                ReportVersion(
                    id="version-2",
                    report_id="report-1",
                    version=2,
                    supersedes_version_id="version-1",
                ),
            ]
        )
        session.commit()

        version = session.get(ReportVersion, "version-2")
        assert version.supersedes_version_id == "version-1"
        assert session.get(ReportVersion, "version-1").validation_json == {"ok": True}


def test_one_file_per_version_and_format(domain_session):
    with domain_session() as session:
        session.add_all(
            [
                ReportFile(id="file-1", report_id="report-1", version_id="version-1", format="pdf"),
                ReportFile(id="file-2", report_id="report-1", version_id="version-1", format="pdf"),
            ]
        )

        with pytest.raises(IntegrityError):
            session.commit()


def test_message_recipient_is_unique_per_user(domain_session):
    with domain_session() as session:
        session.add(
            Message(
                id="message-1",
                message_type="weekly_report",
                source="report-agent",
                title="第 32 周经营周报",
                report_version_id="version-1",
                idempotency_key="report.published:version-1",
            )
        )
        session.add_all(
            [
                MessageRecipient(id="recipient-1", message_id="message-1", user_id="user-1"),
                MessageRecipient(id="recipient-2", message_id="message-1", user_id="user-1"),
            ]
        )

        with pytest.raises(IntegrityError):
            session.commit()


def test_announcement_preserves_published_audience_snapshot(domain_session):
    with domain_session() as session:
        session.add(
            Announcement(
                id="announcement-1",
                title="系统维护公告",
                body="本周六进行维护。",
                revision=1,
                status="published",
                audience_json={"organizationIds": ["org-east"]},
                audience_scope_hash="audience-1",
                created_by="admin-1",
                published_by="admin-1",
            )
        )
        session.commit()

        announcement = session.get(Announcement, "announcement-1")
        assert announcement.audience_json == {"organizationIds": ["org-east"]}
        assert announcement.audience_scope_hash == "audience-1"


def test_new_subscription_defaults_to_email_auto_publish(domain_session):
    with domain_session() as session:
        subscription = ReportSubscription(
            id="sub-1",
            name="经营周报",
            template_id="tpl-weekly",
            source_id=1,
        )
        session.add(subscription)
        session.commit()

        assert subscription.channel == "email"
        assert subscription.publish_policy == "auto_publish"


def test_delivery_is_unique_per_version_user_and_channel(domain_session):
    with domain_session() as session:
        session.add_all(
            [
                ReportDelivery(
                    id="delivery-1",
                    report_id="report-1",
                    version_id="version-1",
                    recipient_user_id="user-1",
                    recipient_address_snapshot="user@example.com",
                    idempotency_key="delivery:version-1:user-1:email",
                    channel="email",
                ),
                ReportDelivery(
                    id="delivery-2",
                    report_id="report-1",
                    version_id="version-1",
                    recipient_user_id="user-1",
                    recipient_address_snapshot="user@example.com",
                    idempotency_key="delivery:duplicate-key",
                    channel="email",
                ),
            ]
        )

        with pytest.raises(IntegrityError):
            session.commit()


def test_delivery_attempts_are_separate_from_business_delivery(domain_session):
    with domain_session() as session:
        delivery = ReportDelivery(
            id="delivery-1",
            report_id="report-1",
            version_id="version-1",
            recipient_user_id="user-1",
            recipient_address_snapshot="user@example.com",
            idempotency_key="delivery:version-1:user-1:email",
            channel="email",
        )
        session.add(delivery)
        session.add_all(
            [
                DeliveryAttempt(id="attempt-1", delivery_id=delivery.id, attempt_number=1, status="failed"),
                DeliveryAttempt(id="attempt-2", delivery_id=delivery.id, attempt_number=2, status="sent"),
            ]
        )
        session.commit()

        assert session.query(DeliveryAttempt).filter_by(delivery_id=delivery.id).count() == 2


def test_organization_membership_and_data_scope_are_persisted(domain_session):
    with domain_session() as session:
        session.add_all(
            [
                OrganizationUnit(id="org-company", code="company", name="示例企业", node_type="enterprise"),
                OrganizationUnit(
                    id="org-east",
                    code="east",
                    name="华东区域",
                    node_type="region",
                    parent_id="org-company",
                ),
                UserMembership(
                    id="membership-1",
                    user_id="user-1",
                    organization_id="org-east",
                    position_code="manager",
                    business_role="regional_manager",
                ),
                DataScopePolicy(
                    id="policy-1",
                    subject_type="organization",
                    subject_id="org-east",
                    business_key="weekly_report",
                    data_source_ids_json=[1],
                    dimensions_json={"region": ["east"]},
                ),
            ]
        )
        session.commit()

        policy = session.get(DataScopePolicy, "policy-1")
        assert policy.dimensions_json == {"region": ["east"]}


def test_outbox_business_key_is_unique(domain_session):
    with domain_session() as session:
        session.add_all(
            [
                DomainEventOutbox(
                    id="event-1",
                    event_type="report.published",
                    aggregate_id="report-1",
                    idempotency_key="report.published:version-1",
                    payload_json={"versionId": "version-1"},
                ),
                DomainEventOutbox(
                    id="event-2",
                    event_type="report.published",
                    aggregate_id="report-1",
                    idempotency_key="report.published:version-1",
                    payload_json={"versionId": "version-1"},
                ),
            ]
        )

        with pytest.raises(IntegrityError):
            session.commit()
