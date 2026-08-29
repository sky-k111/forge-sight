"""MySQL and MariaDB database adapters."""

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


class MySqlAdapter(BaseRelationalAdapter):
    type_name = "mysql"
    label = "MySQL"
    default_port = 3306
    maturity = "stable"
    sqlalchemy_driver = "mysql+pymysql"
    sqlglot_dialect = "mysql"
    capabilities = AdapterCapabilities(explain_cost=True)

    def build_url(self, source: DataSource) -> URL:
        return URL.create(
            self.sqlalchemy_driver,
            username=source.username,
            password=source.password,
            host=source.host,
            port=source.port,
            database=source.db_name,
            query={"charset": "utf8mb4"},
        )

    def configure_engine(self, engine: Engine, source: DataSource) -> None:
        del source

        @event.listens_for(engine, "connect")
        def _set_readonly(dbapi_connection, _record):
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("SET SESSION TRANSACTION READ ONLY")
                cursor.execute(f"SET SESSION MAX_EXECUTION_TIME = {settings.sql_timeout_seconds * 1000}")
            finally:
                cursor.close()

    def version_statement(self) -> str:
        return "SELECT VERSION()"

    def quote_identifier(self, value: str) -> str:
        return "`" + value.replace("`", "``") + "`"

    def list_schemas(self, conn: Connection, source: DataSource) -> list[str]:
        configured = source_schemas(source)
        if configured:
            return configured
        return [source.db_name]

    def inspect_metadata(self, conn: Connection, source: DataSource) -> SchemaSnapshot:
        schemas = self.list_schemas(conn, source)
        tables: list[TableMetadata] = []
        foreign_keys: list[ForeignKeyMetadata] = []
        for schema in schemas:
            table_rows = conn.execute(
                text(
                    "SELECT table_name, table_comment, table_type FROM information_schema.tables "
                    "WHERE table_schema=:schema AND table_type IN ('BASE TABLE','VIEW') ORDER BY table_name"
                ),
                {"schema": schema},
            ).all()
            column_rows = conn.execute(
                text(
                    "SELECT table_name,column_name,column_type,column_comment,ordinal_position,column_key "
                    "FROM information_schema.columns WHERE table_schema=:schema "
                    "ORDER BY table_name,ordinal_position"
                ),
                {"schema": schema},
            ).all()
            by_table: dict[str, list[ColumnMetadata]] = {}
            for table_name, name, data_type, comment, ordinal, key in column_rows:
                by_table.setdefault(table_name, []).append(
                    ColumnMetadata(name, data_type, int(ordinal), comment or "", key == "PRI")
                )
            for table_name, comment, table_type in table_rows:
                object_type = "view" if table_type == "VIEW" else "table"
                tables.append(
                    TableMetadata(
                        PhysicalTableRef(schema, table_name, object_type),
                        comment or "",
                        tuple(by_table.get(table_name, [])),
                    )
                )
            fk_rows = conn.execute(
                text(
                    "SELECT table_name,column_name,referenced_table_schema,referenced_table_name,"
                    "referenced_column_name,constraint_name FROM information_schema.key_column_usage "
                    "WHERE table_schema=:schema AND referenced_table_name IS NOT NULL"
                ),
                {"schema": schema},
            ).all()
            for table_name, column, ref_schema, ref_table, ref_column, constraint in fk_rows:
                foreign_keys.append(
                    ForeignKeyMetadata(
                        PhysicalTableRef(schema, table_name),
                        column,
                        PhysicalTableRef(ref_schema or schema, ref_table),
                        ref_column,
                        constraint or "",
                    )
                )
        return SchemaSnapshot(tuple(tables), tuple(foreign_keys))

    def explain_statement(self, sql: str) -> str:
        return f"EXPLAIN FORMAT=JSON {sql.strip().rstrip(';')}"

    def dialect_prompt(self) -> str:
        return "目标数据库为 MySQL 8，请使用反引号标识符、LIMIT 和 MySQL 兼容日期函数，只生成只读 SQL。"


class MariaDbAdapter(MySqlAdapter):
    type_name = "mariadb"
    label = "MariaDB"
    maturity = "stable"
    sqlalchemy_driver = "mysql+pymysql"
    sqlglot_dialect = "mysql"

    def configure_engine(self, engine: Engine, source: DataSource) -> None:
        del source

        @event.listens_for(engine, "connect")
        def _set_readonly(dbapi_connection, _record):
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("SET SESSION TRANSACTION READ ONLY")
                cursor.execute(f"SET SESSION max_statement_time = {settings.sql_timeout_seconds}")
            finally:
                cursor.close()

    def dialect_prompt(self) -> str:
        return "目标数据库为 MariaDB，请使用 MariaDB 兼容语法与 LIMIT，只生成只读 SQL。"
