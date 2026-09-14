"""The dashboard table must be reproducible outside ORM startup."""

from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MIGRATION_DIR = ROOT / "database" / "postgresql"
SPEC = importlib.util.spec_from_file_location("dashboard_database_bootstrap", ROOT / "database" / "bootstrap.py")
assert SPEC and SPEC.loader
bootstrap = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bootstrap)


def test_dashboard_migration_is_registered_after_multi_database_adapter():
    names = [path.name for path in bootstrap.postgres_migration_paths(MIGRATION_DIR)]
    assert names.index("018_multi_database_adapter.sql") < names.index("019_personal_business_dashboard.sql")


def test_dashboard_schema_keeps_the_three_part_ownership_boundary():
    migration = (MIGRATION_DIR / "019_personal_business_dashboard.sql").read_text(encoding="utf-8")
    base_schema = (MIGRATION_DIR / "001_schema.sql").read_text(encoding="utf-8")
    for sql in (migration, base_schema):
        assert "PRIMARY KEY (tenant_id, user_id, source_id)" in sql
        assert "config_json JSONB NOT NULL" in sql
    assert "ON DELETE CASCADE" in migration
    assert "SQL、查询结果或凭据" in migration
