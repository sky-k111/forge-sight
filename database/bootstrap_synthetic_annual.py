from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

# Reuse the tested helpers from the main bootstrap loader so SQL parsing
# (single-quoted COMMENT strings) stays consistent with the official dataset.
from bootstrap import (
    BACKEND,
    DATABASE,
    execute_sql_file,
    load_env_file,
    require_url,
)

SYNTHETIC_DB = "forge_sight_synthetic_annual_v3r2"
SYNTHETIC_SOURCE_NAME = "华东制造数据底座（一年合成）"
SYNTHETIC_SCHEMA = DATABASE / "mysql" / "004_synthetic_annual_schema.sql"
SYNTHETIC_DATA = DATABASE / "mysql" / "005_synthetic_annual_data.sql"


def _reader_credentials(business_url: str, admin_url: str) -> tuple[str, str]:
    """Prefer explicit env overrides, then the read-only business reader, then admin."""
    business = make_url(business_url)
    admin = make_url(admin_url)
    username = os.getenv("SYNTHETIC_DB_USERNAME", "") or business.username or admin.username or ""
    password = os.getenv("SYNTHETIC_DB_PASSWORD", "") or business.password or admin.password or ""
    return username, password


def initialize_mysql_business(admin_url: str, business_url: str, include_data: bool) -> None:
    """Create the synthetic database, load the one-year data set and grant the reader."""
    admin = make_url(admin_url)
    if not admin.drivername.startswith("mysql"):
        raise RuntimeError("ADMIN_DB_URL must be a MySQL URL")
    admin_engine = create_engine(admin.set(database=None), pool_pre_ping=True)
    with admin_engine.begin() as connection:
        # Synthetic DB is fully derived from local generation scripts and contains
        # no user-entered data, so a clean rebuild is safe and makes the import
        # idempotent (re-running will not collide on primary keys).
        connection.exec_driver_sql(f"DROP DATABASE IF EXISTS `{SYNTHETIC_DB}`")
        connection.exec_driver_sql(
            f"CREATE DATABASE IF NOT EXISTS `{SYNTHETIC_DB}` "
            "CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci"
        )

    target_engine = create_engine(admin.set(database=SYNTHETIC_DB), pool_pre_ping=True)
    schema_count = execute_sql_file(target_engine, SYNTHETIC_SCHEMA)
    print(f"[SyntheticMySQL] synthetic schema ready ({schema_count} statements)")
    if include_data:
        data_count = execute_sql_file(target_engine, SYNTHETIC_DATA)
        print(f"[SyntheticMySQL] one-year dataset ready ({data_count} statements)")
        _grant_reader(admin_engine, business_url, admin_url)
    target_engine.dispose()
    admin_engine.dispose()


def _grant_reader(admin_engine, business_url: str, admin_url: str) -> None:
    """Make sure the read-only account can SELECT the synthetic schema (idempotent)."""
    username, password = _reader_credentials(business_url, admin_url)
    if not username or not password:
        return
    with admin_engine.begin() as connection:
        raw = connection.connection.driver_connection
        escaped_user = raw.escape(username)
        escaped_host = raw.escape("%")
        escaped_password = raw.escape(password)
        # PyMySQL formats ''%''; double it so a literal percent survives the empty
        # parameter tuple that exec_driver_sql passes through.
        connection.exec_driver_sql(
            f"CREATE USER IF NOT EXISTS {escaped_user}@{escaped_host} IDENTIFIED BY {escaped_password}".replace(
                "%", "%%"
            )
        )
        connection.exec_driver_sql(
            f"ALTER USER {escaped_user}@{escaped_host} IDENTIFIED BY {escaped_password}".replace("%", "%%")
        )
        connection.exec_driver_sql(
            f"GRANT SELECT ON `{SYNTHETIC_DB}`.* TO {escaped_user}@{escaped_host}".replace("%", "%%")
        )
    print(f"[SyntheticMySQL] read-only account {username} granted on {SYNTHETIC_DB}")


def register_synthetic_source(platform_url: str, admin_url: str, business_url: str) -> None:
    """Register the synthetic database as a new agent data source in the platform DB."""
    sys.path.insert(0, str(BACKEND))
    from app.db.platform import PlatformSession
    from app.db.models import DataSource
    from app.metadata.scanner import scan_source

    host = make_url(admin_url).host or "127.0.0.1"
    port = make_url(admin_url).port or 3306
    username, password = _reader_credentials(business_url, admin_url)

    with PlatformSession() as session:
        existing = (
            session.query(DataSource)
            .filter_by(db_name=SYNTHETIC_DB)
            .first()
        )
        if existing is not None:
            source = existing
            source.type = "mysql"
            source.host = host
            source.port = port
            source.username = username
            source.password = password
            source.schema_names = [SYNTHETIC_DB]
            source.status = "未测试"
            session.commit()
            session.refresh(source)
        else:
            source = DataSource(
                name=SYNTHETIC_SOURCE_NAME,
                type="mysql",
                host=host,
                port=port,
                db_name=SYNTHETIC_DB,
                username=username,
                password=password,
                schema_names=[SYNTHETIC_DB],
                status="未测试",
                is_default=False,
            )
            session.add(source)
            session.commit()
            session.refresh(source)

    summary = scan_source(source, auto_register=True)
    print(
        f"[SyntheticDataSource] registered & scanned "
        f"(tables={summary.get('tables')}, fields={summary.get('fields')}, "
        f"relations={summary.get('relations')}, schemas={summary.get('schemas')})"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=BACKEND / ".env")
    parser.add_argument("--schema-only", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    values = load_env_file(args.env_file.resolve())
    admin_url = require_url(values, "ADMIN_DB_URL")
    if args.schema_only:
        platform_url = ""
        business_url = ""
    else:
        platform_url = require_url(values, "PLATFORM_DB_URL")
        business_url = require_url(values, "BUSINESS_DB_URL")

    initialize_mysql_business(admin_url, business_url, include_data=not args.schema_only)
    if not args.schema_only:
        register_synthetic_source(platform_url, admin_url, business_url)
    print("ForgeSight synthetic annual data source initialization completed.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"Initialization failed: {type(exc).__name__}: {exc}") from exc
