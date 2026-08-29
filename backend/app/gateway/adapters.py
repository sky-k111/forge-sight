"""Compatibility facade for registered database connectors and engine pools."""

from __future__ import annotations

from copy import copy

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from app.db.models import DataSource
from app.gateway.connectors import get_adapter, supported_types, type_descriptors

SUPPORTED_TYPES = supported_types()
_engines: dict[tuple[str, int, int], Engine] = {}


def _cache_key(source: DataSource) -> tuple[str, int, int]:
    return (source.tenant_id or "tenant-default", int(source.id or 0), int(source.connection_revision or 1))


def build_url(source: DataSource) -> str:
    """Return a password-masked URL for diagnostics and tests."""
    return get_adapter(source.type).build_url(source).render_as_string(hide_password=True)


def get_engine(source: DataSource) -> Engine:
    key = _cache_key(source)
    engine = _engines.get(key)
    if engine is not None:
        return engine
    adapter = get_adapter(source.type)
    engine = create_engine(
        adapter.build_url(source),
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        pool_recycle=1800,
    )
    adapter.configure_engine(engine, source)
    _engines[key] = engine
    return engine


def drop_engine(source_id: int, tenant_id: str | None = None) -> None:
    doomed = [
        key for key in _engines
        if key[1] == source_id and (tenant_id is None or key[0] == tenant_id)
    ]
    for key in doomed:
        _engines.pop(key).dispose()


def test_connection(source: DataSource) -> tuple[bool, str]:
    """Test connectivity through the same pooled engine used by the gateway."""
    try:
        result = get_adapter(source.type).test_connection(get_engine(source), source)
        details = result.message
        if result.server_version:
            details += f"；版本 {result.server_version}"
        if result.warnings:
            details += "；" + "；".join(result.warnings)
        if not result.ok:
            drop_engine(source.id, source.tenant_id)
        return result.ok, details[:500]
    except Exception as exc:  # noqa: BLE001 - driver/import failures are admin-facing
        drop_engine(source.id, source.tenant_id)
        return False, f"{type(exc).__name__}: {exc}"[:500]


def discover_schemas(source: DataSource) -> list[str]:
    adapter = get_adapter(source.type)
    discovery_source = copy(source)
    # 发现候选不能被已保存的允许范围短路；扫描时仍使用原 source 的白名单。
    discovery_source.schema_names = []
    with get_engine(source).connect() as conn:
        return adapter.list_schemas(conn, discovery_source)


__all__ = [
    "SUPPORTED_TYPES", "build_url", "discover_schemas", "drop_engine", "get_adapter", "get_engine",
    "test_connection", "type_descriptors",
]
