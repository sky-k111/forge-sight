"""Connector registry and dialect rendering tests without live databases."""

import pytest

from app.db.models import DataSource
from app.gateway.adapters import SUPPORTED_TYPES, build_url, get_adapter
from app.gateway.connectors.registry import register_adapter


def _source(source_type: str) -> DataSource:
    return DataSource(
        id=7,
        tenant_id="tenant-a",
        name="测试源",
        type=source_type,
        host="db.internal",
        port=SUPPORTED_TYPES[source_type]["default_port"],
        db_name="manufacturing",
        username="readonly",
        password="secret with space",
        schema_names=[],
        config_json={},
    )


def test_registry_exposes_five_business_database_types():
    assert set(SUPPORTED_TYPES) == {"mysql", "postgresql", "mariadb", "sqlserver", "oracle"}


def test_registry_rejects_duplicate_adapter_type():
    with pytest.raises(RuntimeError, match="重复数据源适配器"):
        register_adapter(get_adapter("mysql"))


def test_diagnostic_url_never_discloses_password():
    url = build_url(_source("postgresql"))
    assert "secret" not in url
    assert "***" in url


def test_row_limit_uses_native_dialect():
    assert "LIMIT 25" in get_adapter("postgresql").apply_row_limit("SELECT * FROM orders", 25)
    assert "TOP 25" in get_adapter("sqlserver").apply_row_limit("SELECT * FROM orders", 25).upper()
    assert "FETCH FIRST 25 ROWS ONLY" in get_adapter("oracle").apply_row_limit(
        "SELECT * FROM orders", 25
    ).upper()


def test_existing_stricter_limit_is_preserved():
    sql = get_adapter("mysql").apply_row_limit("SELECT * FROM orders LIMIT 5", 25)
    assert "LIMIT 5" in sql.upper()
