"""SQL Server and Oracle adapters (beta connector tier)."""

from __future__ import annotations

from sqlalchemy import event, text
from sqlalchemy.engine import Connection, Engine, URL

from app.config import settings
from app.db.models import DataSource
from app.gateway.connectors.base import (
    AdapterCapabilities,
    BaseRelationalAdapter,
    ColumnMetadata,
    ConnectionField,
    ForeignKeyMetadata,
    PhysicalTableRef,
    SchemaSnapshot,
    TableMetadata,
    source_schemas,
)


class SqlServerAdapter(BaseRelationalAdapter):
    type_name = "sqlserver"
    label = "SQL Server"
    default_port = 1433
    maturity = "beta"
    sqlalchemy_driver = "mssql+pyodbc"
    sqlglot_dialect = "tsql"
    capabilities = AdapterCapabilities(explain_cost=False)
    connection_fields = (
        ConnectionField("driver", "ODBC 驱动", default="ODBC Driver 18 for SQL Server"),
        ConnectionField("encrypt", "加密连接", kind="boolean", default=True),
        ConnectionField("trustServerCertificate", "信任服务器证书", kind="boolean", default=False),
    )

    def build_url(self, source: DataSource) -> URL:
        config = source.config_json or {}
        query = {
            "driver": str(config.get("driver") or "ODBC Driver 18 for SQL Server"),
            "Encrypt": "yes" if config.get("encrypt", True) else "no",
            "TrustServerCertificate": "yes" if config.get("trustServerCertificate", False) else "no",
        }
        return URL.create(
            self.sqlalchemy_driver,
            username=source.username,
            password=source.password,
            host=source.host,
            port=source.port,
            database=source.db_name,
            query=query,
        )

    def configure_engine(self, engine: Engine, source: DataSource) -> None:
        del source

        @event.listens_for(engine, "connect")
        def _set_session(dbapi_connection, _record):
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute(f"SET LOCK_TIMEOUT {settings.sql_timeout_seconds * 1000}")
            finally:
                cursor.close()

        @event.listens_for(engine, "before_cursor_execute")
        def _set_query_timeout(_conn, cursor, _statement, _parameters, _context, _executemany):
            try:
                cursor.timeout = settings.sql_timeout_seconds
            except Exception:  # noqa: BLE001 - driver versions differ
                pass

    def version_statement(self) -> str:
        return "SELECT CAST(SERVERPROPERTY('ProductVersion') AS varchar(128))"

    def quote_identifier(self, value: str) -> str:
        return "[" + value.replace("]", "]]" ) + "]"

    def list_schemas(self, conn: Connection, source: DataSource) -> list[str]:
        configured = source_schemas(source)
        if configured:
            return configured
        return list(
            conn.execute(
                text(
                    "SELECT name FROM sys.schemas WHERE name NOT IN "
                    "('sys','INFORMATION_SCHEMA','guest') ORDER BY name"
                )
            ).scalars()
        ) or ["dbo"]

    def inspect_metadata(self, conn: Connection, source: DataSource) -> SchemaSnapshot:
        schemas = self.list_schemas(conn, source)
        tables: list[TableMetadata] = []
        foreign_keys: list[ForeignKeyMetadata] = []
        for schema in schemas:
            table_rows = conn.execute(
                text(
                    "SELECT o.name,COALESCE(CAST(ep.value AS nvarchar(4000)),''),o.type "
                    "FROM sys.objects o JOIN sys.schemas s ON s.schema_id=o.schema_id "
                    "LEFT JOIN sys.extended_properties ep ON ep.major_id=o.object_id "
                    "AND ep.minor_id=0 AND ep.name='MS_Description' "
                    "WHERE s.name=:schema AND o.type IN ('U','V') ORDER BY o.name"
                ),
                {"schema": schema},
            ).all()
            column_rows = conn.execute(
                text(
                    "SELECT o.name,c.name,TYPE_NAME(c.user_type_id),"
                    "COALESCE(CAST(ep.value AS nvarchar(4000)),''),c.column_id,"
                    "CASE WHEN pk.column_id IS NULL THEN 0 ELSE 1 END "
                    "FROM sys.objects o JOIN sys.schemas s ON s.schema_id=o.schema_id "
                    "JOIN sys.columns c ON c.object_id=o.object_id "
                    "LEFT JOIN sys.extended_properties ep ON ep.major_id=o.object_id "
                    "AND ep.minor_id=c.column_id AND ep.name='MS_Description' "
                    "LEFT JOIN (SELECT ic.object_id,ic.column_id FROM sys.indexes i "
                    "JOIN sys.index_columns ic ON ic.object_id=i.object_id AND ic.index_id=i.index_id "
                    "WHERE i.is_primary_key=1) pk ON pk.object_id=o.object_id AND pk.column_id=c.column_id "
                    "WHERE s.name=:schema AND o.type IN ('U','V') ORDER BY o.name,c.column_id"
                ),
                {"schema": schema},
            ).all()
            by_table: dict[str, list[ColumnMetadata]] = {}
            for table_name, name, data_type, comment, ordinal, is_primary in column_rows:
                by_table.setdefault(table_name, []).append(
                    ColumnMetadata(name, data_type, int(ordinal), comment or "", bool(is_primary))
                )
            for table_name, comment, kind in table_rows:
                tables.append(
                    TableMetadata(
                        PhysicalTableRef(schema, table_name, "view" if kind == "V" else "table"),
                        comment or "",
                        tuple(by_table.get(table_name, [])),
                    )
                )
            fk_rows = conn.execute(
                text(
                    "SELECT po.name,pc.name,rs.name,ro.name,rc.name,fk.name "
                    "FROM sys.foreign_keys fk JOIN sys.foreign_key_columns fkc "
                    "ON fkc.constraint_object_id=fk.object_id "
                    "JOIN sys.objects po ON po.object_id=fk.parent_object_id "
                    "JOIN sys.schemas ps ON ps.schema_id=po.schema_id "
                    "JOIN sys.columns pc ON pc.object_id=po.object_id AND pc.column_id=fkc.parent_column_id "
                    "JOIN sys.objects ro ON ro.object_id=fk.referenced_object_id "
                    "JOIN sys.schemas rs ON rs.schema_id=ro.schema_id "
                    "JOIN sys.columns rc ON rc.object_id=ro.object_id AND rc.column_id=fkc.referenced_column_id "
                    "WHERE ps.name=:schema ORDER BY fk.name,fkc.constraint_column_id"
                ),
                {"schema": schema},
            ).all()
            for src_table, src_col, target_schema, target_table, target_col, constraint in fk_rows:
                foreign_keys.append(
                    ForeignKeyMetadata(
                        PhysicalTableRef(schema, src_table), src_col,
                        PhysicalTableRef(target_schema, target_table), target_col, constraint or "",
                    )
                )
        return SchemaSnapshot(tuple(tables), tuple(foreign_keys), ("SQL Server 只读性必须由数据库账号权限保证。",))

    def dialect_prompt(self) -> str:
        return (
            "目标数据库为 SQL Server，请使用方括号标识符、TOP 或 OFFSET/FETCH 和 T-SQL 日期函数；"
            "不要使用 LIMIT，只生成只读 SQL。"
        )


class OracleAdapter(BaseRelationalAdapter):
    type_name = "oracle"
    label = "Oracle"
    default_port = 1521
    maturity = "beta"
    sqlalchemy_driver = "oracle+oracledb"
    sqlglot_dialect = "oracle"
    capabilities = AdapterCapabilities(explain_cost=False, materialized_views=True)
    connection_fields = (
        ConnectionField("serviceName", "Service Name", required=True),
    )

    def build_url(self, source: DataSource) -> URL:
        config = source.config_json or {}
        service_name = str(config.get("serviceName") or source.db_name)
        return URL.create(
            self.sqlalchemy_driver,
            username=source.username,
            password=source.password,
            host=source.host,
            port=source.port,
            query={"service_name": service_name},
        )

    def configure_engine(self, engine: Engine, source: DataSource) -> None:
        del source

        @event.listens_for(engine, "connect")
        def _set_timeout(dbapi_connection, _record):
            try:
                dbapi_connection.call_timeout = settings.sql_timeout_seconds * 1000
            except Exception:  # noqa: BLE001 - old thin/thick drivers differ
                pass

    def test_connection(self, engine: Engine, source: DataSource):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1 FROM DUAL"))
                schemas = tuple(self.list_schemas(conn, source))
                version = str(conn.execute(text(self.version_statement())).scalar() or "")[:120]
            from app.gateway.connectors.base import ConnectionTestResult

            return ConnectionTestResult(True, "连接正常", version, schemas, ("Oracle 只读性必须由数据库账号权限保证。",))
        except Exception as exc:  # noqa: BLE001
            from app.gateway.connectors.base import ConnectionTestResult

            return ConnectionTestResult(False, f"{type(exc).__name__}: {exc}"[:300])

    def version_statement(self) -> str:
        return "SELECT version FROM product_component_version WHERE product LIKE 'Oracle Database%' FETCH FIRST 1 ROWS ONLY"

    def list_schemas(self, conn: Connection, source: DataSource) -> list[str]:
        configured = source_schemas(source)
        if configured:
            return [item.upper() for item in configured]
        return [source.username.upper()]

    def inspect_metadata(self, conn: Connection, source: DataSource) -> SchemaSnapshot:
        schemas = self.list_schemas(conn, source)
        tables: list[TableMetadata] = []
        foreign_keys: list[ForeignKeyMetadata] = []
        for schema in schemas:
            table_rows = conn.execute(
                text(
                    "SELECT o.object_name,COALESCE(c.comments,''),o.object_type FROM all_objects o "
                    "LEFT JOIN all_tab_comments c ON c.owner=o.owner AND c.table_name=o.object_name "
                    "WHERE o.owner=:schema AND o.object_type IN ('TABLE','VIEW','MATERIALIZED VIEW') "
                    "ORDER BY o.object_name"
                ),
                {"schema": schema},
            ).all()
            column_rows = conn.execute(
                text(
                    "SELECT c.table_name,c.column_name,c.data_type,COALESCE(cc.comments,''),c.column_id,"
                    "CASE WHEN pk.column_name IS NULL THEN 0 ELSE 1 END FROM all_tab_columns c "
                    "LEFT JOIN all_col_comments cc ON cc.owner=c.owner AND cc.table_name=c.table_name "
                    "AND cc.column_name=c.column_name LEFT JOIN (SELECT acc.owner,acc.table_name,acc.column_name "
                    "FROM all_constraints ac JOIN all_cons_columns acc ON acc.owner=ac.owner "
                    "AND acc.constraint_name=ac.constraint_name WHERE ac.constraint_type='P') pk "
                    "ON pk.owner=c.owner AND pk.table_name=c.table_name AND pk.column_name=c.column_name "
                    "WHERE c.owner=:schema ORDER BY c.table_name,c.column_id"
                ),
                {"schema": schema},
            ).all()
            by_table: dict[str, list[ColumnMetadata]] = {}
            for table_name, name, data_type, comment, ordinal, is_primary in column_rows:
                by_table.setdefault(table_name, []).append(
                    ColumnMetadata(name, data_type, int(ordinal), comment or "", bool(is_primary))
                )
            kind_map = {"TABLE": "table", "VIEW": "view", "MATERIALIZED VIEW": "materialized_view"}
            for table_name, comment, kind in table_rows:
                tables.append(
                    TableMetadata(
                        PhysicalTableRef(schema, table_name, kind_map.get(kind, "table")),
                        comment or "", tuple(by_table.get(table_name, [])),
                    )
                )
            fk_rows = conn.execute(
                text(
                    "SELECT cc.table_name,cc.column_name,r.owner,r.table_name,r.column_name,c.constraint_name "
                    "FROM all_constraints c JOIN all_cons_columns cc ON cc.owner=c.owner "
                    "AND cc.constraint_name=c.constraint_name JOIN all_constraints rc "
                    "ON rc.owner=c.r_owner AND rc.constraint_name=c.r_constraint_name "
                    "JOIN all_cons_columns r ON r.owner=rc.owner AND r.constraint_name=rc.constraint_name "
                    "AND r.position=cc.position WHERE c.constraint_type='R' AND c.owner=:schema"
                ),
                {"schema": schema},
            ).all()
            for src_table, src_col, target_schema, target_table, target_col, constraint in fk_rows:
                foreign_keys.append(
                    ForeignKeyMetadata(
                        PhysicalTableRef(schema, src_table), src_col,
                        PhysicalTableRef(target_schema, target_table), target_col, constraint or "",
                    )
                )
        return SchemaSnapshot(tuple(tables), tuple(foreign_keys), ("Oracle 只读性必须由数据库账号权限保证。",))

    def dialect_prompt(self) -> str:
        return (
            "目标数据库为 Oracle，请使用双引号标识符、FETCH FIRST/ROWNUM 和 Oracle 日期函数；"
            "不要使用 LIMIT，只生成只读 SQL。"
        )
