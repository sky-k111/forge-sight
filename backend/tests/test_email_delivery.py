"""E01-E07: reliable, provider-independent email delivery."""

from datetime import datetime
from pathlib import Path
from threading import Event, Thread

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import (
    Announcement,
    Base,
    DataScopePolicy,
    DomainEventOutbox,
    DeliveryAttempt,
    Message,
    MessageRecipient,
    PlatformUser,
    Report,
    ReportDelivery,
    ReportFile,
    ReportVersion,
)
from app.delivery.provider import (
    CaptureMailProvider,
    MailAttachment,
    OutgoingMail,
    ProviderFailure,
)
from app.delivery.service import (
    block_deliveries_for_configuration,
    consume_message_published_event,
    delivery_work_pending,
    run_delivery_event_worker_once,
    run_delivery_worker_once,
    resume_configuration_blocked_deliveries,
    retry_delivery,
)
from app.delivery.templates import build_announcement_mail, build_weekly_report_mail


@pytest.fixture()
def delivery_session():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _verified_user(user_id: str, email: str | None, *, enabled: bool = True):
    return PlatformUser(
        id=user_id,
        username=user_id,
        password_hash="test-only",
        display_name=user_id,
        role="business",
        enabled=enabled,
        email=email,
        email_canonical=email,
        email_verified_at=datetime(2026, 8, 1) if email else None,
    )


def _seed_announcement(factory):
    with factory() as session:
        message = Message(
            id="message-announcement",
            message_type="admin_announcement",
            source="admin",
            title="停机 <演练>",
            summary="今晚进行例行演练。",
            related_type="announcement",
            related_id="announcement-1",
            idempotency_key="inbox:announcement:1",
            published_at=datetime(2026, 8, 13, 9, 0),
        )
        session.add_all(
            [
                _verified_user("user-ok", "ok@example.com"),
                _verified_user("user-unverified", None),
                Announcement(
                    id="announcement-1",
                    revision=1,
                    title=message.title,
                    body="正文含有 & 符号，但它是纯文本。",
                    audience_json={"userIds": ["user-ok", "user-unverified"]},
                    audience_scope_hash="scope",
                    status="published",
                    created_by="admin",
                    published_by="admin",
                    published_at=message.published_at,
                ),
                message,
                MessageRecipient(
                    id="recipient-ok", message_id=message.id, user_id="user-ok"
                ),
                MessageRecipient(
                    id="recipient-unverified",
                    message_id=message.id,
                    user_id="user-unverified",
                ),
                DomainEventOutbox(
                    id="event-message",
                    event_type="message.published",
                    aggregate_id=message.id,
                    idempotency_key="message.published:message-announcement",
                    payload_json={"messageId": message.id},
                    status="processing",
                    lease_owner="event-worker",
                    attempt_count=1,
                ),
            ]
        )
        session.commit()


def _weekly_content():
    return {
        "schemaVersion": "weekly-report/v1",
        "identity": {
            "title": "生产经营周报",
            "periodStart": "2026-08-03T00:00:00",
            "periodEnd": "2026-08-10T00:00:00",
        },
        "managementSummary": {
            "findings": [
                {"level": "confirmed", "text": "产量稳定。"},
                {"level": "confirmed", "text": "良率略有提升。"},
            ],
            "actions": [],
        },
        "anomalies": [{"title": "AOI 波动", "severity": "high"}],
        "dataQuality": {"coverage": 96, "delayedSources": []},
    }


def _seed_weekly(factory, storage_root: Path, *, file_status="ready", file_size=None):
    payload = b"%PDF-1.4 test-pdf"
    path = storage_root / "version-1" / "weekly.pdf"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    with factory() as session:
        report = Report(
            id="report-1",
            title="生产经营周报",
            template_id="tpl-weekly",
            source_id=1,
            report_type="weekly",
            status="published",
            owner_id="user-ok",
            period_start=datetime(2026, 8, 3),
            period_end=datetime(2026, 8, 10),
            current_version_id="version-1",
            scope_json={"audience": {"userIds": ["user-ok"]}},
        )
        version = ReportVersion(
            id="version-1",
            report_id=report.id,
            version=1,
            status="published",
            summary="产量稳定。\n良率略有提升。",
            content_json=_weekly_content(),
            content_checksum="content-checksum",
            published_at=datetime(2026, 8, 10, 9, 0),
        )
        message = Message(
            id="message-weekly",
            message_type="weekly_report",
            source="report-agent",
            title=report.title,
            summary=version.summary,
            related_type="report",
            related_id=report.id,
            report_version_id=version.id,
            idempotency_key="inbox:weekly:1",
            published_at=version.published_at,
        )
        session.add_all(
            [
                _verified_user("user-ok", "ok@example.com"),
                report,
                version,
                message,
                MessageRecipient(
                    id="recipient-weekly", message_id=message.id, user_id="user-ok"
                ),
                DataScopePolicy(
                    id="scope-user-ok",
                    subject_type="user",
                    subject_id="user-ok",
                    business_key="weekly_report",
                    data_source_ids_json=[1],
                    dimensions_json={},
                ),
                ReportFile(
                    id="file-pdf",
                    report_id=report.id,
                    version_id=version.id,
                    format="pdf",
                    status=file_status,
                    storage_uri="version-1/weekly.pdf" if file_status == "ready" else None,
                    mime_type="application/pdf" if file_status == "ready" else None,
                    file_size=(file_size if file_size is not None else len(payload)),
                    checksum=(
                        "e611a0deef5e91a840d39e144fe86ce25a3484cabdc5c67be324f90619cd61d8"
                        if file_status == "ready"
                        else None
                    ),
                    source_content_checksum="content-checksum",
                ),
                DomainEventOutbox(
                    id="event-weekly",
                    event_type="message.published",
                    aggregate_id=message.id,
                    idempotency_key="message.published:message-weekly",
                    payload_json={"messageId": message.id},
                    status="processing",
                    lease_owner="event-worker",
                    attempt_count=1,
                ),
            ]
        )
        session.commit()


def test_templates_escape_user_content_and_include_plain_text_links():
    content = _weekly_content()
    content["anomalies"] = [
        {"title": "轻微波动", "severity": "low"},
        {"title": "关键停机", "severity": "critical"},
    ]
    weekly = build_weekly_report_mail(
        title="周报 <一>",
        period_start=datetime(2026, 8, 3),
        period_end=datetime(2026, 8, 10),
        version=2,
        content=content,
        fallback_summary="摘要",
        link="https://example.test/workspace/reports/report-1",
    )
    announcement = build_announcement_mail(
        title="公告 <重要>",
        body="内容 & 提醒",
        publisher="管理员",
        published_at=datetime(2026, 8, 13, 9, 0),
        expires_at=None,
        link="https://example.test/workspace/messages/message-1",
    )

    assert "周报 <一>" in weekly.text_body
    assert "周报 &lt;一&gt;" in weekly.html_body
    assert "https://example.test/" in weekly.text_body
    assert "关键停机" in weekly.text_body
    assert "轻微波动" not in weekly.text_body
    assert "公告 &lt;重要&gt;" in announcement.html_body
    assert "内容 &amp; 提醒" in announcement.html_body


def test_message_event_creates_one_delivery_per_recipient_and_suppresses_unverified(
    delivery_session,
):
    _seed_announcement(delivery_session)
    with delivery_session() as session:
        consume_message_published_event(
            session, "event-message", "event-worker", now=datetime(2026, 8, 13, 9, 1)
        )
        session.commit()
        event = session.get(DomainEventOutbox, "event-message")
        deliveries = session.query(ReportDelivery).order_by(ReportDelivery.recipient_user_id).all()

        assert event.status == "completed"
        assert [(row.recipient_user_id, row.status) for row in deliveries] == [
            ("user-ok", "queued"),
            ("user-unverified", "suppressed"),
        ]
        assert deliveries[0].recipient_address_snapshot == "ok@example.com"

        event.status = "processing"
        event.lease_owner = "event-worker-2"
        event.attempt_count += 1
        consume_message_published_event(session, event.id, "event-worker-2")
        session.commit()
        assert session.query(ReportDelivery).count() == 2


def test_event_worker_claims_only_message_publications(delivery_session):
    _seed_announcement(delivery_session)
    with delivery_session() as session:
        event = session.get(DomainEventOutbox, "event-message")
        event.status = "pending"
        event.lease_owner = None
        session.add(
            DomainEventOutbox(
                id="unrelated-event",
                event_type="report.file.ready",
                aggregate_id="file-1",
                idempotency_key="report.file.ready:file-1",
            )
        )
        session.commit()

    result = run_delivery_event_worker_once(
        delivery_session,
        "event-worker",
        now=datetime(2026, 8, 13, 9, 1),
    )

    assert result == {"processed": 1, "failed": 0}
    with delivery_session() as session:
        assert session.get(DomainEventOutbox, "event-message").status == "completed"
        assert session.get(DomainEventOutbox, "unrelated-event").status == "pending"


@pytest.mark.parametrize("recipient_count", [100, 1000])
def test_capacity_batch_creates_independent_idempotent_deliveries(
    delivery_session, recipient_count
):
    with delivery_session() as session:
        message = Message(
            id="message-capacity",
            message_type="admin_announcement",
            source="admin",
            title="容量通知",
            summary="容量测试",
            related_type="announcement",
            related_id="announcement-capacity",
            idempotency_key="inbox:capacity",
            published_at=datetime(2026, 8, 13, 9, 0),
        )
        session.add(
            Announcement(
                id="announcement-capacity",
                revision=1,
                title="容量通知",
                body="容量测试",
                audience_json={"userIds": []},
                audience_scope_hash="capacity",
                status="published",
                created_by="admin",
                published_by="admin",
                published_at=message.published_at,
            )
        )
        session.add(message)
        for index in range(recipient_count):
            user_id = f"capacity-{index:03d}"
            session.add(_verified_user(user_id, f"user{index}@example.com"))
            session.add(
                MessageRecipient(
                    id=f"recipient-capacity-{index:03d}",
                    message_id=message.id,
                    user_id=user_id,
                )
            )
        session.add(
            DomainEventOutbox(
                id="event-capacity",
                event_type="message.published",
                aggregate_id=message.id,
                idempotency_key="message.published:capacity",
                payload_json={"messageId": message.id},
                status="processing",
                lease_owner="capacity-worker",
                attempt_count=1,
            )
        )
        session.commit()

        assert consume_message_published_event(
            session, "event-capacity", "capacity-worker"
        ) == recipient_count
        session.commit()
        assert (
            session.query(ReportDelivery).filter_by(message_id=message.id).count()
            == recipient_count
        )


def test_weekly_delivery_waits_for_pdf_then_sends_exact_attachment(
    delivery_session, tmp_path
):
    _seed_weekly(delivery_session, tmp_path, file_status="queued")
    with delivery_session() as session:
        consume_message_published_event(session, "event-weekly", "event-worker")
        session.commit()

    provider = CaptureMailProvider()
    assert run_delivery_worker_once(
        delivery_session,
        "mail-worker",
        provider,
        storage_root=tmp_path,
        public_app_url="https://example.test/base",
        now=datetime(2026, 8, 13, 10, 0),
    )
    assert provider.messages == []
    with delivery_session() as session:
        delivery = session.query(ReportDelivery).one()
        assert delivery.status == "waiting_attachment"
        pdf = session.get(ReportFile, "file-pdf")
        pdf.status = "ready"
        pdf.storage_uri = "version-1/weekly.pdf"
        pdf.mime_type = "application/pdf"
        pdf.file_size = (tmp_path / pdf.storage_uri).stat().st_size
        import hashlib

        pdf.checksum = hashlib.sha256((tmp_path / pdf.storage_uri).read_bytes()).hexdigest()
        session.commit()

    assert run_delivery_worker_once(
        delivery_session,
        "mail-worker",
        provider,
        storage_root=tmp_path,
        public_app_url="https://example.test/base",
        now=datetime(2026, 8, 13, 10, 2),
    )
    assert len(provider.messages) == 1
    sent = provider.messages[0]
    assert sent.to == "ok@example.com"
    assert sent.attachments[0].content == b"%PDF-1.4 test-pdf"
    assert sent.attachments[0].content_type == "application/pdf"
    assert "https://example.test/base/workspace/reports/report-1" in sent.text_body


def test_oversize_pdf_is_explicitly_omitted_without_sending_excel(
    delivery_session, tmp_path
):
    _seed_weekly(delivery_session, tmp_path)
    with delivery_session() as session:
        consume_message_published_event(session, "event-weekly", "event-worker")
        session.commit()
    provider = CaptureMailProvider()

    run_delivery_worker_once(
        delivery_session,
        "mail-worker",
        provider,
        storage_root=tmp_path,
        public_app_url="https://example.test",
        attachment_max_bytes=1,
    )

    assert provider.messages[0].attachments == ()
    assert "附件大小超过邮件服务限制" in provider.messages[0].text_body
    with delivery_session() as session:
        delivery = session.query(ReportDelivery).one()
        assert delivery.status == "sent"
        assert delivery.attachment_omitted is True


def test_email_change_and_scope_revocation_are_rechecked_before_send(
    delivery_session, tmp_path
):
    _seed_weekly(delivery_session, tmp_path)
    with delivery_session() as session:
        consume_message_published_event(session, "event-weekly", "event-worker")
        user = session.get(PlatformUser, "user-ok")
        user.email = "changed@example.com"
        user.email_canonical = "changed@example.com"
        session.commit()
    provider = CaptureMailProvider()

    run_delivery_worker_once(
        delivery_session,
        "mail-worker",
        provider,
        storage_root=tmp_path,
        public_app_url="https://example.test",
    )

    assert provider.messages == []
    with delivery_session() as session:
        assert session.query(ReportDelivery).one().status == "suppressed"


def test_non_local_http_public_url_fails_without_leaking_message(
    delivery_session,
):
    _seed_announcement(delivery_session)
    with delivery_session() as session:
        consume_message_published_event(session, "event-message", "event-worker")
        session.commit()
    provider = CaptureMailProvider()

    run_delivery_worker_once(
        delivery_session,
        "mail-worker",
        provider,
        storage_root=".",
        public_app_url="http://example.test",
    )

    with delivery_session() as session:
        delivery = session.query(ReportDelivery).filter_by(recipient_user_id="user-ok").one()
        assert delivery.status == "failed"
        assert delivery.error_category == "configuration"
        assert delivery.error_json == {"message": "邮件服务配置无效"}
    assert provider.messages == []


class _FailingProvider:
    def __init__(self, failure: ProviderFailure):
        self.failure = failure

    def send(self, _message: OutgoingMail):
        raise self.failure


@pytest.mark.parametrize(
    ("retryable", "expected_status"),
    [(True, "retry_wait"), (False, "failed")],
)
def test_provider_failures_are_classified_and_attempts_are_not_overwritten(
    delivery_session, retryable, expected_status
):
    _seed_announcement(delivery_session)
    with delivery_session() as session:
        consume_message_published_event(session, "event-message", "event-worker")
        session.commit()

    provider = _FailingProvider(
        ProviderFailure("rate_limited", "provider unavailable", retryable=retryable)
    )
    run_delivery_worker_once(
        delivery_session,
        "mail-worker",
        provider,
        storage_root=".",
        public_app_url="https://example.test",
        now=datetime(2026, 8, 13, 10, 0),
    )

    with delivery_session() as session:
        delivery = session.query(ReportDelivery).filter_by(recipient_user_id="user-ok").one()
        assert delivery.status == expected_status
        assert delivery.attempt_count == 1
        assert delivery.error_category == "rate_limited"
        assert session.query(DeliveryAttempt).filter_by(delivery_id=delivery.id).count() == 1
        if retryable:
            assert delivery.next_retry_at > datetime(2026, 8, 13, 10, 0)
        else:
            assert delivery.next_retry_at is None


def test_retryable_failure_stops_after_configured_attempt_limit(delivery_session):
    _seed_announcement(delivery_session)
    with delivery_session() as session:
        consume_message_published_event(session, "event-message", "event-worker")
        session.commit()
    provider = _FailingProvider(
        ProviderFailure("network", "socket detail must not persist", retryable=True)
    )
    for attempt in range(1, 4):
        current = datetime(2026, 8, 13, 10, attempt)
        with delivery_session() as session:
            delivery = session.query(ReportDelivery).filter_by(recipient_user_id="user-ok").one()
            if delivery.status == "retry_wait":
                delivery.next_retry_at = current
                session.commit()
        run_delivery_worker_once(
            delivery_session,
            "mail-worker",
            provider,
            storage_root=".",
            public_app_url="https://example.test",
            max_attempts=3,
            now=current,
        )

    with delivery_session() as session:
        delivery = session.query(ReportDelivery).filter_by(recipient_user_id="user-ok").one()
        assert delivery.status == "failed"
        assert delivery.attempt_count == 3
        assert session.query(DeliveryAttempt).filter_by(delivery_id=delivery.id).count() == 3
        assert "socket detail" not in str(delivery.error_json)


def test_external_side_effect_then_process_exit_stays_unknown_not_auto_retry(
    delivery_session,
):
    _seed_announcement(delivery_session)
    with delivery_session() as session:
        consume_message_published_event(session, "event-message", "event-worker")
        session.commit()

    class CrashAfterSideEffect:
        def __init__(self):
            self.sent = []

        def send(self, message):
            self.sent.append(message)
            raise SystemExit("simulated process exit")

    provider = CrashAfterSideEffect()
    with pytest.raises(SystemExit):
        run_delivery_worker_once(
            delivery_session,
            "mail-worker",
            provider,
            storage_root=".",
            public_app_url="https://example.test",
        )

    assert len(provider.sent) == 1
    with delivery_session() as session:
        delivery = session.query(ReportDelivery).filter_by(recipient_user_id="user-ok").one()
        assert delivery.status == "unknown"
        assert delivery.attempt_count == 1
    assert run_delivery_worker_once(
        delivery_session,
        "another-worker",
        provider,
        storage_root=".",
        public_app_url="https://example.test",
        now=datetime(2026, 8, 14),
    ) is False
    assert len(provider.sent) == 1


def test_configuration_failure_blocks_queue_and_is_visible(delivery_session):
    _seed_announcement(delivery_session)
    with delivery_session() as session:
        consume_message_published_event(session, "event-message", "event-worker")
        blocked = block_deliveries_for_configuration(
            session, "SMTP 配置无效", configuration_fingerprint="config-a"
        )
        session.commit()

        delivery = session.query(ReportDelivery).filter_by(recipient_user_id="user-ok").one()
        assert blocked == 1
        assert delivery.status == "blocked_configuration"
        assert delivery.error_category == "configuration"
        assert "SMTP 配置无效" not in str(delivery.error_json)
        assert resume_configuration_blocked_deliveries(
            session, configuration_verified=False
        ) == 0
        assert resume_configuration_blocked_deliveries(
            session,
            configuration_verified=True,
            configuration_fingerprint="config-b",
        ) == 1
        session.commit()
        assert session.get(ReportDelivery, delivery.id).status == "queued"


def test_runtime_authentication_failure_blocks_until_configuration_changes(
    delivery_session,
):
    _seed_announcement(delivery_session)
    with delivery_session() as session:
        consume_message_published_event(session, "event-message", "event-worker")
        session.commit()

    provider = _FailingProvider(
        ProviderFailure("configuration", "SMTP authentication failed", retryable=False)
    )
    run_delivery_worker_once(
        delivery_session,
        "mail-worker",
        provider,
        storage_root=".",
        public_app_url="https://example.test",
        configuration_fingerprint="bad-config",
    )

    with delivery_session() as session:
        delivery = session.query(ReportDelivery).filter_by(recipient_user_id="user-ok").one()
        assert delivery.status == "blocked_configuration"
        assert delivery.error_category == "configuration"
        assert "authentication" not in str(delivery.error_json)
        assert resume_configuration_blocked_deliveries(
            session, configuration_verified=False
        ) == 0
        assert resume_configuration_blocked_deliveries(
            session,
            configuration_verified=True,
            configuration_fingerprint="fixed-config",
        ) == 1


def test_bad_configuration_backs_off_and_cannot_reblock_verified_work(
    delivery_session,
):
    _seed_announcement(delivery_session)
    now = datetime(2026, 8, 13, 10, 0)
    with delivery_session() as session:
        consume_message_published_event(session, "event-message", "event-worker")
        assert delivery_work_pending(
            session, configuration_fingerprint="bad-config", now=now
        ) is True
        assert block_deliveries_for_configuration(
            session,
            configuration_fingerprint="bad-config",
            now=now,
            retry_seconds=300,
        ) == 1

        assert delivery_work_pending(
            session,
            configuration_fingerprint="bad-config",
            now=datetime(2026, 8, 13, 10, 1),
        ) is False
        assert delivery_work_pending(
            session,
            configuration_fingerprint="good-config",
            now=datetime(2026, 8, 13, 10, 1),
        ) is True
        assert resume_configuration_blocked_deliveries(
            session,
            configuration_verified=True,
            configuration_fingerprint="good-config",
            now=datetime(2026, 8, 13, 10, 1),
        ) == 1

        delivery = session.query(ReportDelivery).filter_by(recipient_user_id="user-ok").one()
        assert delivery.status == "queued"
        assert delivery.error_json == {
            "verifiedConfigurationFingerprint": "good-config"
        }
        assert delivery_work_pending(
            session,
            configuration_fingerprint="bad-config",
            now=datetime(2026, 8, 13, 10, 2),
        ) is False
        assert block_deliveries_for_configuration(
            session,
            configuration_fingerprint="bad-config",
            now=datetime(2026, 8, 13, 10, 2),
        ) == 0
        assert delivery.status == "queued"


def test_verified_configuration_survives_retry_and_manual_requeue(delivery_session):
    _seed_announcement(delivery_session)
    now = datetime(2026, 8, 13, 10, 0)
    with delivery_session() as session:
        consume_message_published_event(session, "event-message", "event-worker")
        block_deliveries_for_configuration(
            session, configuration_fingerprint="bad-config", now=now
        )
        resume_configuration_blocked_deliveries(
            session,
            configuration_verified=True,
            configuration_fingerprint="good-config",
            now=now,
        )
        session.commit()

    run_delivery_worker_once(
        delivery_session,
        "good-worker",
        _FailingProvider(
            ProviderFailure("network", "temporary", retryable=True)
        ),
        storage_root=".",
        public_app_url="https://example.test",
        configuration_fingerprint="good-config",
        now=now,
    )

    with delivery_session() as session:
        delivery = session.query(ReportDelivery).filter_by(recipient_user_id="user-ok").one()
        assert delivery.status == "retry_wait"
        assert delivery.error_json["verifiedConfigurationFingerprint"] == "good-config"
        delivery.next_retry_at = now
        assert delivery_work_pending(
            session, configuration_fingerprint="bad-config", now=now
        ) is False
        assert block_deliveries_for_configuration(
            session, configuration_fingerprint="bad-config", now=now
        ) == 0

        retry_delivery(session, delivery.id, now=now)
        assert delivery.error_json == {
            "verifiedConfigurationFingerprint": "good-config"
        }
        assert block_deliveries_for_configuration(
            session, configuration_fingerprint="bad-config", now=now
        ) == 0
        assert delivery.status == "queued"


def test_verified_configuration_survives_waiting_for_pdf(delivery_session, tmp_path):
    _seed_weekly(delivery_session, tmp_path, file_status="queued")
    now = datetime(2026, 8, 13, 10, 0)
    with delivery_session() as session:
        consume_message_published_event(session, "event-weekly", "event-worker")
        block_deliveries_for_configuration(
            session, configuration_fingerprint="bad-config", now=now
        )
        resume_configuration_blocked_deliveries(
            session,
            configuration_verified=True,
            configuration_fingerprint="good-config",
            now=now,
        )
        session.commit()

    run_delivery_worker_once(
        delivery_session,
        "good-worker",
        CaptureMailProvider(),
        storage_root=tmp_path,
        public_app_url="https://example.test",
        configuration_fingerprint="good-config",
        now=now,
    )

    with delivery_session() as session:
        delivery = session.query(ReportDelivery).one()
        assert delivery.status == "waiting_attachment"
        assert delivery.error_json["verifiedConfigurationFingerprint"] == "good-config"
        delivery.next_retry_at = now
        assert delivery_work_pending(
            session, configuration_fingerprint="bad-config", now=now
        ) is False
        assert block_deliveries_for_configuration(
            session, configuration_fingerprint="bad-config", now=now
        ) == 0
        assert delivery.status == "waiting_attachment"


def test_unknown_delivery_cannot_be_retried_while_provider_call_is_in_flight(
    delivery_session,
):
    _seed_announcement(delivery_session)
    with delivery_session() as session:
        consume_message_published_event(session, "event-message", "event-worker")
        session.commit()

    entered = Event()
    release = Event()

    class BlockingProvider(CaptureMailProvider):
        def send(self, message):
            entered.set()
            assert release.wait(timeout=5)
            return super().send(message)

    provider = BlockingProvider()
    worker = Thread(
        target=run_delivery_worker_once,
        args=(delivery_session, "mail-worker", provider),
        kwargs={
            "storage_root": ".",
            "public_app_url": "https://example.test",
            "lease_seconds": 60,
            "now": datetime(2026, 8, 13, 10, 0),
        },
    )
    worker.start()
    assert entered.wait(timeout=5)
    try:
        with delivery_session() as session:
            delivery = session.query(ReportDelivery).filter_by(recipient_user_id="user-ok").one()
            assert delivery.status == "unknown"
            assert delivery.lease_expires_at == datetime(2026, 8, 13, 10, 1)
            with pytest.raises(ValueError, match="仍在投递"):
                retry_delivery(
                    session,
                    delivery.id,
                    confirm_unknown=True,
                    now=datetime(2026, 8, 13, 10, 0, 30),
                )
    finally:
        release.set()
        worker.join(timeout=5)

    assert not worker.is_alive()
    assert len(provider.messages) == 1
    with delivery_session() as session:
        delivery = session.query(ReportDelivery).filter_by(recipient_user_id="user-ok").one()
        assert delivery.status == "sent"


def test_expired_unknown_delivery_requires_explicit_confirmation(delivery_session):
    _seed_announcement(delivery_session)
    with delivery_session() as session:
        consume_message_published_event(session, "event-message", "event-worker")
        delivery = session.query(ReportDelivery).filter_by(recipient_user_id="user-ok").one()
        delivery.status = "unknown"
        delivery.lease_expires_at = datetime(2026, 8, 13, 10, 0)
        session.commit()

        with pytest.raises(ValueError, match="确认投递结果"):
            retry_delivery(
                session, delivery.id, now=datetime(2026, 8, 13, 10, 1)
            )
        retry_delivery(
            session,
            delivery.id,
            confirm_unknown=True,
            now=datetime(2026, 8, 13, 10, 1),
        )
        assert delivery.status == "queued"


def test_mail_value_objects_do_not_expose_attachment_content_in_repr():
    attachment = MailAttachment("weekly.pdf", "application/pdf", b"secret-pdf")
    mail = OutgoingMail(
        to="user@example.com",
        subject="subject",
        text_body="private body",
        html_body="<p>private body</p>",
        attachments=(attachment,),
    )

    assert "secret-pdf" not in repr(mail)
    assert "private body" not in repr(mail)
