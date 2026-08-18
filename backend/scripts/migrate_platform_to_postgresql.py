"""把 ForgeSight 平台库从 MySQL 迁移到 PostgreSQL，并升级为两层指标模型。

安全约束：
1. 总是先把 MySQL 平台库完整导出为 JSON 备份；
2. 目标库存在业务数据时拒绝覆盖；
3. 只有迁移计数与数据源隔离检查全部通过后，才允许显式清理 MySQL 知识表；
4. 官方业务库 forge_sight 永远不在本脚本的写入范围内。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote_plus

from sqlalchemy import Integer, MetaData, create_engine, inspect, select, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402
from app.db.models import (  # noqa: E402
    Base,
    KnowledgeMetric,
    KnowledgeReference,
    MetricFieldBinding,
    MetricSourceBinding,
    MetricTableBinding,
)
from app.knowledge.seed import METRICS  # noqa: E402

OLD_KNOWLEDGE_TABLES = [
    "knowledge_reference",
    "metric_source_binding",
    "knowledge_metric",
    "business_rule",
    "business_object",
    "knowledge_topic",
]
PLATFORM_TABLES = ["audit_log", "data_source", "data_table", "table_field", "table_relation"]
CANONICAL_TABLES = ["knowledge_topic", "business_object", "business_rule", "knowledge_metric", "knowledge_reference"]


def _json_default(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TypeError(f"不支持的 JSON 类型：{type(value).__name__}")


def _rows(engine: Engine, table_name: str) -> list[dict]:
    metadata = MetaData()
    metadata.reflect(bind=engine, only=[table_name])
    table = metadata.tables[table_name]
    with engine.connect() as conn:
        return [dict(row) for row in conn.execute(select(table)).mappings()]


def backup_mysql(engine: Engine) -> Path:
    backup_dir = ROOT / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = backup_dir / f"mysql_platform_before_postgresql_{stamp}.json"
    inspector = inspect(engine)
    payload = {
        "createdAt": datetime.now().isoformat(),
        "source": {"driver": engine.url.drivername, "host": engine.url.host, "database": engine.url.database},
        "tables": {name: _rows(engine, name) for name in inspector.get_table_names()},
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8")
    return path


def ensure_postgres_database(admin_url: str, target_db: str) -> str:
    url = make_url(admin_url)
    if not url.drivername.startswith("postgresql"):
        raise RuntimeError("PostgreSQL 管理连接必须使用 postgresql 驱动")
    admin_engine = create_engine(url.set(database=url.database or "postgres"), isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as conn:
        exists = conn.execute(text("SELECT 1 FROM pg_database WHERE datname=:name"), {"name": target_db}).scalar()
        if not exists:
            safe_name = target_db.replace('"', '""')
            conn.execute(text(f'CREATE DATABASE "{safe_name}" ENCODING \'UTF8\''))
    admin_engine.dispose()
    return url.set(database=target_db).render_as_string(hide_password=False)


def ensure_empty_target(engine: Engine) -> None:
    tables = inspect(engine).get_table_names()
    if not tables:
        return
    with engine.connect() as conn:
        occupied = []
        for table in tables:
            try:
                if conn.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar_one():
                    occupied.append(table)
            except Exception:
                occupied.append(table)
        if occupied:
            raise RuntimeError(f"目标 PostgreSQL 库不是空库，拒绝覆盖：{', '.join(occupied)}")


def copy_same_shape(source: Engine, target: Engine, table_name: str) -> int:
    rows = _rows(source, table_name)
    if not rows:
        return 0
    target_table = Base.metadata.tables[table_name]
    allowed = {column.name for column in target_table.columns}
    values = [{key: value for key, value in row.items() if key in allowed} for row in rows]
    with target.begin() as conn:
        conn.execute(target_table.insert(), values)
    return len(values)


def _seed_metric(metric_id: str) -> dict | None:
    return next((item for item in METRICS if item["id"] == metric_id), None)


def migrate_knowledge(source: Engine, target: Engine) -> dict[str, int]:
    for table in CANONICAL_TABLES[:-2]:
        copy_same_shape(source, target, table)

    source_metrics = _rows(source, "knowledge_metric")
    source_references = _rows(source, "knowledge_reference")
    old_bindings = _rows(source, "metric_source_binding")
    old_fields = _rows(source, "table_field")
    source_rows = {row["id"]: row for row in _rows(source, "data_source")}
    fields_by_source_table: dict[tuple[int, str], dict[str, str]] = defaultdict(dict)
    for row in old_fields:
        fields_by_source_table[(row["source_id"], row["table_name"])][row["name"]] = row.get("cn_name") or row["name"]

    TargetSession = sessionmaker(bind=target, autoflush=False, expire_on_commit=False)
    with TargetSession() as session:
        for old in source_metrics:
            seed = _seed_metric(old["id"])
            session.add(
                KnowledgeMetric(
                    id=old["id"],
                    name=old["name"],
                    topic=old["topic"],
                    definition=old.get("definition") or "",
                    formula=seed["formula"] if seed else old["formula"],
                    synonyms_json=old.get("synonyms_json") or "[]",
                    dimensions_json=old.get("dimensions_json") or "[]",
                    time_field=seed["semantic_time_field"] if seed else "统计时间",
                    precision=old.get("precision") or "",
                    owner=old.get("owner") or "数据治理组",
                    status=old.get("status") or "待审核",
                    version=old.get("version") or 1,
                    created_at=old.get("created_at") or datetime.now(),
                    updated_at=old.get("updated_at") or datetime.now(),
                    published_at=old.get("published_at"),
                )
            )
        session.flush()

        grouped: dict[tuple[str, int], list[str]] = defaultdict(list)
        for row in old_bindings:
            grouped[(row["metric_id"], row["source_id"])].append(row["table_name"])

        binding_count = table_binding_count = field_binding_count = 0
        old_metrics_by_id = {row["id"]: row for row in source_metrics}
        for (metric_id, source_id), table_names in grouped.items():
            old_metric = old_metrics_by_id[metric_id]
            seed = _seed_metric(metric_id)
            source_info = source_rows.get(source_id, {})
            binding = MetricSourceBinding(
                metric_id=metric_id,
                source_id=source_id,
                physical_formula=seed["physical_formula"] if seed else old_metric["formula"],
                time_field=seed["time_field"] if seed else old_metric.get("time_field") or "",
                filter_expression=seed.get("filter_expression", "") if seed else "",
                sql_dialect=source_info.get("type") or "mysql",
                status="已验证" if old_metric.get("status") == "已发布" else "待验证",
                version=1,
                validated_at=datetime.now() if old_metric.get("status") == "已发布" else None,
            )
            session.add(binding)
            session.flush()
            binding_count += 1
            for table_name in dict.fromkeys(table_names):
                session.add(MetricTableBinding(binding_id=binding.id, table_name=table_name))
                table_binding_count += 1

            if seed:
                mappings = seed["fields"]
            else:
                mappings = []
                physical_formula = old_metric.get("formula") or ""
                for table_name in table_names:
                    for field_name, cn_name in fields_by_source_table[(source_id, table_name)].items():
                        if field_name in physical_formula:
                            mappings.append((cn_name, table_name, field_name))
            for semantic_field, table_name, field_name in mappings:
                if table_name in table_names and field_name in fields_by_source_table[(source_id, table_name)]:
                    session.add(
                        MetricFieldBinding(
                            binding_id=binding.id,
                            semantic_field=semantic_field,
                            table_name=table_name,
                            field_name=field_name,
                        )
                    )
                    field_binding_count += 1

        for row in source_references:
            session.add(KnowledgeReference(**{key: value for key, value in row.items() if key != "id"}, id=row["id"]))
        session.commit()

    return {
        "knowledge_metric": len(source_metrics),
        "metric_source_binding": binding_count,
        "metric_table_binding": table_binding_count,
        "metric_field_binding": field_binding_count,
        "knowledge_reference": len(source_references),
    }


def reset_postgres_sequences(engine: Engine) -> None:
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            pk_columns = list(table.primary_key.columns)
            if (
                len(pk_columns) != 1
                or pk_columns[0].name != "id"
                or not isinstance(pk_columns[0].type, Integer)
            ):
                continue
            name = table.name
            conn.execute(
                text(
                    "SELECT setval(pg_get_serial_sequence(:table, 'id'), "
                    f"COALESCE((SELECT MAX(id) FROM \"{name}\"), 1), "
                    f"EXISTS(SELECT 1 FROM \"{name}\"))"
                ),
                {"table": name},
            )


def verify(source: Engine, target: Engine) -> dict[str, int]:
    result = {}
    with source.connect() as src, target.connect() as dst:
        for table in PLATFORM_TABLES + CANONICAL_TABLES:
            src_count = src.execute(text(f"SELECT COUNT(*) FROM `{table}`")).scalar_one()
            dst_count = dst.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar_one()
            if src_count != dst_count:
                raise RuntimeError(f"表 {table} 计数不一致：MySQL={src_count}, PostgreSQL={dst_count}")
            result[table] = dst_count
        source_count = dst.execute(text('SELECT COUNT(*) FROM "data_source"')).scalar_one()
        isolated = dst.execute(
            text(
                'SELECT COUNT(*) FROM "metric_source_binding" b '
                'JOIN "data_source" s ON s.id=b.source_id'
            )
        ).scalar_one()
        binding_count = dst.execute(text('SELECT COUNT(*) FROM "metric_source_binding"')).scalar_one()
        if isolated != binding_count or source_count == 0:
            raise RuntimeError("存在没有归属到有效数据源的指标物理绑定")
        if dst.execute(text('SELECT COUNT(*) FROM "metric_table_binding"')).scalar_one() == 0:
            raise RuntimeError("物理表绑定为空")
        if dst.execute(text('SELECT COUNT(*) FROM "metric_field_binding"')).scalar_one() == 0:
            raise RuntimeError("语义字段到物理字段的映射为空")
    return result


def cleanup_mysql_knowledge(source: Engine) -> None:
    inspector = inspect(source)
    existing = set(inspector.get_table_names())
    with source.begin() as conn:
        for table in OLD_KNOWLEDGE_TABLES:
            if table in existing:
                conn.execute(text(f"DELETE FROM `{table}`"))
    with source.connect() as conn:
        leftovers = {
            table: conn.execute(text(f"SELECT COUNT(*) FROM `{table}`")).scalar_one()
            for table in OLD_KNOWLEDGE_TABLES
            if table in existing
        }
    if any(leftovers.values()):
        raise RuntimeError(f"MySQL 知识数据清理不完整：{leftovers}")


def _admin_url_from_env() -> str:
    explicit = os.getenv("POSTGRES_ADMIN_DB_URL", "").strip()
    if explicit:
        return explicit
    password = os.getenv("POSTGRES_PASSWORD", "")
    if not password:
        raise RuntimeError("请设置 POSTGRES_PASSWORD 或 POSTGRES_ADMIN_DB_URL")
    return f"postgresql+psycopg2://postgres:{quote_plus(password)}@127.0.0.1:5432/postgres"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-db", default="forge_sight_app")
    parser.add_argument("--cleanup-source", action="store_true")
    parser.add_argument("--confirm-cleanup", default="")
    args = parser.parse_args()

    if not settings.platform_db_url.startswith("mysql"):
        raise RuntimeError("当前 PLATFORM_DB_URL 不是 MySQL，无法识别迁移源")
    source = create_engine(settings.platform_db_url, pool_pre_ping=True)
    backup = backup_mysql(source)
    print(f"MySQL 全量备份完成：{backup}")

    target_url = ensure_postgres_database(_admin_url_from_env(), args.target_db)
    target = create_engine(target_url, pool_pre_ping=True)
    ensure_empty_target(target)
    Base.metadata.create_all(target)

    copied = {table: copy_same_shape(source, target, table) for table in PLATFORM_TABLES}
    copied.update(migrate_knowledge(source, target))
    reset_postgres_sequences(target)
    verified = verify(source, target)
    print("PostgreSQL 迁移验证通过：", json.dumps({**copied, **verified}, ensure_ascii=False))
    print("目标连接：", make_url(target_url).render_as_string(hide_password=True))

    if args.cleanup_source:
        if args.confirm_cleanup != "DELETE_MYSQL_KNOWLEDGE":
            raise RuntimeError("清理源库必须同时传入 --confirm-cleanup DELETE_MYSQL_KNOWLEDGE")
        cleanup_mysql_knowledge(source)
        print("MySQL 旧知识数据已清理；官方业务库未被修改")


if __name__ == "__main__":
    main()
