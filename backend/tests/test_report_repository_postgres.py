"""真实 PostgreSQL 下的周报幂等创建与 Outbox 并发领取。"""

from __future__ import annotations

import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from threading import Barrier

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, DomainEventOutbox, Message, Report, ReportDelivery
from app.reports.repository import (
    claim_outbox_events,
    get_or_create_delivery,
    get_or_create_message,
    get_or_create_report,
)


@pytest.mark.skipif(
    not os.getenv("POSTGRES_TEST_URL"),
    reason="POSTGRES_TEST_URL is only configured for PostgreSQL integration runs",
)
def test_postgres_prevents_duplicate_domain_records_and_outbox_claims():
    database_url = os.environ["POSTGRES_TEST_URL"]
    schema_name = f"m10_{uuid.uuid4().hex}"
    admin_engine = create_engine(database_url)
    scoped_engine = create_engine(database_url, pool_size=6, max_overflow=2)

    @event.listens_for(scoped_engine, "connect")
    def _set_search_path(dbapi_connection, _connection_record):
        with dbapi_connection.cursor() as cursor:
            cursor.execute(f'SET search_path TO "{schema_name}"')

    try:
        with admin_engine.begin() as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA "{schema_name}"')
        Base.metadata.create_all(scoped_engine)
        sessions = sessionmaker(bind=scoped_engine, autoflush=False, expire_on_commit=False)

        def concurrently(callable_):
            barrier = Barrier(2)

            def run(index):
                with sessions() as session, session.begin():
                    barrier.wait()
                    return callable_(session, index)

            with ThreadPoolExecutor(max_workers=2) as pool:
                return [future.result() for future in [pool.submit(run, 1), pool.submit(run, 2)]]

        report_ids = concurrently(
            lambda session, index: get_or_create_report(
                session,
                id=f"report-{index}",
                title="经营周报",
                template_id="tpl-weekly",
                source_id=1,
                period_start=datetime(2026, 8, 3),
                period_end=datetime(2026, 8, 10),
                subscription_id="sub-1",
                schedule_key="2026-W32",
                audience_scope_hash="scope-a",
                publish_source="report-agent",
            ).id
        )
        message_ids = concurrently(
            lambda session, index: get_or_create_message(
                session,
                id=f"message-{index}",
                idempotency_key="report.published:version-1",
                message_type="weekly_report",
                source="report-agent",
                title="经营周报",
                report_version_id="version-1",
            ).id
        )
        delivery_ids = concurrently(
            lambda session, index: get_or_create_delivery(
                session,
                id=f"delivery-{index}",
                report_id=report_ids[0],
                version_id="version-1",
                recipient_user_id="user-1",
                recipient_address_snapshot="user@example.com",
                idempotency_key="delivery:version-1:user-1:email",
                channel="email",
            ).id
        )

        with sessions() as session, session.begin():
            session.add(
                DomainEventOutbox(
                    id="event-1",
                    event_type="report.published",
                    aggregate_id=report_ids[0],
                    idempotency_key="outbox:version-1",
                )
            )
        claimed = concurrently(
            lambda session, index: [
                row.id
                for row in claim_outbox_events(
                    session,
                    f"worker-{index}",
                    now=datetime(2026, 8, 13, 9, 0),
                    limit=1,
                )
            ]
        )

        with sessions() as session:
            assert session.query(Report).count() == 1
            assert session.query(Message).count() == 1
            assert session.query(ReportDelivery).count() == 1
        assert report_ids[0] == report_ids[1]
        assert message_ids[0] == message_ids[1]
        assert delivery_ids[0] == delivery_ids[1]
        assert sorted(len(items) for items in claimed) == [0, 1]
    finally:
        scoped_engine.dispose()
        with admin_engine.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        admin_engine.dispose()
