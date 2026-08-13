"""元数据扫描：从数据源读取真实结构，登记入平台库。

原则：表结构只以 information_schema 为权威来源，禁止凭约定编造；
中文名优先取 schema.sql（默认数据源）或数据库 COMMENT，推导的部分（主题、关系）单独标注来源。
"""

from datetime import date, datetime
from pathlib import Path

from sqlalchemy import text

from app.db.models import DataSource, DataTable, TableField, TableRelation
from app.db.platform import PlatformSession
from app.gateway.adapters import get_engine

# schema.sql 是默认数据源字段注释的权威来源（建库时客户端编码问题会导致 information_schema 中的注释变成问号）
SCHEMA_SQL_PATH = Path(__file__).resolve().parents[3] / "database" / "mysql" / "001_schema.sql"

# 表名前缀 → 业务主题（仅用于默认分类，管理员可改）
TOPIC_BY_PREFIX = {
    "mes_": "生产",
    "qms_": "质量",
    "eqp_": "设备",
    "inv_": "库存",
    "dim_": "主数据",
}

# PostgreSQL 只扫描该 schema，避免系统表混入
PG_SCHEMA = "public"


def derive_topic(table_name: str) -> str:
    for prefix, topic in TOPIC_BY_PREFIX.items():
        if table_name.startswith(prefix):
            return topic
    return "未分类"


def load_schema_comments(path: Path = SCHEMA_SQL_PATH) -> dict[str, dict]:
    """从 schema.sql 解析表注释与字段注释：{表名: {"comment": ..., "fields": {字段: 注释}}}"""
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


def _scalar(conn, sql: str, params: dict | None = None):
    return conn.execute(text(sql), params or {}).scalar()


def _inspect_mysql(conn, db_name: str):
    tables = conn.execute(
        text(
            "SELECT table_name, table_comment FROM information_schema.tables "
            "WHERE table_schema = :schema AND table_type = 'BASE TABLE' ORDER BY table_name"
        ),
        {"schema": db_name},
    ).all()
    columns = conn.execute(
        text(
            "SELECT table_name, column_name, column_type, column_comment, ordinal_position, column_key "
            "FROM information_schema.columns WHERE table_schema = :schema "
            "ORDER BY table_name, ordinal_position"
        ),
        {"schema": db_name},
    ).all()
    foreign_keys = conn.execute(
        text(
            "SELECT table_name, column_name, referenced_table_name, referenced_column_name "
            "FROM information_schema.key_column_usage "
            "WHERE table_schema = :schema AND referenced_table_name IS NOT NULL"
        ),
        {"schema": db_name},
    ).all()
    fields = {}
    for table_name, col_name, col_type, comment, ordinal, col_key in columns:
        fields.setdefault(table_name, []).append(
            {"name": col_name, "type": col_type, "comment": comment or "", "ordinal": ordinal,
             "is_primary": col_key == "PRI"}
        )
    fk_map = {(t, c): (rt, rc) for t, c, rt, rc in foreign_keys}
    return tables, fields, fk_map


def _inspect_postgresql(conn):
    tables = conn.execute(
        text(
            "SELECT table_name, '' FROM information_schema.tables "
            "WHERE table_schema = :schema AND table_type = 'BASE TABLE' ORDER BY table_name"
        ),
        {"schema": PG_SCHEMA},
    ).all()
    columns = conn.execute(
        text(
            "SELECT table_name, column_name, data_type, '', ordinal_position, '' "
            "FROM information_schema.columns WHERE table_schema = :schema "
            "ORDER BY table_name, ordinal_position"
        ),
        {"schema": PG_SCHEMA},
    ).all()
    primary_keys = conn.execute(
        text(
            "SELECT kcu.table_name, kcu.column_name "
            "FROM information_schema.table_constraints tc "
            "JOIN information_schema.key_column_usage kcu "
            "  ON tc.constraint_name = kcu.constraint_name AND tc.table_name = kcu.table_name "
            "WHERE tc.constraint_type = 'PRIMARY KEY' AND tc.table_schema = :schema"
        ),
        {"schema": PG_SCHEMA},
    ).all()
    foreign_keys = conn.execute(
        text(
            "SELECT tc.table_name, kcu.column_name, ccu.table_name, ccu.column_name "
            "FROM information_schema.table_constraints tc "
            "JOIN information_schema.key_column_usage kcu "
            "  ON tc.constraint_name = kcu.constraint_name AND tc.table_name = kcu.table_name "
            "JOIN information_schema.constraint_column_usage ccu "
            "  ON tc.constraint_name = ccu.constraint_name "
            "WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_schema = :schema"
        ),
        {"schema": PG_SCHEMA},
    ).all()
    pk_set = {(t, c) for t, c in primary_keys}
    fields = {}
    for table_name, col_name, col_type, comment, ordinal, _ in columns:
        fields.setdefault(table_name, []).append(
            {"name": col_name, "type": col_type, "comment": "", "ordinal": ordinal,
             "is_primary": (table_name, col_name) in pk_set}
        )
    fk_map = {(t, c): (rt, rc) for t, c, rt, rc in foreign_keys}
    return tables, fields, fk_map


def scan_source(source: DataSource, auto_register: bool = False) -> dict:
    """扫描指定数据源并刷新平台库登记。返回扫描摘要。

    auto_register=True 仅用于首次初始化；此后发现的新表一律为「待确认」状态，
    必须经管理员确认（可走 AI 语义推荐）才对问析可见。
    """
    engine = get_engine(source)
    with engine.connect() as conn:
        if source.type == "mysql":
            tables, fields_by_table, fk_map = _inspect_mysql(conn, source.db_name)
        elif source.type == "postgresql":
            tables, fields_by_table, fk_map = _inspect_postgresql(conn)
        else:
            raise ValueError(f"不支持的数据源类型：{source.type}")

    table_names = {name for name, _ in tables}
    primary_keys = {name: _primary_field(fields_by_table, name) for name in table_names}

    relations: list[tuple[str, str, str, str, str]] = []
    for (table, field_name), (ref_table, ref_field) in sorted(fk_map.items()):
        relations.append((table, field_name, ref_table, ref_field, "扫描"))
    # 命名约定推导：xxx_id 字段指向以其为主键的表（补充未声明外键的关系）
    for table_name in table_names:
        for fld in fields_by_table.get(table_name, []):
            if (table_name, fld["name"]) in fk_map or not fld["name"].endswith("_id"):
                continue
            for target, pk in primary_keys.items():
                if target != table_name and pk == fld["name"]:
                    relations.append((table_name, fld["name"], target, pk, "推导"))
                    fk_map[(table_name, fld["name"])] = (target, pk)

    scanned_at = datetime.now()
    summary = {"tables": 0, "fields": 0, "relations": len(relations)}
    # schema.sql 注释只对默认数据源（forge_sight 官方库）适用
    schema_comments = load_schema_comments() if source.is_default else {}

    with PlatformSession() as session:
        first_scan = session.query(DataTable.id).filter_by(source_id=source.id).first() is None
        for table_name, comment in tables:
            fields = fields_by_table.get(table_name, [])
            pk = primary_keys.get(table_name) or ""
            table_comment = schema_comments.get(table_name, {}).get("comment") or comment or ""

            with engine.connect() as conn:
                row_count = _scalar(conn, f"SELECT COUNT(*) FROM `{table_name}`" if source.type == "mysql"
                                    else f'SELECT COUNT(*) FROM "{table_name}"')
                quote = "`" if source.type == "mysql" else '"'
                sample_row = conn.execute(
                    text(f"SELECT * FROM {quote}{table_name}{quote} LIMIT 1")
                ).mappings().first()
                data_updated = _infer_data_updated(conn, table_name, fields, quote)

            existing = session.query(DataTable).filter_by(source_id=source.id, name=table_name).first()
            if existing is None:
                existing = DataTable(name=table_name, source_id=source.id, registered=auto_register)
                session.add(existing)
            existing.comment = table_comment
            if not existing.cn_name or "?" in existing.cn_name:
                existing.cn_name = table_comment or table_name
            existing.topic = existing.topic or derive_topic(table_name)
            existing.row_count = row_count or 0
            existing.primary_key = pk
            existing.data_updated = data_updated
            existing.scanned_at = scanned_at

            session.query(TableField).filter_by(source_id=source.id, table_name=table_name).delete()
            field_comments = schema_comments.get(table_name, {}).get("fields", {})
            for fld in fields:
                sample = ""
                if sample_row is not None and fld["name"] in sample_row:
                    sample = _format_sample(sample_row[fld["name"]])
                ref = fk_map.get((table_name, fld["name"]))
                cn_name = field_comments.get(fld["name"]) or fld["comment"]
                session.add(
                    TableField(
                        source_id=source.id,
                        table_name=table_name,
                        name=fld["name"],
                        cn_name=cn_name,
                        data_type=fld["type"],
                        ordinal=fld["ordinal"],
                        is_primary=fld["is_primary"],
                        ref_table=ref[0] if ref else "",
                        comment=cn_name,
                        sample_value=sample,
                    )
                )
            summary["tables"] += 1
            summary["fields"] += len(fields)

        session.query(TableRelation).filter_by(source_id=source.id).delete()
        for from_table, from_field, to_table, to_field, source_kind in relations:
            session.add(
                TableRelation(
                    source_id=source.id,
                    from_table=from_table,
                    from_field=from_field,
                    to_table=to_table,
                    to_field=to_field,
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
    """扫描默认数据源（启动时自动首次扫描用）。"""
    with PlatformSession() as session:
        source = session.query(DataSource).filter_by(is_default=True).first()
    if source is None:
        return {"tables": 0, "fields": 0, "relations": 0}
    return scan_source(source, auto_register=auto_register)


def _primary_field(fields_by_table: dict, table_name: str) -> str | None:
    for fld in fields_by_table.get(table_name, []):
        if fld["is_primary"]:
            return fld["name"]
    return None


def _infer_data_updated(conn, table_name: str, fields: list, quote: str = "`") -> str:
    """取第一个 date/datetime 字段的 MAX 作为数据新鲜度。"""
    for fld in fields:
        if fld["type"].startswith(("date", "timestamp", "datetime")):
            value = _scalar(conn, f"SELECT MAX({quote}{fld['name']}{quote}) FROM {quote}{table_name}{quote}")
            if value is not None:
                return str(value)[:10]
    return ""


def _format_sample(value) -> str:
    if isinstance(value, (datetime, date)):
        return str(value)
    return str(value)[:100]
