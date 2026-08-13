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
    names = [path.name for path in bootstrap.postgres_migration_paths(MIGRATION_DIR)]

    assert names[:2] == ["002_email_identity.sql", "003_domain_model.sql"]
    assert "004_report_version_freeze.sql" in names
    assert "005_message_center.sql" in names
    assert names.index("003_domain_model.sql") < names.index(
        "004_report_version_freeze.sql"
    ) < names.index("005_message_center.sql")


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

        migration_count = len(bootstrap.postgres_migration_paths(MIGRATION_DIR))
        assert bootstrap.apply_postgres_migrations(scoped_engine, MIGRATION_DIR) == migration_count
        assert bootstrap.apply_postgres_migrations(scoped_engine, MIGRATION_DIR) == migration_count

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

        with scoped_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO announcement "
                    "(id, revision, title, body, priority, is_pinned, audience_json, "
                    "audience_scope_hash, status, created_by, published_by, created_at, published_at) "
                    "VALUES ('frozen-announcement', 1, '公告', '冻结正文', 'normal', FALSE, "
                    "'{}'::jsonb, 'scope-a', 'published', 'admin', 'admin', "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
        with pytest.raises(DBAPIError), scoped_engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE announcement SET body = '篡改正文' "
                    "WHERE id = 'frozen-announcement'"
                )
            )
        with pytest.raises(DBAPIError), scoped_engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE announcement SET withdrawal_reason = '越权写入' "
                    "WHERE id = 'frozen-announcement'"
                )
            )
        with scoped_engine.begin() as connection:
            changed = connection.execute(
                text(
                    "UPDATE announcement SET status = 'withdrawn', withdrawn_by = 'admin', "
                    "withdrawn_at = CURRENT_TIMESTAMP, withdrawal_reason = '取消发布' "
                    "WHERE id = 'frozen-announcement'"
                )
            )
            assert changed.rowcount == 1
        with pytest.raises(DBAPIError), scoped_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM announcement WHERE id = 'frozen-announcement'")
            )
        with scoped_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO announcement "
                    "(id, revision, title, body, priority, is_pinned, audience_json, "
                    "audience_scope_hash, status, created_by, created_at) "
                    "VALUES ('draft-announcement', 1, '草稿', '可删除', 'normal', FALSE, "
                    "'{}'::jsonb, 'scope-draft', 'draft', 'admin', CURRENT_TIMESTAMP)"
                )
            )
            deleted = connection.execute(
                text("DELETE FROM announcement WHERE id = 'draft-announcement'")
            )
            assert deleted.rowcount == 1

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
