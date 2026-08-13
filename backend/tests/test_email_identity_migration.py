from __future__ import annotations

import importlib.util
import os
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, text


ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP_PATH = ROOT / "database" / "bootstrap.py"
SPEC = importlib.util.spec_from_file_location("forge_sight_database_bootstrap", BOOTSTRAP_PATH)
assert SPEC and SPEC.loader
bootstrap = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bootstrap)


def test_split_sql_keeps_postgres_dollar_quoted_block_together():
    script = """
    DO $$
    BEGIN
        IF TRUE THEN
            PERFORM 1;
        END IF;
    END
    $$;
    SELECT 2;
    """

    statements = bootstrap.split_sql(script)

    assert len(statements) == 2
    assert statements[0].lstrip().startswith("DO $$")
    assert statements[0].rstrip().endswith("$$")
    assert statements[1] == "SELECT 2"


def test_postgres_migration_paths_only_return_incremental_files(tmp_path):
    for name in (
        "000_create_database.sql",
        "001_schema.sql",
        "002_email_identity.sql",
        "010_future.sql",
        "notes.sql",
    ):
        (tmp_path / name).write_text("SELECT 1;", encoding="utf-8")

    paths = bootstrap.postgres_migration_paths(tmp_path)

    assert [path.name for path in paths] == [
        "002_email_identity.sql",
        "010_future.sql",
    ]


@pytest.mark.skipif(
    not os.getenv("POSTGRES_TEST_URL"),
    reason="POSTGRES_TEST_URL is only configured for PostgreSQL integration runs",
)
def test_email_identity_migration_is_repeatable_and_preserves_existing_user():
    database_url = os.environ["POSTGRES_TEST_URL"]
    schema_name = f"q02_{uuid.uuid4().hex}"
    admin_engine = create_engine(database_url)
    scoped_engine = create_engine(database_url)

    @event.listens_for(scoped_engine, "connect")
    def _set_search_path(dbapi_connection, _connection_record):
        with dbapi_connection.cursor() as cursor:
            cursor.execute(f'SET search_path TO "{schema_name}"')

    try:
        with admin_engine.begin() as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA "{schema_name}"')

        with scoped_engine.begin() as connection:
            connection.exec_driver_sql(
                """
                CREATE TABLE platform_user (
                    id VARCHAR(40) PRIMARY KEY,
                    username VARCHAR(50) NOT NULL UNIQUE,
                    password_hash VARCHAR(256) NOT NULL
                )
                """
            )
            connection.execute(
                text(
                    "INSERT INTO platform_user (id, username, password_hash) "
                    "VALUES (:id, :username, :password_hash)"
                ),
                {
                    "id": "u_legacy",
                    "username": "legacy",
                    "password_hash": "scrypt$legacy-password-hash",
                },
            )

        migration_dir = ROOT / "database" / "postgresql"
        assert bootstrap.apply_postgres_migrations(scoped_engine, migration_dir) == 1
        assert bootstrap.apply_postgres_migrations(scoped_engine, migration_dir) == 1

        with scoped_engine.connect() as connection:
            legacy_user = connection.execute(
                text(
                    "SELECT username, password_hash, email, email_canonical, "
                    "email_verified_at FROM platform_user WHERE id = 'u_legacy'"
                )
            ).one()
            table_names = set(
                connection.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = current_schema()"
                    )
                ).scalars()
            )
            index_names = set(
                connection.execute(
                    text(
                        "SELECT indexname FROM pg_indexes "
                        "WHERE schemaname = current_schema()"
                    )
                ).scalars()
            )

        assert legacy_user == (
            "legacy",
            "scrypt$legacy-password-hash",
            None,
            None,
            None,
        )
        assert {
            "platform_user",
            "email_verification_challenge",
            "email_verification_event",
        } <= table_names
        assert {
            "uq_platform_user_email",
            "uq_email_verification_challenge_active",
            "ix_email_verification_event_email_time",
        } <= index_names
    finally:
        scoped_engine.dispose()
        with admin_engine.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        admin_engine.dispose()
