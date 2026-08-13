from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import email_verification
from app.auth.email_verification import EmailVerificationError
from app.auth.mail_provider import (
    CaptureMailProvider,
    SendResult,
    get_verification_mail_provider,
)
from app.config import settings
from app.db.models import EmailVerificationChallenge, EmailVerificationEvent, PlatformUser
from app.db.platform import Base


NOW = datetime(2026, 8, 13, 9, 0, 0)


@pytest.fixture()
def mail_session(monkeypatch):
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(email_verification, "PlatformSession", factory)
    monkeypatch.setattr(settings, "email_code_secret", "unit-test-email-secret")
    monkeypatch.setattr(settings, "email_code_resend_seconds", 60)
    monkeypatch.setattr(settings, "email_hourly_limit_email", 5)
    monkeypatch.setattr(settings, "email_hourly_limit_account", 5)
    monkeypatch.setattr(settings, "email_hourly_limit_ip", 10)
    with factory() as session:
        session.add(
            PlatformUser(
                id="u_owner",
                username="owner",
                password_hash="scrypt$test",
                display_name="owner",
                role="business",
                title="业务人员",
                enabled=True,
                created_at=NOW,
            )
        )
        session.commit()
    yield factory
    engine.dispose()


class FailingMailProvider:
    def send_code(self, to: str, code: str, purpose: str) -> SendResult:
        return SendResult(ok=False, error_category="timeout", error_summary="provider timeout")


def test_capture_provider_keeps_message_in_memory_without_exposing_code_in_repr():
    provider = CaptureMailProvider()

    result = provider.send_code("Owner@example.com", "012345", "bind")

    assert result.ok is True
    assert provider.messages[0].to == "Owner@example.com"
    assert provider.messages[0].code == "012345"
    assert "012345" not in repr(provider)
    assert "012345" not in repr(provider.messages[0])


def test_provider_factory_uses_capture_by_default(monkeypatch):
    monkeypatch.setattr(settings, "mail_provider", "capture")

    assert isinstance(get_verification_mail_provider(), CaptureMailProvider)


def test_provider_factory_rejects_unimplemented_provider(monkeypatch):
    monkeypatch.setattr(settings, "mail_provider", "smtp")

    with pytest.raises(RuntimeError, match="Unsupported MAIL_PROVIDER"):
        get_verification_mail_provider()


def test_successful_provider_send_records_sent_event(mail_session, monkeypatch):
    monkeypatch.setattr(email_verification, "generate_verification_code", lambda: "012345")
    provider = CaptureMailProvider()

    issued = email_verification.issue_email_challenge(
        "u_owner",
        "Owner@example.com",
        "bind",
        "127.0.0.1",
        now=NOW,
        provider=provider,
    )

    assert issued.code == "012345"
    assert provider.messages[0].code == "012345"
    with mail_session() as session:
        assert session.query(EmailVerificationEvent).filter_by(event_type="code_sent").count() == 1


def test_provider_failure_keeps_challenge_and_does_not_increment_resend_count(
    mail_session,
):
    provider = CaptureMailProvider()
    issued = email_verification.issue_email_challenge(
        "u_owner",
        "owner@example.com",
        "bind",
        "127.0.0.1",
        now=NOW,
        provider=provider,
    )

    with pytest.raises(EmailVerificationError) as captured:
        email_verification.issue_email_challenge(
            "u_owner",
            "owner@example.com",
            "bind",
            "127.0.0.1",
            now=NOW + timedelta(seconds=60),
            provider=FailingMailProvider(),
        )

    assert captured.value.code == "EMAIL_SEND_FAILED"
    with mail_session() as session:
        challenge = session.get(EmailVerificationChallenge, issued.challenge_id)
        assert challenge.consumed_at is None
        assert challenge.superseded_at is None
        assert challenge.send_count == 1
        failed = session.query(EmailVerificationEvent).filter_by(event_type="send_failed").one()
        assert failed.outcome == "provider_error"


def test_first_provider_failure_retains_unsent_challenge_with_zero_send_count(
    mail_session,
):
    with pytest.raises(EmailVerificationError) as captured:
        email_verification.issue_email_challenge(
            "u_owner",
            "owner@example.com",
            "bind",
            "127.0.0.1",
            now=NOW,
            provider=FailingMailProvider(),
        )

    assert captured.value.code == "EMAIL_SEND_FAILED"
    with mail_session() as session:
        challenge = session.query(EmailVerificationChallenge).one()
        assert challenge.send_count == 0
        assert challenge.code_hash is not None
