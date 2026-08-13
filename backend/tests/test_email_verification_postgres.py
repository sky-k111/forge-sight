from __future__ import annotations

import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.auth.email_verification import (
    EmailVerificationError,
    issue_email_challenge,
    verify_email_challenge,
)
from app.config import settings
from app.db.models import EmailVerificationChallenge, EmailVerificationEvent, PlatformUser
from app.db.platform import Base


@pytest.mark.skipif(
    not os.getenv("POSTGRES_TEST_URL"),
    reason="POSTGRES_TEST_URL is only configured for PostgreSQL integration runs",
)
def test_postgres_concurrent_verification_succeeds_exactly_once(monkeypatch):
    database_url = os.environ["POSTGRES_TEST_URL"]
    schema_name = f"q07_{uuid.uuid4().hex}"
    admin_engine = create_engine(database_url)
    scoped_engine = create_engine(database_url)

    @event.listens_for(scoped_engine, "connect")
    def _set_search_path(dbapi_connection, _connection_record):
        with dbapi_connection.cursor() as cursor:
            cursor.execute(f'SET search_path TO "{schema_name}"')

    try:
        with admin_engine.begin() as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA "{schema_name}"')
        Base.metadata.create_all(scoped_engine)
        factory = sessionmaker(bind=scoped_engine, autoflush=False, expire_on_commit=False)
        monkeypatch.setattr(settings, "email_code_secret", "postgres-concurrency-secret")
        monkeypatch.setattr(settings, "email_hourly_limit_ip", 100)
        now = datetime(2026, 8, 13, 10, 0, 0)
        with factory() as session:
            session.add(
                PlatformUser(
                    id="u_concurrent",
                    username="concurrent",
                    password_hash="scrypt$test",
                    display_name="concurrent",
                    role="business",
                    title="业务人员",
                    enabled=True,
                    created_at=now,
                )
            )
            session.commit()
        issued = issue_email_challenge(
            "u_concurrent",
            "concurrent@example.com",
            "bind",
            "127.0.0.1",
            now=now,
            session_factory=factory,
        )
        barrier = threading.Barrier(2)

        def attempt(index: int) -> str:
            barrier.wait()
            try:
                verify_email_challenge(
                    "u_concurrent",
                    "concurrent@example.com",
                    issued.code,
                    f"127.0.0.{index + 1}",
                    now=now + timedelta(seconds=1),
                    session_factory=factory,
                )
                return "success"
            except EmailVerificationError as exc:
                return exc.code

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(attempt, (1, 2)))

        assert sorted(outcomes) == ["CODE_INVALID", "success"]
        with factory() as session:
            challenge = session.get(EmailVerificationChallenge, issued.challenge_id)
            assert challenge.consumed_at is not None
            assert challenge.code_hash is None
            assert (
                session.query(EmailVerificationEvent)
                .filter_by(event_type="verify_success")
                .count()
                == 1
            )
    finally:
        scoped_engine.dispose()
        with admin_engine.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        admin_engine.dispose()
