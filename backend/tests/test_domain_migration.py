"""M 领域模型 PostgreSQL 增量迁移。"""

from __future__ import annotations

import importlib.util
import os
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import DBAPIError


ROOT = Path(__file__).resolve().parents[2]
MIGRATION_DIR = ROOT / "database" / "postgresql"
SPEC = importlib.util.spec_from_file_location(
    "forge_sight_domain_database_bootstrap", ROOT / "database" / "bootstrap.py"
)
assert SPEC and SPEC.loader
bootstrap = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bootstrap)


def test_domain_migration_is_registered_after_email_identity():
    assert [path.name for path in bootstrap.postgres_migration_paths(MIGRATION_DIR)] == [
        "002_email_identity.sql",
        "003_domain_model.sql",
        "004_report_version_freeze.sql",
    ]


@pytest.mark.skipif(
    not os.getenv("POSTGRES_TEST_URL"),
    reason="POSTGRES_TEST_URL is only configured for PostgreSQL integration runs",
)
def test_domain_migration_is_repeatable_and_preserves_legacy_subscription():
    database_url = os.environ["POSTGRES_TEST_URL"]
    schema_name = f"m09_{uuid.uuid4().hex}"
    admin_engine = create_engine(database_url)
    scoped_engine = create_engine(database_url)

    @event.listens_for(scoped_engine, "connect")
    def _set_search_path(dbapi_connection, _connection_record):
        with dbapi_connection.cursor() as cursor:
            cursor.execute(f'SET search_path TO "{schema_name}"')

    try:
        with admin_engine.begin() as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA "{schema_name}"')
        bootstrap.execute_sql_file(scoped_engine, MIGRATION_DIR / "001_schema.sql")
        with scoped_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO report_subscription "
                    "(id, name, template_id, source_id, owner_id, cadence, weekday, "
                    "send_time, timezone, channel, publish_policy, enabled, created_at, updated_at) "
                    "VALUES ('legacy-sub', '历史飞书周报', 'tpl-weekly', 1, 'legacy-user', "
                    "'weekly', 1, '09:00', 'Asia/Shanghai', 'feishu', 'after_review', "
                    "TRUE, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )

        assert bootstrap.apply_postgres_migrations(scoped_engine, MIGRATION_DIR) == 3
        assert bootstrap.apply_postgres_migrations(scoped_engine, MIGRATION_DIR) == 3

        with scoped_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO report_version "
                    "(id, report_id, version, status, summary, content_json, "
                    "data_snapshot_json, validation_json, schema_version, workflow_version, "
                    "model_snapshot_json, data_quality_json, created_by, generated_at) "
                    "VALUES ('frozen-version', 'report-1', 1, 'published', 'frozen', "
                    "'{}'::jsonb, '{}'::jsonb, '{}'::jsonb, 'weekly-report/v1', "
                    "'workflow-v1', '{}'::jsonb, '{}'::jsonb, 'test', CURRENT_TIMESTAMP)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO report_version "
                    "(id, report_id, version, status, summary, content_json, "
                    "data_snapshot_json, validation_json, schema_version, workflow_version, "
                    "model_snapshot_json, data_quality_json, created_by, generated_at) "
                    "VALUES ('draft-version', 'report-2', 1, 'draft', 'draft', "
                    "'{}'::jsonb, '{}'::jsonb, '{}'::jsonb, 'weekly-report/v1', "
                    "'workflow-v1', '{}'::jsonb, '{}'::jsonb, 'test', CURRENT_TIMESTAMP)"
                )
            )
        with scoped_engine.begin() as connection:
            deleted = connection.execute(
                text("DELETE FROM report_version WHERE id = 'draft-version'")
            )
            assert deleted.rowcount == 1
        with pytest.raises(DBAPIError), scoped_engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE report_version SET status = 'draft' "
                    "WHERE id = 'frozen-version'"
                )
            )
        with pytest.raises(DBAPIError), scoped_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM report_version WHERE id = 'frozen-version'")
            )

        with scoped_engine.connect() as connection:
            legacy = connection.execute(
                text(
                    "SELECT channel, publish_policy FROM report_subscription "
                    "WHERE id = 'legacy-sub'"
                )
            ).one()
            tables = set(
                connection.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = current_schema()"
                    )
                ).scalars()
            )
            report_columns = set(
                connection.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema = current_schema() AND table_name = 'report'"
                    )
                ).scalars()
            )

        assert legacy == ("feishu", "after_review")
        assert {
            "report_file",
            "message",
            "message_recipient",
            "announcement",
            "delivery_attempt",
            "organization_unit",
            "user_membership",
            "data_scope_policy",
            "domain_event_outbox",
        } <= tables
        assert {
            "subscription_id",
            "schedule_key",
            "audience_scope_hash",
            "publish_source",
        } <= report_columns
    finally:
        scoped_engine.dispose()
        with admin_engine.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        admin_engine.dispose()
