"""Database connector contracts shared by metadata and query services."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from sqlalchemy.engine import Connection, Engine, URL

from app.db.models import DataSource


@dataclass(frozen=True)
class AdapterCapabilities:
    schemas: bool = True
    comments: bool = True
    foreign_keys: bool = True
    views: bool = True
    materialized_views: bool = False
    explain_cost: bool = False


@dataclass(frozen=True)
class ConnectionField:
    name: str
    label: str
    kind: str = "text"
    required: bool = False
    default: Any = None
    placeholder: str = ""


@dataclass(frozen=True)
class DataSourceTypeDescriptor:
    type: str
    label: str
    default_port: int
    maturity: str
    capabilities: AdapterCapabilities
    fields: tuple[ConnectionField, ...] = ()


@dataclass(frozen=True)
class PhysicalTableRef:
    schema: str
    name: str
    object_type: str = "table"

    @property
    def qualified_name(self) -> str:
        return f"{self.schema}.{self.name}" if self.schema else self.name


@dataclass(frozen=True)
class ColumnMetadata:
    name: str
    data_type: str
    ordinal: int
    comment: str = ""
    is_primary: bool = False


@dataclass(frozen=True)
class TableMetadata:
    ref: PhysicalTableRef
    comment: str = ""
    columns: tuple[ColumnMetadata, ...] = ()


@dataclass(frozen=True)
class ForeignKeyMetadata:
    from_table: PhysicalTableRef
    from_column: str
    to_table: PhysicalTableRef
    to_column: str
    constraint_name: str = ""


@dataclass(frozen=True)
class SchemaSnapshot:
    tables: tuple[TableMetadata, ...] = ()
    foreign_keys: tuple[ForeignKeyMetadata, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class ConnectionTestResult:
    ok: bool
    message: str
    server_version: str = ""
    schemas: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


def source_schemas(source: DataSource) -> list[str]:
    values = [str(item).strip() for item in (source.schema_names or []) if str(item).strip()]
    return list(dict.fromkeys(values))


class DatabaseAdapter(Protocol):
    type_name: str
    label: str
    default_port: int
    maturity: str
    sqlalchemy_driver: str
    sqlglot_dialect: str
    capabilities: AdapterCapabilities
    connection_fields: tuple[ConnectionField, ...]

    def descriptor(self) -> DataSourceTypeDescriptor: ...
    def build_url(self, source: DataSource) -> URL: ...
    def configure_engine(self, engine: Engine, source: DataSource) -> None: ...
    def test_connection(self, engine: Engine, source: DataSource) -> ConnectionTestResult: ...
    def list_schemas(self, conn: Connection, source: DataSource) -> list[str]: ...
    def inspect_metadata(self, conn: Connection, source: DataSource) -> SchemaSnapshot: ...
    def quote_identifier(self, value: str) -> str: ...
    def qualify_table(self, ref: PhysicalTableRef) -> str: ...
    def apply_row_limit(self, sql: str, limit: int) -> str: ...
    def explain_statement(self, sql: str) -> str | None: ...
    def dialect_prompt(self) -> str: ...


class BaseRelationalAdapter:
    """Conservative defaults for read-only relational connectors."""

    type_name = ""
    label = ""
    default_port = 0
    maturity = "beta"
    sqlalchemy_driver = ""
    sqlglot_dialect = ""
    capabilities = AdapterCapabilities()
    connection_fields: tuple[ConnectionField, ...] = ()

    def descriptor(self) -> DataSourceTypeDescriptor:
        return DataSourceTypeDescriptor(
            type=self.type_name,
            label=self.label,
            default_port=self.default_port,
            maturity=self.maturity,
            capabilities=self.capabilities,
            fields=self.connection_fields,
        )

    def configure_engine(self, engine: Engine, source: DataSource) -> None:
        del engine, source

    def test_connection(self, engine: Engine, source: DataSource) -> ConnectionTestResult:
        from sqlalchemy import text

        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
                schemas = tuple(self.list_schemas(conn, source))
                version = str(conn.execute(text(self.version_statement())).scalar() or "")[:120]
            return ConnectionTestResult(True, "连接正常", version, schemas)
        except Exception as exc:  # noqa: BLE001 - administrator needs the driver error class
            return ConnectionTestResult(False, f"{type(exc).__name__}: {exc}"[:300])

    def version_statement(self) -> str:
        return "SELECT CURRENT_TIMESTAMP"

    def quote_identifier(self, value: str) -> str:
        return '"' + value.replace('"', '""') + '"'

    def qualify_table(self, ref: PhysicalTableRef) -> str:
        table = self.quote_identifier(ref.name)
        return f"{self.quote_identifier(ref.schema)}.{table}" if ref.schema else table

    def apply_row_limit(self, sql: str, limit: int) -> str:
        import sqlglot
        from sqlglot import exp

        clean = sql.strip().rstrip(";")
        expression = sqlglot.parse_one(clean, read=self.sqlglot_dialect)
        current = expression.args.get("limit")
        if isinstance(current, exp.Limit):
            value = current.expression
            if isinstance(value, exp.Literal) and value.is_int and int(value.this) <= int(limit):
                return expression.sql(dialect=self.sqlglot_dialect)
        return expression.limit(int(limit)).sql(dialect=self.sqlglot_dialect)

    def explain_statement(self, sql: str) -> str | None:
        return None

    def dialect_prompt(self) -> str:
        return f"目标数据库为 {self.label}，请生成兼容 {self.label} 的只读 SQL。"

    def list_schemas(self, conn: Connection, source: DataSource) -> list[str]:
        del conn
        return source_schemas(source)

    def inspect_metadata(self, conn: Connection, source: DataSource) -> SchemaSnapshot:
        raise NotImplementedError

    def count_rows(self, conn: Connection, ref: PhysicalTableRef) -> int:
        from sqlalchemy import text

        value = conn.execute(text(f"SELECT COUNT(*) FROM {self.qualify_table(ref)}")).scalar()
        return int(value or 0)

    def preview_table(self, conn: Connection, ref: PhysicalTableRef, limit: int = 1) -> list[dict]:
        from sqlalchemy import text

        statement = f"SELECT * FROM {self.qualify_table(ref)}"
        rows = conn.execute(text(self.apply_row_limit(statement, limit))).mappings().all()
        return [dict(row) for row in rows]

    def infer_data_updated(
        self,
        conn: Connection,
        ref: PhysicalTableRef,
        columns: tuple[ColumnMetadata, ...],
    ) -> str:
        from sqlalchemy import text

        date_types = ("date", "timestamp", "datetime", "time")
        for column in columns:
            if column.data_type.lower().startswith(date_types):
                field_name = self.quote_identifier(column.name)
                value = conn.execute(
                    text(f"SELECT MAX({field_name}) FROM {self.qualify_table(ref)}")
                ).scalar()
                if value is not None:
                    return str(value)[:10]
        return ""
