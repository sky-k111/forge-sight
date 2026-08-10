"""资源中心查询服务：表目录、字段详情、数据预览、表关系。

返回结构与前端 api/resources.js 契约对齐（name/cn/topic/rows/fields/freshness/quality/key）。
"""

from app.db.models import DataSource, DataTable, TableField, TableRelation
from app.db.platform import PlatformSession
from app.gateway.executor import execute_readonly


def _default_source_id() -> int:
    with PlatformSession() as session:
        source = session.query(DataSource).filter_by(is_default=True).first()
    return source.id if source else 1


def _quality_score(table: DataTable, field_count: int) -> int:
    """简单质量分：有中文名、有主键、有注释、有行数据各计一部分。后续可扩展为真实质量规则。"""
    score = 0
    if table.cn_name and table.cn_name != table.name:
        score += 30
    if table.primary_key:
        score += 25
    if table.comment:
        score += 20
    if table.row_count > 0:
        score += 15
    if field_count > 0:
        score += 10
    return score


def list_tables(keyword: str = "", topic: str | None = None, source_id: int | None = None) -> list[dict]:
    source_id = source_id or _default_source_id()
    with PlatformSession() as session:
        tables = (
            session.query(DataTable)
            .filter_by(registered=True, source_id=source_id)
            .order_by(DataTable.name)
            .all()
        )
        # 统计每张表字段数
        counts: dict[str, int] = {}
        for row in session.query(TableField).filter(TableField.source_id == source_id):
            counts[row.table_name] = counts.get(row.table_name, 0) + 1

    items = []
    for table in tables:
        if keyword and keyword.lower() not in table.name.lower() and keyword not in table.cn_name and keyword not in table.topic:
            continue
        if topic and table.topic != topic:
            continue
        count = counts.get(table.name, 0)
        items.append(
            {
                "name": table.name,
                "cn": table.cn_name,
                "topic": table.topic,
                "rows": f"{table.row_count:,}",
                "fields": count,
                "freshness": table.data_updated,
                "quality": _quality_score(table, count),
                "key": table.primary_key,
            }
        )
    return items


def get_table_fields(table_name: str, source_id: int | None = None) -> list[dict] | None:
    source_id = source_id or _default_source_id()
    with PlatformSession() as session:
        table = session.query(DataTable).filter_by(name=table_name, source_id=source_id, registered=True).first()
        if table is None:
            return None
        fields = (
            session.query(TableField)
            .filter_by(source_id=source_id, table_name=table_name)
            .order_by(TableField.ordinal)
            .all()
        )

    return [
        {
            "name": field.name,
            "cn": field.cn_name or field.name,
            "type": field.data_type,
            "rule": (
                "主键 · 非空"
                if field.is_primary
                else f"外键 → {field.ref_table}"
                if field.ref_table
                else "已映射业务字段"
            ),
            "sample": field.sample_value,
        }
        for field in fields
    ]


def get_table_preview(table_name: str, limit: int = 20, source_id: int | None = None) -> dict | None:
    """只读预览，走 gateway（白名单 + 审计）。"""
    source_id = source_id or _default_source_id()
    with PlatformSession() as session:
        table = session.query(DataTable).filter_by(name=table_name, source_id=source_id, registered=True).first()
        source = session.query(DataSource).filter_by(id=source_id).first()
    if table is None or source is None:
        return None

    quote = "`" if source.type == "mysql" else '"'
    result = execute_readonly(
        f"SELECT * FROM {quote}{table_name}{quote} LIMIT {limit}", action="预览数据表", source=source
    )
    if not result.ok:
        return {"columns": [], "rows": [], "error": result.error}
    return {
        "columns": result.columns,
        "rows": [["" if value is None else str(value) for value in row] for row in result.rows],
    }


def list_relations(source_id: int | None = None) -> list[dict]:
    source_id = source_id or _default_source_id()
    with PlatformSession() as session:
        relations = session.query(TableRelation).filter_by(source_id=source_id).all()
    return [
        {
            "fromTable": r.from_table,
            "fromField": r.from_field,
            "toTable": r.to_table,
            "toField": r.to_field,
            "source": r.source,
        }
        for r in relations
    ]


def list_unregistered_tables(source_id: int | None = None) -> list[dict]:
    """扫描到但尚未确认登记的表（新表接入入口，对应 AC-07 前半段）。"""
    source_id = source_id or _default_source_id()
    with PlatformSession() as session:
        tables = session.query(DataTable).filter_by(registered=False, source_id=source_id).all()
    return [{"name": t.name, "rows": t.row_count, "comment": t.comment, "sourceId": t.source_id} for t in tables]
