"""Datasource metadata scanning through the registered adapter contract."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from app.db.models import DataSource, DataTable, TableField, TableRelation
from app.db.platform import PlatformSession
from app.gateway.adapters import get_adapter, get_engine
from app.gateway.connectors.base import PhysicalTableRef, TableMetadata

SCHEMA_SQL_PATH = Path(__file__).resolve().parents[3] / "database" / "mysql" / "001_schema.sql"

TOPIC_BY_PREFIX = {
    "mes_": "生产",
    "qms_": "质量",
    "eqp_": "设备",
    "inv_": "库存",
    "dim_": "主数据",
}


def derive_topic(table_name: str) -> str:
    for prefix, topic in TOPIC_BY_PREFIX.items():
        if table_name.lower().startswith(prefix):
            return topic
    return "未分类"


def load_schema_comments(path: Path = SCHEMA_SQL_PATH) -> dict[str, dict]:
    """Parse comments from the bundled MySQL schema for the official demo source."""
    import re

    comments: dict[str, dict] = {}
    if not path.exists():
        return comments
    current_table = None
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"\s*CREATE TABLE(?: IF NOT EXISTS)?\s+(\w+)", line)
        if match:
            current_table = match.group(1)
            comments[current_table] = {"comment": "", "fields": {}}
            continue
        if current_table is None:
            continue
        field_match = re.match(r"\s*(\w+)\s+[\w()]+.*?COMMENT '([^']*)'", line)
        if field_match:
            comments[current_table]["fields"][field_match.group(1)] = field_match.group(2)
            continue
        table_match = re.search(r"COMMENT='([^']*)'", line)
        if table_match:
            comments[current_table]["comment"] = table_match.group(1)
            current_table = None
    return comments


def _primary_field(table: TableMetadata) -> str:
    return next((column.name for column in table.columns if column.is_primary), "")


def _format_sample(value) -> str:
    if isinstance(value, (datetime, date)):
        return str(value)
    return str(value)[:100]


def scan_source(source: DataSource, auto_register: bool = False) -> dict:
    """Scan one datasource and refresh its physical catalog.

    Physical metadata is automatic. Business semantics remain candidates until an
    administrator confirms them, so connector automation never publishes a metric
    definition by itself.
    """
    adapter = get_adapter(source.type)
    engine = get_engine(source)
    with engine.connect() as conn:
        snapshot = adapter.inspect_metadata(conn, source)

    tables_by_key = {(item.ref.schema, item.ref.name): item for item in snapshot.tables}
    primary_keys = {key: _primary_field(item) for key, item in tables_by_key.items()}
    fk_map: dict[tuple[str, str, str], tuple[str, str, str]] = {}
    relations: list[tuple[str, str, str, str, str, str, str, str]] = []
    for foreign_key in snapshot.foreign_keys:
        source_key = (
            foreign_key.from_table.schema,
            foreign_key.from_table.name,
            foreign_key.from_column,
        )
        fk_map[source_key] = (
            foreign_key.to_table.schema,
            foreign_key.to_table.name,
            foreign_key.to_column,
        )
        relations.append(
            (
                foreign_key.from_table.schema,
                foreign_key.from_table.name,
                foreign_key.from_column,
                foreign_key.to_table.schema,
                foreign_key.to_table.name,
                foreign_key.to_column,
                "扫描",
                foreign_key.constraint_name,
            )
        )

    # A conservative naming fallback, limited to the same schema. It is marked as
    # inferred evidence and never confused with a real database constraint.
    for (schema, table_name), table in tables_by_key.items():
        for column in table.columns:
            source_key = (schema, table_name, column.name)
            if source_key in fk_map or not column.name.lower().endswith("_id"):
                continue
            for (target_schema, target_name), primary_key in primary_keys.items():
                if target_schema != schema or target_name == table_name or primary_key != column.name:
                    continue
                fk_map[source_key] = (target_schema, target_name, primary_key)
                relations.append(
                    (schema, table_name, column.name, target_schema, target_name, primary_key, "推导", "")
                )
                break

    scanned_at = datetime.now()
    summary = {
        "tables": 0,
        "fields": 0,
        "relations": len(relations),
        "schemas": sorted({item.ref.schema for item in snapshot.tables}),
        "warnings": list(snapshot.warnings),
    }
    schema_comments = load_schema_comments() if source.is_default and source.type == "mysql" else {}

    with PlatformSession() as session:
        first_scan = session.query(DataTable.id).filter_by(source_id=source.id).first() is None
        scanned_keys = {(item.ref.schema, item.ref.name) for item in snapshot.tables}
        # Schema 范围收窄或物理表删除后，历史目录保留用于证据回看，但立即退出问析白名单。
        for stale in session.query(DataTable).filter_by(source_id=source.id).all():
            if (stale.schema_name, stale.name) not in scanned_keys:
                stale.registered = False
        for table in snapshot.tables:
            ref = table.ref
            official_comments = schema_comments.get(ref.name, {})
            table_comment = official_comments.get("comment") or table.comment or ""
            row_count = 0
            sample_row: dict = {}
            data_updated = ""
            try:
                with engine.connect() as conn:
                    row_count = adapter.count_rows(conn, ref)
                    samples = adapter.preview_table(conn, ref, 1)
                    sample_row = samples[0] if samples else {}
                    data_updated = adapter.infer_data_updated(conn, ref, table.columns)
            except Exception as exc:  # noqa: BLE001 - one protected object must not abort the catalog scan
                summary["warnings"].append(
                    f"{ref.qualified_name} 样例或行数读取失败：{type(exc).__name__}"
                )

            existing = (
                session.query(DataTable)
                .filter_by(source_id=source.id, schema_name=ref.schema, name=ref.name)
                .first()
            )
            if existing is None:
                existing = DataTable(
                    source_id=source.id,
                    schema_name=ref.schema,
                    name=ref.name,
                    registered=auto_register,
                )
                session.add(existing)
            existing.object_type = ref.object_type
            existing.comment = table_comment
            if not existing.cn_name or "?" in existing.cn_name:
                existing.cn_name = table_comment or ref.name
            existing.topic = existing.topic or derive_topic(ref.name)
            existing.row_count = row_count
            existing.primary_key = _primary_field(table)
            existing.data_updated = data_updated
            existing.scanned_at = scanned_at

            (
                session.query(TableField)
                .filter_by(source_id=source.id, schema_name=ref.schema, table_name=ref.name)
                .delete()
            )
            field_comments = official_comments.get("fields", {})
            for column in table.columns:
                relation = fk_map.get((ref.schema, ref.name, column.name))
                cn_name = field_comments.get(column.name) or column.comment or ""
                session.add(
                    TableField(
                        source_id=source.id,
                        schema_name=ref.schema,
                        table_name=ref.name,
                        name=column.name,
                        cn_name=cn_name,
                        data_type=column.data_type,
                        ordinal=column.ordinal,
                        is_primary=column.is_primary,
                        ref_schema=relation[0] if relation else "",
                        ref_table=relation[1] if relation else "",
                        comment=cn_name,
                        sample_value=_format_sample(sample_row[column.name]) if column.name in sample_row else "",
                    )
                )
            summary["tables"] += 1
            summary["fields"] += len(table.columns)

        session.query(TableRelation).filter_by(source_id=source.id).delete()
        for (
            from_schema, from_table, from_field, to_schema, to_table,
            to_field, source_kind, constraint_name,
        ) in relations:
            session.add(
                TableRelation(
                    source_id=source.id,
                    from_schema=from_schema,
                    from_table=from_table,
                    from_field=from_field,
                    to_schema=to_schema,
                    to_table=to_table,
                    to_field=to_field,
                    constraint_name=constraint_name,
                    source=source_kind,
                )
            )

        from app.retrieval.index_jobs import enqueue_index_job, serialize_job

        index_job = enqueue_index_job(
            session,
            source.id,
            mode="full" if first_scan else "incremental",
            reason="数据源首次接入并完成扫描" if first_scan else "数据源元数据重新扫描",
        )
        session.commit()

    summary["semanticIndexJob"] = serialize_job(index_job)
    return summary


def scan_default_source(auto_register: bool = False) -> dict:
    with PlatformSession() as session:
        source = session.query(DataSource).filter_by(is_default=True).first()
    if source is None:
        return {"tables": 0, "fields": 0, "relations": 0, "schemas": [], "warnings": []}
    return scan_source(source, auto_register=auto_register)
