"""新表语义接入（AC-07）：LLM 推荐 + 管理员确认。

原则：AI 只做推荐，必须管理员确认后才登记；推荐基于真实字段与样例，不允许 LLM 自由发挥。
"""

import json
import re

from app.db.models import DataTable, TableField
from app.db.platform import PlatformSession
from app.llm.provider import chat

_SYSTEM = """你是制造业数据底座的元数据分析助手。根据数据表的字段名、类型和样例值，推断这张表的业务含义。
只输出 JSON，不要输出任何其他文字。JSON 格式：
{
  "cn": "中文表名（如 设备传感器读数表）",
  "topic": "业务主题，从 生产/质量/设备/库存/主数据 中选一个，无法判断则为 未分类",
  "confidence": 0到1之间的置信度,
  "reason": "推断理由，一句话，需引用具体字段名",
  "field_suggestions": [{"name": "字段名", "cn": "字段中文名", "note": "如 疑似主键 / 疑似外键 → 目标表 / 单位等"}]
}"""


def _parse_json(text: str) -> dict:
    text = re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    return json.loads(text)


def suggest_table_semantics(table_name: str, source_id: int) -> dict:
    with PlatformSession() as session:
        table = session.query(DataTable).filter_by(source_id=source_id, name=table_name).first()
        if table is None:
            raise KeyError(f"数据表 {table_name} 不存在")
        fields = (
            session.query(TableField)
            .filter_by(source_id=source_id, table_name=table_name)
            .order_by(TableField.ordinal)
            .all()
        )
        field_desc = "\n".join(
            f"- {f.name} ({f.data_type}) 样例: {f.sample_value or '无'}" for f in fields
        )
        context = f"表名: {table.name}\n行数: {table.row_count}\n字段:\n{field_desc}"
        existing = session.query(DataTable).filter_by(source_id=source_id, registered=True).all()
        existing_desc = "、".join(f"{t.name}（{t.cn_name}）" for t in existing) or "无"

    raw = chat(
        _SYSTEM,
        f"库中已登记的表: {existing_desc}\n\n请分析以下未登记数据表：\n{context}",
        temperature=0.1,
    )
    result = _parse_json(raw)
    return {
        "name": table_name,
        "cn": result.get("cn", table_name),
        "topic": result.get("topic", "未分类"),
        "confidence": min(max(float(result.get("confidence", 0.5)), 0), 1),
        "reason": result.get("reason", ""),
        "fieldSuggestions": [
            {"name": item.get("name", ""), "cn": item.get("cn", ""), "note": item.get("note", "")}
            for item in result.get("field_suggestions", [])
        ],
    }


def confirm_table_semantics(table_name: str, source_id: int, suggestion: dict) -> dict:
    """管理员确认后登记：表进入资源目录并对问析可见（加入白名单）。"""
    with PlatformSession() as session:
        table = session.query(DataTable).filter_by(source_id=source_id, name=table_name).first()
        if table is None:
            raise KeyError(f"数据表 {table_name} 不存在")
        table.cn_name = suggestion.get("cn") or table.cn_name
        table.topic = suggestion.get("topic") or table.topic
        table.registered = True
        # 字段中文名一并采纳
        cn_map = {item["name"]: item["cn"] for item in suggestion.get("fieldSuggestions", []) if item.get("cn")}
        if cn_map:
            for field in session.query(TableField).filter_by(source_id=source_id, table_name=table_name):
                if field.name in cn_map:
                    field.cn_name = cn_map[field.name]
        session.commit()
        return {
            "name": table.name,
            "cn": table.cn_name,
            "topic": table.topic,
            "rows": f"{table.row_count:,}",
            "registered": table.registered,
        }
