"""周报领域仓储的权限解析、原子发布和租约行为。"""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import (
    Base,
    DataScopePolicy,
    DomainEventOutbox,
    Message,
    OrganizationUnit,
    Report,
    ReportFile,
    ReportDelivery,
    ReportVersion,
    UserMembership,
)
from app.reports.domain import PublishedVersionImmutable
from app.reports.repository import (
    claim_outbox_events,
    complete_outbox_event,
    fail_outbox_event,
    get_or_create_message,
    get_or_create_delivery,
    get_or_create_report,
    publish_version,
    replace_current_version,
    recover_dead_letter_event,
    resolve_audience_scope,
    update_report_file,
    update_report_version,
)


@pytest.fixture()
def session_factory():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _add_report(session):
    report = Report(
        id="report-1",
        title="经营周报",
        template_id="tpl-weekly",
        source_id=1,
        period_start=datetime(2026, 8, 3),
        period_end=datetime(2026, 8, 10),
    )
    version = ReportVersion(
        id="version-1",
        report_id=report.id,
        version=1,
        status="composing",
        content_json={"title": "经营周报"},
        data_snapshot_json={"sourceId": 1},
    )
    report.current_version_id = version.id
    session.add_all([report, version])


def test_scope_resolver_handles_explicit_org_role_and_position(session_factory):
    with session_factory() as session:
        session.add_all(
            [
                OrganizationUnit(id="company", code="company", name="企业", node_type="enterprise"),
                OrganizationUnit(
                    id="east", code="east", name="华东", node_type="region", parent_id="company"
                ),
                OrganizationUnit(
                    id="quality", code="quality", name="质量部", node_type="department", parent_id="east"
                ),
                UserMembership(
                    id="member-1",
                    user_id="user-1",
                    organization_id="quality",
                    position_code="manager",
                    business_role="quality_owner",
                ),
                UserMembership(
                    id="member-2",
                    user_id="user-2",
                    organization_id="east",
                    position_code="analyst",
                    business_role="analyst",
                ),
                DataScopePolicy(
                    id="policy-1",
                    subject_type="organization",
                    subject_id="quality",
                    business_key="weekly_report",
                    data_source_ids_json=[1],
                    dimensions_json={"department": ["quality"]},
                ),
                DataScopePolicy(
                    id="policy-2",
                    subject_type="business_role",
                    subject_id="analyst",
                    business_key="weekly_report",
                    data_source_ids_json=[2],
                    dimensions_json={"region": ["east"]},
                ),
                DataScopePolicy(
                    id="policy-3",
                    subject_type="organization",
                    subject_id="company",
                    business_key="weekly_report",
                    data_source_ids_json=[3],
                    dimensions_json={"enterprise": ["company"]},
                ),
            ]
        )
        session.commit()

        resolved = resolve_audience_scope(
            session,
            "weekly_report",
            {
                "userIds": ["user-direct"],
                "organizationIds": ["company"],
                "businessRoles": ["analyst"],
                "positions": ["manager"],
            },
        )

        assert resolved.user_ids == ("user-1", "user-2", "user-direct")
        assert resolved.data_source_ids == (1, 2, 3)
        assert resolved.dimensions == {
            "department": ("quality",),
            "enterprise": ("company",),
            "region": ("east",),
        }


def test_publish_version_and_outbox_commit_as_one_transaction(session_factory):
    with session_factory() as session:
        _add_report(session)
        session.commit()

        publish_version(
            session,
            report_id="report-1",
            version_id="version-1",
            event_id="event-1",
            idempotency_key="report.published:version-1",
        )
        session.commit()

        assert session.get(Report, "report-1").status == "published"
        assert session.get(ReportVersion, "version-1").status == "published"
        assert session.get(DomainEventOutbox, "event-1").payload_json == {
            "reportId": "report-1",
            "versionId": "version-1",
        }


def test_replacing_current_version_sets_supersession_atomically(session_factory):
    with session_factory() as session:
        _add_report(session)
        session.flush()
        session.get(ReportVersion, "version-1").status = "published"
        replacement = ReportVersion(
            id="version-2",
            report_id="report-1",
            version=2,
            status="composing",
        )
        session.add(replacement)
        session.flush()

        replace_current_version(session, "report-1", "version-2")

        assert session.get(Report, "report-1").current_version_id == "version-2"
        assert replacement.supersedes_version_id == "version-1"


def test_published_version_and_ready_file_reject_content_replacement(session_factory):
    with session_factory() as session:
        _add_report(session)
        file = ReportFile(
            id="file-1",
            report_id="report-1",
            version_id="version-1",
            format="pdf",
            status="ready",
            checksum="checksum-1",
        )
        session.add(file)
        session.flush()
        session.get(ReportVersion, "version-1").status = "published"

        with pytest.raises(PublishedVersionImmutable):
            update_report_version(session, "version-1", content_json={"changed": True})
        with pytest.raises(PublishedVersionImmutable):
            update_report_file(session, "file-1", checksum="checksum-2")


def test_message_creation_is_idempotent(session_factory):
    with session_factory() as session:
        first = get_or_create_message(
            session,
            id="message-1",
            idempotency_key="report.published:version-1",
            message_type="weekly_report",
            source="report-agent",
            title="经营周报",
            report_version_id="version-1",
        )
        second = get_or_create_message(
            session,
            id="message-2",
            idempotency_key="report.published:version-1",
            message_type="weekly_report",
            source="report-agent",
            title="重复调用",
            report_version_id="version-1",
        )

        assert first.id == second.id == "message-1"
        assert session.query(Message).count() == 1


def test_report_and_delivery_creation_are_idempotent(session_factory):
    with session_factory() as session:
        report_values = {
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
        first_report = get_or_create_report(session, id="report-1", **report_values)
        second_report = get_or_create_report(session, id="report-2", **report_values)

        delivery_values = {
            "report_id": first_report.id,
            "version_id": "version-1",
            "recipient_user_id": "user-1",
            "recipient_address_snapshot": "user@example.com",
            "idempotency_key": "delivery:version-1:user-1:email",
            "channel": "email",
        }
        first_delivery = get_or_create_delivery(session, id="delivery-1", **delivery_values)
        second_delivery = get_or_create_delivery(session, id="delivery-2", **delivery_values)

        assert first_report.id == second_report.id == "report-1"
        assert first_delivery.id == second_delivery.id == "delivery-1"
        assert session.query(Report).count() == 1
        assert session.query(ReportDelivery).count() == 1


def test_outbox_lease_prevents_double_claim_until_expired(session_factory):
    now = datetime(2026, 8, 13, 9, 0)
    with session_factory() as session:
        session.add(
            DomainEventOutbox(
                id="event-1",
                event_type="report.published",
                aggregate_id="report-1",
                idempotency_key="report.published:version-1",
            )
        )
        session.commit()

        first = claim_outbox_events(session, "worker-a", now=now, lease_seconds=30)
        session.commit()
        blocked = claim_outbox_events(session, "worker-b", now=now + timedelta(seconds=10))
        reclaimed = claim_outbox_events(session, "worker-b", now=now + timedelta(seconds=31))

        assert [event.id for event in first] == ["event-1"]
        assert blocked == []
        assert [event.id for event in reclaimed] == ["event-1"]
        assert reclaimed[0].lease_owner == "worker-b"


def test_outbox_failure_backoff_completion_and_dead_letter_recovery(session_factory):
    now = datetime(2026, 8, 13, 9, 0)
    with session_factory() as session:
        session.add(
            DomainEventOutbox(
                id="event-1",
                event_type="report.published",
                aggregate_id="report-1",
                idempotency_key="report.published:version-1",
            )
        )
        session.commit()
        claim_outbox_events(session, "worker-a", now=now)

        failed = fail_outbox_event(
            session,
            "event-1",
            "worker-a",
            "temporary provider failure",
            now=now,
            max_attempts=2,
            retry_seconds=30,
        )
        assert failed.status == "retry_wait"
        assert failed.next_retry_at == now + timedelta(seconds=30)

        claim_outbox_events(session, "worker-a", now=now + timedelta(seconds=31))
        dead = fail_outbox_event(
            session,
            "event-1",
            "worker-a",
            "permanent failure",
            now=now + timedelta(seconds=31),
            max_attempts=2,
        )
        assert dead.status == "dead_letter"

        recovered = recover_dead_letter_event(session, "event-1")
        assert recovered.status == "pending"
        claim_outbox_events(session, "worker-b", now=now + timedelta(seconds=32))
        completed = complete_outbox_event(
            session, "event-1", "worker-b", now=now + timedelta(seconds=33)
        )
        assert completed.status == "completed"
        assert completed.acknowledged_at == now + timedelta(seconds=33)
