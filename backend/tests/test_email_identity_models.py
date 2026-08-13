"""Q02 邮箱身份模型约束和 SQLite 兼容性测试。"""

from datetime import datetime

import pytest
from sqlalchemy import create_engine, event, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.db.models import (
    Base,
    EmailVerificationChallenge,
    EmailVerificationEvent,
    PlatformUser,
)


@pytest.fixture()
def identity_session():
    engine = create_engine("sqlite+pysqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _user(user_id: str, username: str, **overrides) -> PlatformUser:
    values = {
        "id": user_id,
        "username": username,
        "password_hash": "scrypt$test",
        "display_name": username,
        "role": "business",
        "title": "业务人员",
        "enabled": True,
    }
    values.update(overrides)
    return PlatformUser(**values)


def _challenge(challenge_id: str, user_id: str, **overrides) -> EmailVerificationChallenge:
    sent_at = datetime(2026, 8, 13, 6, 0, 0)
    values = {
        "id": challenge_id,
        "user_id": user_id,
        "purpose": "bind",
        "email": "Owner@example.com",
        "email_canonical": "owner@example.com",
        "code_hash": "a" * 64,
        "expires_at": datetime(2026, 8, 13, 6, 10, 0),
        "attempt_count": 0,
        "send_count": 1,
        "last_sent_at": sent_at,
        "created_at": sent_at,
    }
    values.update(overrides)
    return EmailVerificationChallenge(**values)


def test_platform_user_persists_email_identity_fields(identity_session):
    verified_at = datetime(2026, 8, 13, 6, 0, 0)
    with identity_session() as session:
        session.add(
            _user(
                "u_email",
                "email-user",
                email="CaseSensitive@example.com",
                email_canonical="casesensitive@example.com",
                email_verified_at=verified_at,
            )
        )
        session.commit()
        stored = session.get(PlatformUser, "u_email")

    assert stored.email == "CaseSensitive@example.com"
    assert stored.email_canonical == "casesensitive@example.com"
    assert stored.email_verified_at == verified_at


@pytest.mark.parametrize(
    ("email", "canonical"),
    [
        ("demo@example.com", None),
        (None, "demo@example.com"),
    ],
)
def test_platform_user_rejects_half_populated_email_identity(
    identity_session,
    email,
    canonical,
):
    with identity_session() as session:
        session.add(
            _user(
                "u_incomplete",
                "incomplete-user",
                email=email,
                email_canonical=canonical,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_platform_user_rejects_verified_timestamp_without_email(identity_session):
    with identity_session() as session:
        session.add(
            _user(
                "u_invalid_verified",
                "invalid-verified",
                email_verified_at=datetime(2026, 8, 13, 6, 0, 0),
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_platform_user_rejects_duplicate_canonical_email(identity_session):
    with identity_session() as session:
        session.add_all(
            [
                _user(
                    "u_first",
                    "first-user",
                    email="Owner@example.com",
                    email_canonical="owner@example.com",
                ),
                _user(
                    "u_second",
                    "second-user",
                    email="owner@EXAMPLE.com",
                    email_canonical="owner@example.com",
                ),
            ]
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_email_identity_tables_are_created(identity_session):
    table_names = set(inspect(identity_session.kw["bind"]).get_table_names())

    assert "email_verification_challenge" in table_names
    assert "email_verification_event" in table_names


def test_email_challenge_persists_contract_fields(identity_session):
    with identity_session() as session:
        session.add(_user("u_owner", "owner"))
        session.commit()
        session.add(_challenge("c_valid", "u_owner"))
        session.commit()
        stored = session.get(EmailVerificationChallenge, "c_valid")

    assert stored.email == "Owner@example.com"
    assert stored.email_canonical == "owner@example.com"
    assert stored.attempt_count == 0
    assert stored.send_count == 1


def test_email_challenge_rejects_two_active_rows_for_one_user(identity_session):
    with identity_session() as session:
        session.add(_user("u_owner", "owner"))
        session.commit()
        session.add(_challenge("c_first", "u_owner"))
        session.commit()
        session.add(
            _challenge(
                "c_second",
                "u_owner",
                email="other@example.com",
                email_canonical="other@example.com",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_email_challenge_allows_replacement_after_supersede(identity_session):
    superseded_at = datetime(2026, 8, 13, 6, 1, 0)
    with identity_session() as session:
        session.add(_user("u_owner", "owner"))
        session.commit()
        session.add(_challenge("c_first", "u_owner", superseded_at=superseded_at))
        session.add(
            _challenge(
                "c_second",
                "u_owner",
                email="other@example.com",
                email_canonical="other@example.com",
            )
        )
        session.commit()
        stored_ids = {
            item.id for item in session.query(EmailVerificationChallenge).all()
        }

    assert stored_ids == {"c_first", "c_second"}


def test_email_challenge_rejects_active_row_without_hash(identity_session):
    with identity_session() as session:
        session.add(_user("u_owner", "owner"))
        session.commit()
        session.add(_challenge("c_no_hash", "u_owner", code_hash=None))
        with pytest.raises(IntegrityError):
            session.commit()


@pytest.mark.parametrize(
    ("field", "value"),
    [("attempt_count", -1), ("send_count", -1)],
)
def test_email_challenge_rejects_negative_counters(identity_session, field, value):
    with identity_session() as session:
        session.add(_user("u_owner", "owner"))
        session.commit()
        session.add(_challenge("c_bad_count", "u_owner", **{field: value}))
        with pytest.raises(IntegrityError):
            session.commit()


def test_email_challenge_rejects_unknown_user(identity_session):
    with identity_session() as session:
        session.add(_challenge("c_orphan", "u_missing"))
        with pytest.raises(IntegrityError):
            session.commit()


def test_email_event_rejects_unknown_user(identity_session):
    with identity_session() as session:
        session.add(
            EmailVerificationEvent(
                id="e_orphan",
                user_id="u_missing",
                email_canonical="owner@example.com",
                purpose="bind",
                event_type="code_sent",
                outcome="sent",
                ip="127.0.0.1",
                created_at=datetime(2026, 8, 13, 6, 0, 0),
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_email_event_keeps_history_when_challenge_is_deleted(identity_session):
    with identity_session() as session:
        session.add(_user("u_owner", "owner"))
        session.commit()
        challenge = _challenge("c_history", "u_owner")
        session.add(challenge)
        session.commit()
        session.add(
            EmailVerificationEvent(
                id="e_history",
                user_id="u_owner",
                challenge_id="c_history",
                email_canonical="owner@example.com",
                purpose="bind",
                event_type="code_sent",
                outcome="sent",
                ip="127.0.0.1",
                created_at=datetime(2026, 8, 13, 6, 0, 0),
            )
        )
        session.commit()
        session.delete(challenge)
        session.commit()
        stored = session.get(EmailVerificationEvent, "e_history")

    assert stored.challenge_id is None
