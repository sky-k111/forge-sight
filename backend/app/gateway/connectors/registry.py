"""Database adapter registry."""

from __future__ import annotations

from app.gateway.connectors.base import DatabaseAdapter, DataSourceTypeDescriptor

_ADAPTERS: dict[str, DatabaseAdapter] = {}


def register_adapter(adapter: DatabaseAdapter) -> None:
    if adapter.type_name in _ADAPTERS:
        raise RuntimeError(f"重复数据源适配器：{adapter.type_name}")
    _ADAPTERS[adapter.type_name] = adapter


def get_adapter(source_type: str) -> DatabaseAdapter:
    key = (source_type or "").strip().lower()
    try:
        return _ADAPTERS[key]
    except KeyError as exc:
        supported = " / ".join(_ADAPTERS)
        raise ValueError(f"不支持的数据源类型：{source_type}（当前支持 {supported}）") from exc


def type_descriptors() -> list[DataSourceTypeDescriptor]:
    return [adapter.descriptor() for adapter in _ADAPTERS.values()]


def supported_types() -> dict[str, dict]:
    return {
        item.type: {
            "label": item.label,
            "default_port": item.default_port,
            "maturity": item.maturity,
        }
        for item in type_descriptors()
    }
