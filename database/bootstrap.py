"""Initialize ForgeSight's MySQL business DB and PostgreSQL platform DB.

The command is non-destructive and can be run repeatedly. It creates missing
objects, upserts official business data, scans metadata and inserts baseline
business knowledge.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
DATABASE = ROOT / "database"
SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9_]+$")


def load_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        raise FileNotFoundError(f"Environment file does not exist: {path}")
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
        os.environ.setdefault(key, value)
    return values


def require_url(values: dict[str, str], key: str) -> str:
    value = os.getenv(key, values.get(key, "")).strip()
    if not value or "CHANGE_ME" in value:
        raise RuntimeError(f"Please configure {key} in backend/.env before initialization")
    return value


def safe_database_name(url: str, label: str) -> str:
    name = make_url(url).database or ""
    if not name or not SAFE_IDENTIFIER.fullmatch(name):
        raise RuntimeError(f"{label} database name must contain only letters, numbers and underscores")
    return name


def split_sql(script: str) -> list[str]:
    """Split SQL without breaking quoted or PostgreSQL dollar-quoted semicolons."""
    statements: list[str] = []
    buffer: list[str] = []
    quote: str | None = None
    dollar_quote: str | None = None
    line_comment = False
    block_comment = False
    index = 0
    while index < len(script):
        char = script[index]
        following = script[index + 1] if index + 1 < len(script) else ""
        if dollar_quote:
            if script.startswith(dollar_quote, index):
                buffer.append(dollar_quote)
                index += len(dollar_quote)
                dollar_quote = None
            else:
                buffer.append(char)
                index += 1
            continue
        if line_comment:
            if char == "\n":
                line_comment = False
                buffer.append(char)
            index += 1
            continue
        if block_comment:
            if char == "*" and following == "/":
                block_comment = False
                index += 2
            else:
                index += 1
            continue
        if quote is None and char == "-" and following == "-":
            line_comment = True
            index += 2
            continue
        if quote is None and char == "/" and following == "*":
            block_comment = True
            index += 2
            continue
        if quote:
            buffer.append(char)
            if char == "\\" and index + 1 < len(script):
                buffer.append(script[index + 1])
                index += 2
                continue
            if char == quote:
                if following == quote:
                    buffer.append(following)
                    index += 2
                    continue
                quote = None
            index += 1
            continue
        if char in {"'", '"', "`"}:
            quote = char
            buffer.append(char)
        elif char == "$":
            match = re.match(r"\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$", script[index:])
            if match:
                dollar_quote = match.group(0)
                buffer.append(dollar_quote)
                index += len(dollar_quote)
                continue
            buffer.append(char)
        elif char == ";":
            statement = "".join(buffer).strip()
            if statement:
                statements.append(statement)
            buffer = []
        else:
            buffer.append(char)
        index += 1
    trailing = "".join(buffer).strip()
    if trailing:
        statements.append(trailing)
    return statements


def execute_sql_file(engine, path: Path) -> int:
    statements = split_sql(path.read_text(encoding="utf-8-sig"))
    is_mysql = engine.dialect.name == "mysql"
    with engine.begin() as connection:
        for statement in statements:
            if is_mysql:
                # PyMySQL treats a literal "%" as a format placeholder even when
                # no parameters are bound (SQLAlchemy passes an empty tuple), so
                # double any literal percent to avoid "unsupported format character".
                statement = statement.replace("%", "%%")
            connection.exec_driver_sql(statement)
    return len(statements)


def postgres_migration_paths(directory: Path = DATABASE / "postgresql") -> list[Path]:
    """Return numbered PostgreSQL increments; 000/001 are bootstrap artifacts."""
    migrations: list[Path] = []
    for path in directory.glob("*.sql"):
        prefix = path.stem.split("_", 1)[0]
        if prefix.isdigit() and int(prefix) >= 2:
            migrations.append(path)
    return sorted(migrations, key=lambda path: (int(path.stem.split("_", 1)[0]), path.name))


def apply_postgres_migrations(engine, directory: Path = DATABASE / "postgresql") -> int:
    """Apply all idempotent PostgreSQL increments in numeric order."""
    migrations = postgres_migration_paths(directory)
    for path in migrations:
        statement_count = execute_sql_file(engine, path)
        print(f"[PostgreSQL] applied {path.name} ({statement_count} statements)")
    return len(migrations)


def initialize_mysql(
    admin_url: str,
    business_url: str,
    *,
    include_data: bool,
    create_reader: bool,
) -> None:
    database_name = safe_database_name(business_url, "MySQL business")
    admin_base = make_url(admin_url)
    if not admin_base.drivername.startswith("mysql"):
        raise RuntimeError("ADMIN_DB_URL must be a MySQL URL")

    admin_engine = create_engine(admin_base.set(database=None), pool_pre_ping=True)
    with admin_engine.begin() as connection:
        connection.exec_driver_sql(
            f"CREATE DATABASE IF NOT EXISTS `{database_name}` "
            "CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci"
        )
    admin_engine.dispose()

    target_engine = create_engine(admin_base.set(database=database_name), pool_pre_ping=True)
    schema_count = execute_sql_file(target_engine, DATABASE / "mysql" / "001_schema.sql")
    print(f"[MySQL] schema ready ({schema_count} statements)")
    if include_data:
        seed_count = execute_sql_file(target_engine, DATABASE / "mysql" / "002_seed_official_data.sql")
        print(f"[MySQL] official dataset ready ({seed_count} statements)")

    if create_reader:
        business = make_url(business_url)
        username, password = business.username or "", business.password or ""
        if not username or not password:
            raise RuntimeError("BUSINESS_DB_URL must include the read-only username and password")
        if not re.fullmatch(r"[A-Za-z0-9_]+", username):
            raise RuntimeError("The MySQL read-only username contains unsupported characters")
        with target_engine.begin() as connection:
            raw = connection.connection.driver_connection
            escaped_user = raw.escape(username)
            escaped_host = raw.escape("%")
            escaped_password = raw.escape(password)
            # PyMySQL 的 execute() 内部用 % 格式化参数，SQL 中的 % 字面量（@'%' 主机通配）
            # 必须转义为 %%；mogrify 时会还原为单个 %。
            connection.exec_driver_sql(
                f"CREATE USER IF NOT EXISTS {escaped_user}@{escaped_host} IDENTIFIED BY {escaped_password}".replace(
                    "%", "%%"
                )
            )
            connection.exec_driver_sql(
                f"ALTER USER {escaped_user}@{escaped_host} IDENTIFIED BY {escaped_password}".replace(
                    "%", "%%"
                )
            )
            connection.exec_driver_sql(
                f"GRANT SELECT ON `{database_name}`.* TO {escaped_user}@{escaped_host}".replace(
                    "%", "%%"
                )
            )
        print(f"[MySQL] read-only account {username} ready")
    target_engine.dispose()


def ensure_postgres_database(admin_url: str, platform_url: str) -> None:
    database_name = safe_database_name(platform_url, "PostgreSQL platform")
    admin = make_url(admin_url)
    if not admin.drivername.startswith("postgresql"):
        raise RuntimeError("POSTGRES_ADMIN_DB_URL must be a PostgreSQL URL")
    engine = create_engine(admin, isolation_level="AUTOCOMMIT", pool_pre_ping=True)
    with engine.connect() as connection:
        exists = connection.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :name"),
            {"name": database_name},
        ).scalar()
        if not exists:
            connection.exec_driver_sql(f'CREATE DATABASE "{database_name}" ENCODING \'UTF8\'')
    engine.dispose()


def initialize_postgres(
    postgres_admin_url: str,
    platform_url: str,
    *,
    seed_baseline: bool,
) -> None:
    ensure_postgres_database(postgres_admin_url, platform_url)
    sys.path.insert(0, str(BACKEND))

    from app.db import models  # noqa: F401
    from app.db.platform import Base, platform_engine

    Base.metadata.create_all(platform_engine)
    apply_postgres_migrations(platform_engine)
    print(f"[PostgreSQL] platform schema ready ({len(Base.metadata.tables)} tables)")
    if not seed_baseline:
        return

    from app.knowledge.seed import seed_knowledge
    from app.metadata.datasources import seed_default_source
    from app.metadata.scanner import scan_default_source
    from app.reports.templates import seed_report_templates
    from app.skills.seed import seed_analysis_skills

    seed_default_source()
    scan_summary = scan_default_source(auto_register=True)
    knowledge_summary = seed_knowledge()
    report_summary = seed_report_templates()
    seed_analysis_skills()
    print(
        "[PostgreSQL] baseline ready "
        f"(metadata: {scan_summary}; knowledge: {knowledge_summary}; reports: {report_summary})"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env-file",
        type=Path,
        default=BACKEND / ".env",
        help="Environment file containing the four database URLs",
    )
    parser.add_argument("--skip-mysql", action="store_true")
    parser.add_argument("--skip-postgres", action="store_true")
    parser.add_argument(
        "--schema-only",
        action="store_true",
        help="Create schemas only; do not load official data or semantic baseline",
    )
    parser.add_argument(
        "--skip-reader",
        action="store_true",
        help="Do not create/grant the MySQL read-only account",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    values = load_env_file(args.env_file.resolve())
    business_url = require_url(values, "BUSINESS_DB_URL")
    platform_url = require_url(values, "PLATFORM_DB_URL")
    os.environ["BUSINESS_DB_URL"] = business_url
    os.environ["PLATFORM_DB_URL"] = platform_url

    if not args.skip_mysql:
        initialize_mysql(
            require_url(values, "ADMIN_DB_URL"),
            business_url,
            include_data=not args.schema_only,
            create_reader=not args.skip_reader,
        )
    if not args.skip_postgres:
        initialize_postgres(
            require_url(values, "POSTGRES_ADMIN_DB_URL"),
            platform_url,
            seed_baseline=not args.schema_only,
        )
    print("ForgeSight database initialization completed.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"Initialization failed: {type(exc).__name__}: {exc}") from exc
