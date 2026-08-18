from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import email_verification
from app.auth.email_verification import EmailVerificationError
from app.config import settings
from app.db.models import (
    EmailVerificationChallenge,
    EmailVerificationEvent,
    PlatformUser,
)
from app.db.platform import Base

NOW = datetime(2026, 8, 13, 8, 0, 0)


@pytest.fixture()
def email_session(monkeypatch):
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
    monkeypatch.setattr(settings, "email_code_ttl_seconds", 600)
    monkeypatch.setattr(settings, "email_code_resend_seconds", 60)
    monkeypatch.setattr(settings, "email_code_max_attempts", 5)
    monkeypatch.setattr(settings, "email_hourly_limit_email", 5)
    monkeypatch.setattr(settings, "email_hourly_limit_account", 5)
    monkeypatch.setattr(settings, "email_hourly_limit_ip", 10)
    yield factory
    engine.dispose()


def _add_user(factory, user_id: str, username: str, **overrides) -> None:
    values = {
        "id": user_id,
        "username": username,
        "password_hash": "scrypt$test",
        "display_name": username,
        "role": "business",
        "title": "业务人员",
        "enabled": True,
        "created_at": NOW,
    }
    values.update(overrides)
    with factory() as session:
        session.add(PlatformUser(**values))
        session.commit()


def _assert_error(code: str, function, *args, **kwargs) -> EmailVerificationError:
    with pytest.raises(EmailVerificationError) as captured:
        function(*args, **kwargs)
    assert captured.value.code == code
    return captured.value


def test_normalizes_canonicalizes_and_masks_ascii_email():
    deliverable = email_verification.normalize_email("  Demo.User+tag@Example.COM　")

    assert deliverable == "Demo.User+tag@example.com"
    assert email_verification.canonicalize_email(deliverable) == "demo.user+tag@example.com"
    assert email_verification.mask_email(deliverable) == "D***@example.com"


@pytest.mark.parametrize(
    "raw",
    ["not-an-email", ".demo@example.com", "demo..user@example.com", "用户@example.com"],
)
def test_rejects_invalid_or_non_ascii_email(raw):
    _assert_error("AUTH_INVALID_EMAIL_FORMAT", email_verification.normalize_email, raw)


def test_generates_a_zero_padded_six_digit_code(monkeypatch):
    monkeypatch.setattr(email_verification.secrets, "randbelow", lambda _limit: 7)

    assert email_verification.generate_verification_code() == "000007"


def test_issue_stores_only_hash_and_reuses_challenge_after_cooldown(
    email_session, monkeypatch
):
    codes = iter(("123456", "654321"))
    monkeypatch.setattr(email_verification, "generate_verification_code", lambda: next(codes))
    _add_user(email_session, "u_owner", "owner")
    first = email_verification.issue_email_challenge(
        "u_owner", "Owner@example.com", "bind", "127.0.0.1", now=NOW
    )
    second = email_verification.issue_email_challenge(
        "u_owner",
        "owner@example.com",
        "bind",
        "127.0.0.1",
        now=NOW + timedelta(seconds=60),
    )

    assert first.challenge_id == second.challenge_id
    assert (first.code, second.code) == ("123456", "654321")
    assert "654321" not in repr(second)
    with email_session() as session:
        row = session.get(EmailVerificationChallenge, first.challenge_id)
        assert row.code_hash not in {first.code, second.code}
        assert row.send_count == 2
        assert session.query(EmailVerificationChallenge).count() == 1


def test_issue_rejects_resend_during_cooldown(email_session):
    _add_user(email_session, "u_owner", "owner")
    email_verification.issue_email_challenge(
        "u_owner", "owner@example.com", "bind", "127.0.0.1", now=NOW
    )

    error = _assert_error(
        "CHALLENGE_RESEND_COOLDOWN",
        email_verification.issue_email_challenge,
        "u_owner",
        "owner@example.com",
        "bind",
        "127.0.0.1",
        now=NOW + timedelta(seconds=30),
    )

    assert error.retry_after_seconds == 30


def test_new_target_supersedes_previous_active_challenge(email_session):
    _add_user(email_session, "u_owner", "owner")
    first = email_verification.issue_email_challenge(
        "u_owner", "first@example.com", "bind", "127.0.0.1", now=NOW
    )
    second = email_verification.issue_email_challenge(
        "u_owner", "second@example.com", "bind", "127.0.0.2", now=NOW + timedelta(seconds=1)
    )

    with email_session() as session:
        old_row = session.get(EmailVerificationChallenge, first.challenge_id)
        new_row = session.get(EmailVerificationChallenge, second.challenge_id)
        assert old_row.superseded_at == NOW + timedelta(seconds=1)
        assert new_row.superseded_at is None


def test_account_hourly_send_limit_is_enforced(email_session, monkeypatch):
    monkeypatch.setattr(settings, "email_hourly_limit_account", 2)
    _add_user(email_session, "u_owner", "owner")
    email_verification.issue_email_challenge(
        "u_owner", "owner@example.com", "bind", "10.0.0.1", now=NOW
    )
    email_verification.issue_email_challenge(
        "u_owner", "owner@example.com", "bind", "10.0.0.1", now=NOW + timedelta(seconds=61)
    )

    _assert_error(
        "CHALLENGE_RATE_LIMITED",
        email_verification.issue_email_challenge,
        "u_owner",
        "owner@example.com",
        "bind",
        "10.0.0.1",
        now=NOW + timedelta(seconds=122),
    )


def test_email_and_ip_hourly_send_limits_are_enforced(email_session, monkeypatch):
    monkeypatch.setattr(settings, "email_hourly_limit_email", 2)
    monkeypatch.setattr(settings, "email_hourly_limit_ip", 2)
    for index in range(1, 4):
        _add_user(email_session, f"u_{index}", f"user{index}")

    email_verification.issue_email_challenge(
        "u_1", "shared@example.com", "bind", "10.0.0.1", now=NOW
    )
    email_verification.issue_email_challenge(
        "u_2", "shared@example.com", "bind", "10.0.0.2", now=NOW + timedelta(seconds=1)
    )
    _assert_error(
        "CHALLENGE_RATE_LIMITED",
        email_verification.issue_email_challenge,
        "u_3",
        "shared@example.com",
        "bind",
        "10.0.0.3",
        now=NOW + timedelta(seconds=2),
    )

    monkeypatch.setattr(settings, "email_hourly_limit_email", 5)
    email_verification.issue_email_challenge(
        "u_1",
        "another@example.com",
        "bind",
        "10.0.0.1",
        now=NOW + timedelta(seconds=3),
    )
    _assert_error(
        "CHALLENGE_RATE_LIMITED",
        email_verification.issue_email_challenge,
        "u_3",
        "third@example.com",
        "bind",
        "10.0.0.1",
        now=NOW + timedelta(seconds=4),
    )


def test_wrong_code_reaches_attempt_limit(email_session):
    _add_user(email_session, "u_owner", "owner")
    issued = email_verification.issue_email_challenge(
        "u_owner", "owner@example.com", "bind", "127.0.0.1", now=NOW
    )

    for attempt in range(1, 6):
        expected = "CODE_ATTEMPTS_EXCEEDED" if attempt == 5 else "CODE_INVALID"
        _assert_error(
            expected,
            email_verification.verify_email_challenge,
            "u_owner",
            "owner@example.com",
            "999999",
            "127.0.0.1",
            now=NOW + timedelta(seconds=attempt),
        )

    with email_session() as session:
        row = session.get(EmailVerificationChallenge, issued.challenge_id)
        assert row.attempt_count == 5


def test_expired_code_is_rejected(email_session):
    _add_user(email_session, "u_owner", "owner")
    issued = email_verification.issue_email_challenge(
        "u_owner", "owner@example.com", "bind", "127.0.0.1", now=NOW
    )

    _assert_error(
        "CODE_EXPIRED",
        email_verification.verify_email_challenge,
        "u_owner",
        "owner@example.com",
        issued.code,
        "127.0.0.1",
        now=NOW + timedelta(seconds=601),
    )


def test_verification_attempts_share_the_ip_hourly_limit(email_session, monkeypatch):
    monkeypatch.setattr(settings, "email_hourly_limit_ip", 2)
    _add_user(email_session, "u_owner", "owner")
    email_verification.issue_email_challenge(
        "u_owner", "owner@example.com", "bind", "127.0.0.1", now=NOW
    )
    _assert_error(
        "CODE_INVALID",
        email_verification.verify_email_challenge,
        "u_owner",
        "owner@example.com",
        "999999",
        "127.0.0.1",
        now=NOW + timedelta(seconds=1),
    )

    _assert_error(
        "CHALLENGE_RATE_LIMITED",
        email_verification.verify_email_challenge,
        "u_owner",
        "owner@example.com",
        "999999",
        "127.0.0.1",
        now=NOW + timedelta(seconds=2),
    )


def test_success_updates_user_consumes_challenge_and_blocks_replay(email_session):
    _add_user(email_session, "u_owner", "owner")
    issued = email_verification.issue_email_challenge(
        "u_owner", "Owner@example.com", "bind", "127.0.0.1", now=NOW
    )

    user = email_verification.verify_email_challenge(
        "u_owner",
        "owner@example.com",
        issued.code,
        "127.0.0.1",
        now=NOW + timedelta(seconds=10),
    )

    assert user.email == "Owner@example.com"
    assert user.email_canonical == "owner@example.com"
    assert user.email_verified_at == NOW + timedelta(seconds=10)
    with email_session() as session:
        row = session.get(EmailVerificationChallenge, issued.challenge_id)
        assert row.consumed_at == NOW + timedelta(seconds=10)
        assert row.code_hash is None
        assert session.query(EmailVerificationEvent).filter_by(event_type="verify_success").count() == 1

    _assert_error(
        "CODE_INVALID",
        email_verification.verify_email_challenge,
        "u_owner",
        "owner@example.com",
        issued.code,
        "127.0.0.1",
        now=NOW + timedelta(seconds=11),
    )


def test_verify_detects_email_claimed_after_challenge_was_issued(email_session):
    _add_user(email_session, "u_first", "first")
    _add_user(email_session, "u_second", "second")
    first = email_verification.issue_email_challenge(
        "u_first", "shared@example.com", "bind", "10.0.0.1", now=NOW
    )
    second = email_verification.issue_email_challenge(
        "u_second", "shared@example.com", "bind", "10.0.0.2", now=NOW
    )
    email_verification.verify_email_challenge(
        "u_first",
        "shared@example.com",
        first.code,
        "10.0.0.1",
        now=NOW + timedelta(seconds=1),
    )

    _assert_error(
        "EMAIL_TAKEN",
        email_verification.verify_email_challenge,
        "u_second",
        "shared@example.com",
        second.code,
        "10.0.0.2",
        now=NOW + timedelta(seconds=2),
    )
    with email_session() as session:
        row = session.get(EmailVerificationChallenge, second.challenge_id)
        assert row.consumed_at is None
        assert row.code_hash is not None


def test_issue_rejects_email_owned_by_another_user(email_session):
    _add_user(
        email_session,
        "u_existing",
        "existing",
        email="Existing@example.com",
        email_canonical="existing@example.com",
        email_verified_at=NOW,
    )
    _add_user(email_session, "u_owner", "owner")

    _assert_error(
        "EMAIL_TAKEN",
        email_verification.issue_email_challenge,
        "u_owner",
        "existing@example.com",
        "bind",
        "127.0.0.1",
        now=NOW,
    )
