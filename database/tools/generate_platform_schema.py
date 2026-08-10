"""Render the PostgreSQL platform schema from SQLAlchemy ORM metadata."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

# Model import creates engines lazily; placeholder URLs are sufficient for DDL compilation.
os.environ.setdefault(
    "BUSINESS_DB_URL",
    "mysql+pymysql://forgesight_reader:placeholder@127.0.0.1:3306/forge_sight",
)
os.environ.setdefault(
    "PLATFORM_DB_URL",
    "postgresql+psycopg2://postgres:placeholder@127.0.0.1:5432/forge_sight_app",
)

from sqlalchemy.dialects import postgresql  # noqa: E402
from sqlalchemy.schema import CreateIndex, CreateTable  # noqa: E402

from app.db import models  # noqa: E402,F401
from app.db.platform import Base  # noqa: E402


def render_schema() -> str:
    dialect = postgresql.dialect()
    parts = [
        "-- Generated from backend/app/db/models.py.",
        "-- Do not edit by hand; regenerate with database/tools/generate_platform_schema.py.",
        "SET client_encoding = 'UTF8';",
        "",
    ]
    for table in Base.metadata.sorted_tables:
        table_sql = str(CreateTable(table, if_not_exists=True).compile(dialect=dialect)).strip()
        parts.append("\n".join(line.rstrip() for line in table_sql.splitlines()) + ";")
        for index in sorted(table.indexes, key=lambda item: item.name or ""):
            parts.append(str(CreateIndex(index, if_not_exists=True).compile(dialect=dialect)).strip() + ";")
        parts.append("")
    return "\n".join(parts)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "database" / "postgresql" / "001_schema.sql",
    )
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render_schema(), encoding="utf-8", newline="\n")
    print(f"Generated {args.output}")


if __name__ == "__main__":
    main()
