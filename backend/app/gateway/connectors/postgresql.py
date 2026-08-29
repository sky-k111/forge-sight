"""PostgreSQL adapter with multi-schema and comment-aware introspection."""

from __future__ import annotations

from sqlalchemy import event, text
from sqlalchemy.engine import Connection, Engine, URL

from app.config import settings
from app.db.models import DataSource
from app.gateway.connectors.base import (
    AdapterCapabilities,
    BaseRelationalAdapter,
    ColumnMetadata,
    ForeignKeyMetadata,
    PhysicalTableRef,
    SchemaSnapshot,
    TableMetadata,
    source_schemas,
)


class PostgreSqlAdapter(BaseRelationalAdapter):
    type_name = "postgresql"
    label = "PostgreSQL"
    default_port = 5432
    maturity = "stable"
    sqlalchemy_driver = "postgresql+psycopg2"
    sqlglot_dialect = "postgres"
    capabilities = AdapterCapabilities(materialized_views=True, explain_cost=True)

    def build_url(self, source: DataSource) -> URL:
        return URL.create(
            self.sqlalchemy_driver,
            username=source.username,
            password=source.password,
            host=source.host,
            port=source.port,
            database=source.db_name,
        )

    def configure_engine(self, engine: Engine, source: DataSource) -> None:
        del source

        @event.listens_for(engine, "connect")
        def _set_readonly(dbapi_connection, _record):
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
                cursor.execute(f"SET statement_timeout = {settings.sql_timeout_seconds * 1000}")
            finally:
                cursor.close()

    def version_statement(self) -> str:
        return "SHOW server_version"

    def list_schemas(self, conn: Connection, source: DataSource) -> list[str]:
        configured = source_schemas(source)
        if configured:
            return configured
        rows = conn.execute(
            text(
                "SELECT schema_name FROM information_schema.schemata "
                "WHERE schema_name NOT IN ('pg_catalog','information_schema') "
                "AND schema_name NOT LIKE 'pg_toast%' AND schema_name NOT LIKE 'pg_temp_%' "
                "ORDER BY schema_name"
            )
        ).scalars()
        result = list(rows)
        return result or ["public"]

    def inspect_metadata(self, conn: Connection, source: DataSource) -> SchemaSnapshot:
        schemas = self.list_schemas(conn, source)
        tables: list[TableMetadata] = []
        foreign_keys: list[ForeignKeyMetadata] = []
        for schema in schemas:
            table_rows = conn.execute(
                text(
                    "SELECT c.relname, COALESCE(obj_description(c.oid,'pg_class'),''), c.relkind "
                    "FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
                    "WHERE n.nspname=:schema AND c.relkind IN ('r','p','v','m') ORDER BY c.relname"
                ),
                {"schema": schema},
            ).all()
            column_rows = conn.execute(
                text(
                    "SELECT c.relname,a.attname,pg_catalog.format_type(a.atttypid,a.atttypmod),"
                    "COALESCE(col_description(c.oid,a.attnum),''),a.attnum,"
                    "EXISTS (SELECT 1 FROM pg_catalog.pg_index i WHERE i.indrelid=c.oid "
                    "AND i.indisprimary AND a.attnum=ANY(i.indkey)) "
                    "FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
                    "JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid "
                    "WHERE n.nspname=:schema AND c.relkind IN ('r','p','v','m') "
                    "AND a.attnum>0 AND NOT a.attisdropped ORDER BY c.relname,a.attnum"
                ),
                {"schema": schema},
            ).all()
            by_table: dict[str, list[ColumnMetadata]] = {}
            for table_name, name, data_type, comment, ordinal, is_primary in column_rows:
                by_table.setdefault(table_name, []).append(
                    ColumnMetadata(name, data_type, int(ordinal), comment or "", bool(is_primary))
                )
            kind_map = {"r": "table", "p": "table", "v": "view", "m": "materialized_view"}
            for table_name, comment, kind in table_rows:
                tables.append(
                    TableMetadata(
                        PhysicalTableRef(schema, table_name, kind_map.get(kind, "table")),
                        comment or "",
                        tuple(by_table.get(table_name, [])),
                    )
                )
            fk_rows = conn.execute(
                text(
                    "SELECT src.relname,sa.attname,tn.nspname,tgt.relname,ta.attname,con.conname "
                    "FROM pg_catalog.pg_constraint con "
                    "JOIN pg_catalog.pg_class src ON src.oid=con.conrelid "
                    "JOIN pg_catalog.pg_namespace sn ON sn.oid=src.relnamespace "
                    "JOIN pg_catalog.pg_class tgt ON tgt.oid=con.confrelid "
                    "JOIN pg_catalog.pg_namespace tn ON tn.oid=tgt.relnamespace "
                    "JOIN LATERAL unnest(con.conkey) WITH ORDINALITY sk(attnum,ord) ON TRUE "
                    "JOIN LATERAL unnest(con.confkey) WITH ORDINALITY tk(attnum,ord) ON tk.ord=sk.ord "
                    "JOIN pg_catalog.pg_attribute sa ON sa.attrelid=src.oid AND sa.attnum=sk.attnum "
                    "JOIN pg_catalog.pg_attribute ta ON ta.attrelid=tgt.oid AND ta.attnum=tk.attnum "
                    "WHERE con.contype='f' AND sn.nspname=:schema ORDER BY con.conname,sk.ord"
                ),
                {"schema": schema},
            ).all()
            for src_table, src_col, target_schema, target_table, target_col, constraint in fk_rows:
                foreign_keys.append(
                    ForeignKeyMetadata(
                        PhysicalTableRef(schema, src_table),
                        src_col,
                        PhysicalTableRef(target_schema, target_table),
                        target_col,
                        constraint or "",
                    )
                )
        return SchemaSnapshot(tuple(tables), tuple(foreign_keys))

    def explain_statement(self, sql: str) -> str:
        return f"EXPLAIN (FORMAT JSON, COSTS TRUE) {sql.strip().rstrip(';')}"

    def dialect_prompt(self) -> str:
        return (
            "目标数据库为 PostgreSQL，请使用双引号标识符、schema.table 限定名、LIMIT 和 PostgreSQL 日期函数；"
            "不要使用 MySQL 的反引号、IFNULL 或 DATE_FORMAT，只生成只读 SQL。"
        )
