"""N01-N08: message and announcement domain behavior."""

from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import (
    Announcement,
    Base,
    DataScopePolicy,
    DomainEventOutbox,
    Message,
    MessageRecipient,
    PlatformUser,
    Report,
    ReportVersion,
)
from app.messages.service import (
    AnnouncementConflict,
    create_announcement,
    consume_published_event,
    expire_announcements,
    get_user_message,
    list_user_messages,
    mark_message_read,
    publish_announcement,
    run_message_worker_once,
    serialize_message_card,
    unread_count,
    update_announcement,
    withdraw_announcement,
)


@pytest.fixture()
def message_session():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _user(user_id: str, username: str, *, enabled: bool = True) -> PlatformUser:
    return PlatformUser(
        id=user_id,
        username=username,
        password_hash="test-only",
        display_name=username,
        role="business",
        enabled=enabled,
    )


def _seed_report_event(factory):
    with factory() as session:
        report = Report(
            id="report-1",
            title="生产经营周报",
            template_id="tpl-weekly",
            source_id=1,
            status="published",
            period_start=datetime(2026, 8, 3),
            period_end=datetime(2026, 8, 10),
            current_version_id="version-1",
            scope_json={"audience": {"userIds": ["user-a", "user-b", "user-disabled", "missing"]}},
        )
        version = ReportVersion(
            id="version-1",
            report_id=report.id,
            version=1,
            status="published",
            summary="本周生产稳定。\n请关注质量波动。",
        )
        event = DomainEventOutbox(
            id="event-report",
            event_type="report.version.published",
            aggregate_id=report.id,
            idempotency_key="report.version.published:version-1",
            payload_json={"reportId": report.id, "versionId": version.id},
            status="processing",
            lease_owner="worker-a",
            attempt_count=1,
        )
        session.add_all(
            [
                _user("user-a-id", "user-a"),
                _user("user-b-id", "user-b"),
                _user("user-disabled-id", "user-disabled", enabled=False),
                report,
                version,
                event,
                DataScopePolicy(
                    id="policy-a",
                    subject_type="user",
                    subject_id="user-a",
                    business_key="weekly_report",
                    data_source_ids_json=[1],
                ),
                DataScopePolicy(
                    id="policy-b",
                    subject_type="user",
                    subject_id="user-b",
                    business_key="weekly_report",
                    data_source_ids_json=[1],
                ),
                DataScopePolicy(
                    id="policy-disabled",
                    subject_type="user",
                    subject_id="user-disabled",
                    business_key="weekly_report",
                    data_source_ids_json=[1],
                ),
            ]
        )
        session.commit()


def test_weekly_report_card_contract_needs_no_frontend_business_inference():
    message = SimpleNamespace(
        id="message-1",
        message_type="weekly_report",
        source="report-agent",
        title="生产经营周报",
        summary="本周生产稳定。\n请关注 AOI 波动。\n第三行不进入卡片摘要。",
        related_type="report",
        related_id="report-1",
        report_version_id="version-1",
        priority="high",
        is_pinned=True,
        status="published",
        valid_from=None,
        expires_at=None,
        published_at=datetime(2026, 8, 10, 9, 0),
    )
    recipient = SimpleNamespace(is_read=False, read_at=None)

    result = serialize_message_card(message, recipient, now=datetime(2026, 8, 11))

    assert result == {
        "id": "message-1",
        "type": "weekly_report",
        "sourceLabel": "周报 Agent",
        "title": "生产经营周报",
        "summaryLines": ["本周生产稳定。", "请关注 AOI 波动。"],
        "priority": "high",
        "isPinned": True,
        "status": "published",
        "isRead": False,
        "readAt": None,
        "publishedAt": "2026-08-10T09:00:00",
        "expiresAt": None,
        "primaryAction": {
            "type": "open_report",
            "label": "查看周报",
            "resourceId": "report-1",
            "versionId": "version-1",
        },
        "secondaryAction": None,
        "renderMode": "plain_text",
    }


def test_report_event_expands_only_active_authorized_recipients_idempotently(message_session):
    _seed_report_event(message_session)
    with message_session() as session:
        first = consume_published_event(
            session,
            event_id="event-report",
            worker_id="worker-a",
            now=datetime(2026, 8, 10, 9, 0),
        )
        session.commit()
        event = session.get(DomainEventOutbox, "event-report")
        event.status = "processing"
        event.lease_owner = "worker-b"
        event.attempt_count += 1
        session.commit()
        second = consume_published_event(
            session,
            event_id="event-report",
            worker_id="worker-b",
            now=datetime(2026, 8, 10, 9, 1),
        )
        session.commit()

        assert first.id == second.id
        assert session.query(Message).count() == 1
        assert {
            row.user_id for row in session.query(MessageRecipient).all()
        } == {"user-a-id", "user-b-id"}
        event = session.get(DomainEventOutbox, "event-report")
        assert event.status == "completed"
        assert event.payload_json["messageProcessing"]["skipped"] == {
            "missing": "user_not_found",
            "user-disabled": "user_disabled",
        }


def test_inbox_filter_cursor_unread_and_read_state_are_user_scoped(message_session):
    _seed_report_event(message_session)
    with message_session() as session:
        consume_published_event(
            session,
            event_id="event-report",
            worker_id="worker-a",
            now=datetime(2026, 8, 10, 9, 0),
        )
        notice = Message(
            id="message-notice",
            message_type="admin_announcement",
            source="admin",
            title="系统维护公告",
            summary="今晚进行维护。",
            related_type="announcement",
            related_id="announcement-1",
            priority="urgent",
            is_pinned=True,
            idempotency_key="announcement:announcement-1",
            published_at=datetime(2026, 8, 11, 9, 0),
        )
        session.add_all(
            [
                notice,
                MessageRecipient(
                    id="recipient-notice-a",
                    message_id=notice.id,
                    user_id="user-a",
                ),
            ]
        )
        session.commit()

        first_page = list_user_messages(
            session,
            identity_keys={"user-a-id", "user-a"},
            limit=1,
            now=datetime(2026, 8, 12),
        )
        second_page = list_user_messages(
            session,
            identity_keys={"user-a-id", "user-a"},
            limit=1,
            cursor=first_page["nextCursor"],
            now=datetime(2026, 8, 12),
        )
        reports_only = list_user_messages(
            session,
            identity_keys={"user-a-id", "user-a"},
            message_type="weekly_report",
            read=False,
            now=datetime(2026, 8, 12),
        )
        after_notice = list_user_messages(
            session,
            identity_keys={"user-a-id", "user-a"},
            published_from=datetime(2026, 8, 11),
            now=datetime(2026, 8, 12),
        )

        assert first_page["items"][0]["id"] == "message-notice"
        assert second_page["items"][0]["id"] != "message-notice"
        assert reports_only["items"][0]["type"] == "weekly_report"
        assert [item["id"] for item in after_notice["items"]] == ["message-notice"]
        assert unread_count(session, {"user-a-id", "user-a"}, now=datetime(2026, 8, 12)) == 2
        assert unread_count(session, {"user-b-id", "user-b"}, now=datetime(2026, 8, 12)) == 1

        first_read = mark_message_read(
            session,
            "message-notice",
            {"user-a-id", "user-a"},
            now=datetime(2026, 8, 12, 10, 0),
        )
        repeated = mark_message_read(
            session,
            "message-notice",
            {"user-a-id", "user-a"},
            now=datetime(2026, 8, 12, 11, 0),
        )

        assert first_read["readAt"] == repeated["readAt"] == "2026-08-12T10:00:00"
        assert unread_count(session, {"user-a-id", "user-a"}, now=datetime(2026, 8, 12)) == 1
        with pytest.raises(PermissionError):
            mark_message_read(
                session,
                "message-notice",
                {"user-b-id", "user-b"},
                now=datetime(2026, 8, 12),
            )


def test_admin_announcement_draft_publish_event_and_recipient_expansion(message_session):
    with message_session() as session:
        session.add_all([_user("user-a-id", "user-a"), _user("user-b-id", "user-b")])
        session.commit()
        draft = create_announcement(
            session,
            title="系统维护通知",
            body="今晚 22:00 至 23:00 进行维护。",
            priority="urgent",
            is_pinned=True,
            audience={"userIds": ["user-a", "user-a-id", "user-b"]},
            created_by="admin",
            valid_from=datetime(2026, 8, 13, 9, 0),
            expires_at=datetime(2026, 8, 14, 9, 0),
        )
        updated = update_announcement(
            session,
            draft.id,
            title="系统维护公告",
            body="今晚 22:00 至 23:00 进行例行维护。",
        )
        published = publish_announcement(
            session,
            updated.id,
            published_by="admin",
            now=datetime(2026, 8, 13, 9, 0),
        )
        session.commit()

        assert published.status == "published"
        event = session.query(DomainEventOutbox).filter_by(
            event_type="announcement.published"
        ).one()
        assert event.payload_json["estimatedRecipientCount"] == 2
        assert event.idempotency_key == f"announcement.published:{published.id}:r1"

        event.status = "processing"
        event.lease_owner = "worker-a"
        event.attempt_count = 1
        session.commit()
        message = consume_published_event(
            session,
            event_id=event.id,
            worker_id="worker-a",
            now=datetime(2026, 8, 13, 9, 1),
        )
        session.commit()

        assert message.message_type == "admin_announcement"
        assert message.summary == "今晚 22:00 至 23:00 进行例行维护。"
        assert {row.user_id for row in session.query(MessageRecipient).all()} == {
            "user-a-id",
            "user-b-id",
        }
        downstream = session.query(DomainEventOutbox).filter_by(
            event_type="message.published"
        ).one()
        assert downstream.payload_json["messageId"] == message.id


def test_announcement_content_and_admin_lifecycle_are_safe_and_immutable(message_session):
    with message_session() as session:
        session.add(_user("user-a-id", "user-a"))
        session.commit()
        for unsafe_body in ("<script>alert(1)</script>", "请阅读 <b>重要</b> 通知"):
            with pytest.raises(ValueError):
                create_announcement(
                    session,
                    title="危险公告",
                    body=unsafe_body,
                    audience={"userIds": ["user-a"]},
                    created_by="admin",
                )
        draft = create_announcement(
            session,
            title="安全公告",
            body="请按通知安排工作。",
            audience={"userIds": ["user-a"]},
            created_by="admin",
            expires_at=datetime(2026, 8, 14, 9, 0),
        )
        publish_announcement(
            session,
            draft.id,
            published_by="admin",
            now=datetime(2026, 8, 13, 9, 0),
        )
        with pytest.raises(AnnouncementConflict):
            update_announcement(session, draft.id, title="篡改已发布公告")
        with pytest.raises(ValueError):
            withdraw_announcement(
                session,
                draft.id,
                withdrawn_by="admin",
                reason=" ",
            )
        withdrawn = withdraw_announcement(
            session,
            draft.id,
            withdrawn_by="admin",
            reason="维护窗口取消",
            now=datetime(2026, 8, 13, 10, 0),
        )

        assert withdrawn.status == "withdrawn"
        assert withdrawn.withdrawal_reason == "维护窗口取消"
        assert session.query(DomainEventOutbox).filter_by(
            event_type="announcement.withdrawn"
        ).count() == 1


def test_expiration_updates_announcement_and_message_without_deleting_history(message_session):
    with message_session() as session:
        announcement = Announcement(
            id="announcement-expired",
            revision=1,
            title="短期公告",
            body="短期通知。",
            audience_json={"userIds": ["user-a"]},
            audience_scope_hash="scope-expired",
            status="published",
            created_by="admin",
            published_by="admin",
            published_at=datetime(2026, 8, 10),
            expires_at=datetime(2026, 8, 11),
        )
        message = Message(
            id="message-expired",
            message_type="admin_announcement",
            source="admin",
            title=announcement.title,
            summary=announcement.body,
            related_type="announcement",
            related_id=announcement.id,
            status="published",
            is_pinned=True,
            idempotency_key="announcement:expired",
        )
        session.add_all([announcement, message])
        session.commit()

        count = expire_announcements(session, now=datetime(2026, 8, 12))

        assert count == 1
        assert session.get(Announcement, announcement.id).status == "expired"
        assert session.get(Message, message.id).status == "expired"
        assert session.get(Message, message.id).is_pinned is False


def test_message_worker_filters_events_and_moves_repeated_failures_to_dead_letter(message_session):
    with message_session() as session:
        session.add_all(
            [
                DomainEventOutbox(
                    id="broken-message-event",
                    event_type="report.version.published",
                    aggregate_id="missing-report",
                    idempotency_key="broken-message-event",
                    payload_json={"reportId": "missing-report", "versionId": "missing-version"},
                ),
                DomainEventOutbox(
                    id="future-email-event",
                    event_type="message.published",
                    aggregate_id="message-1",
                    idempotency_key="future-email-event",
                ),
            ]
        )
        session.commit()

    first = run_message_worker_once(
        session_factory=message_session,
        worker_id="message-worker",
        now=datetime(2026, 8, 13, 9, 0),
        max_attempts=2,
        retry_seconds=1,
    )
    second = run_message_worker_once(
        session_factory=message_session,
        worker_id="message-worker",
        now=datetime(2026, 8, 13, 9, 2),
        max_attempts=2,
        retry_seconds=1,
    )

    with message_session() as session:
        assert first == {"processed": 0, "failed": 1}
        assert second == {"processed": 0, "failed": 1}
        assert session.get(DomainEventOutbox, "broken-message-event").status == "dead_letter"
        assert session.get(DomainEventOutbox, "future-email-event").status == "pending"


def test_weekly_message_detail_rechecks_current_report_data_scope(message_session):
    _seed_report_event(message_session)
    with message_session() as session:
        consume_published_event(
            session,
            event_id="event-report",
            worker_id="worker-a",
            now=datetime(2026, 8, 10, 9, 0),
        )
        message = session.query(Message).filter_by(message_type="weekly_report").one()
        session.get(DataScopePolicy, "policy-a").enabled = False
        session.flush()

        with pytest.raises(PermissionError):
            get_user_message(
                session,
                message.id,
                {"user-a-id", "user-a"},
                now=datetime(2026, 8, 11),
            )
