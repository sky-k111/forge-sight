"""业务知识管理服务。

知识定义全局共享；指标到物理表的绑定按数据源维护。所有写操作都校验引用关系并写审计。
"""

from __future__ import annotations

import json
import re
from datetime import datetime

from app.audit.service import write_audit
from app.db.models import (
    BusinessObject,
    BusinessRule,
    DataSource,
    DataTable,
    KnowledgeMetric,
    KnowledgeReference,
    KnowledgeTopic,
    MetricFieldBinding,
    MetricSourceBinding,
    MetricTableBinding,
    TableField,
)
from app.db.platform import PlatformSession


class KnowledgeNotFound(KeyError):
    pass


class KnowledgeValidation(ValueError):
    pass


class KnowledgeConflict(RuntimeError):
    pass


VALID_METRIC_STATUSES = {"待审核", "已发布"}
VALID_BINDING_STATUSES = {"待验证", "已验证", "已停用"}


def _json_list(value: str) -> list[str]:
    try:
        parsed = json.loads(value or "[]")
    except json.JSONDecodeError:
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


def _dump_list(items) -> str:
    return json.dumps([str(item).strip() for item in (items or []) if str(item).strip()], ensure_ascii=False)


def _updated_label(value: datetime | None) -> str:
    return value.strftime("%m-%d %H:%M") if value else "—"


def _next_code(session, model, prefix: str) -> str:
    maximum = 0
    for (value,) in session.query(model.id).all():
        match = re.fullmatch(rf"{re.escape(prefix)}-(\d+)", value or "")
        if match:
            maximum = max(maximum, int(match.group(1)))
    return f"{prefix}-{maximum + 1:03d}"


def _default_source_id(session) -> int:
    source = session.query(DataSource).filter_by(is_default=True).first()
    if source is None:
        raise KnowledgeValidation("尚未配置默认数据源")
    return source.id


def _ensure_topic(session, topic_name: str, *, allow_global: bool = False) -> None:
    if allow_global and topic_name == "全局":
        return
    if session.query(KnowledgeTopic).filter_by(name=topic_name, enabled=True).first() is None:
        raise KnowledgeValidation(f"分析主题不存在或未启用：{topic_name}")


def _reference_count(session, entity_type: str, entity_id: str) -> int:
    return session.query(KnowledgeReference).filter_by(entity_type=entity_type, entity_id=entity_id).count()


def _block_if_referenced(session, entity_type: str, entity_id: str) -> None:
    references = session.query(KnowledgeReference).filter_by(entity_type=entity_type, entity_id=entity_id).all()
    if references:
        examples = "、".join(f"{row.consumer_type}:{row.consumer_id}" for row in references[:3])
        raise KnowledgeConflict(f"该知识项仍被 {len(references)} 个内容引用（{examples}），不能删除")


def list_topics() -> list[dict]:
    with PlatformSession() as session:
        rows = session.query(KnowledgeTopic).order_by(KnowledgeTopic.code).all()
        metric_rows = session.query(KnowledgeMetric.topic, KnowledgeMetric.name).filter_by(status="已发布").all()
    by_topic: dict[str, list[str]] = {}
    for topic, name in metric_rows:
        by_topic.setdefault(topic, []).append(name)
    return [
        {
            "name": row.name,
            "code": row.code,
            "color": row.color,
            "description": row.description,
            "metrics": by_topic.get(row.name, [])[:4],
            "coverage": row.coverage,
            "enabled": row.enabled,
        }
        for row in rows
    ]


def create_topic(payload: dict) -> dict:
    code = str(payload.get("code", "")).strip().upper()
    name = str(payload.get("name", "")).strip()
    if not code or not name:
        raise KnowledgeValidation("主题编码和名称不能为空")
    with PlatformSession() as session:
        if session.query(KnowledgeTopic).filter(
            (KnowledgeTopic.code == code) | (KnowledgeTopic.name == name)
        ).first():
            raise KnowledgeConflict("主题编码或名称已存在")
        row = KnowledgeTopic(
            code=code,
            name=name,
            color=payload.get("color") or "blue",
            description=payload.get("description") or "",
            coverage=int(payload.get("coverage") or 0),
            enabled=payload.get("enabled", True),
        )
        session.add(row)
        session.commit()
    write_audit(action=f"创建分析主题 {name}", resource=code, result="已记录", role="管理员")
    return next(item for item in list_topics() if item["code"] == code)


def update_topic(code: str, payload: dict) -> dict:
    with PlatformSession() as session:
        row = session.query(KnowledgeTopic).filter_by(code=code).first()
        if row is None:
            raise KnowledgeNotFound(f"分析主题 {code} 不存在")
        old_name = row.name
        new_name = str(payload.get("name", old_name)).strip()
        duplicate = session.query(KnowledgeTopic).filter(KnowledgeTopic.name == new_name, KnowledgeTopic.code != code).first()
        if duplicate:
            raise KnowledgeConflict(f"分析主题名称已存在：{new_name}")
        row.name = new_name
        for field in ("color", "description"):
            if field in payload and payload[field] is not None:
                setattr(row, field, payload[field])
        if "coverage" in payload and payload["coverage"] is not None:
            row.coverage = int(payload["coverage"])
        if "enabled" in payload and payload["enabled"] is not None:
            row.enabled = bool(payload["enabled"])
        if new_name != old_name:
            session.query(BusinessObject).filter_by(topic=old_name).update({"topic": new_name})
            session.query(BusinessRule).filter_by(scope=old_name).update({"scope": new_name})
            session.query(KnowledgeMetric).filter_by(topic=old_name).update({"topic": new_name, "status": "待审核"})
        session.commit()
    write_audit(action=f"修改分析主题 {code}", resource=new_name, result="已记录", role="管理员")
    return next(item for item in list_topics() if item["code"] == code)


def delete_topic(code: str) -> dict:
    with PlatformSession() as session:
        row = session.query(KnowledgeTopic).filter_by(code=code).first()
        if row is None:
            raise KnowledgeNotFound(f"分析主题 {code} 不存在")
        _block_if_referenced(session, "topic", code)
        dependencies = (
            session.query(BusinessObject).filter_by(topic=row.name).count()
            + session.query(BusinessRule).filter_by(scope=row.name).count()
            + session.query(KnowledgeMetric).filter_by(topic=row.name).count()
        )
        if dependencies:
            raise KnowledgeConflict(f"主题“{row.name}”下仍有 {dependencies} 个知识项，不能删除")
        session.delete(row)
        session.commit()
    write_audit(action=f"删除分析主题 {code}", resource=code, result="已记录", role="管理员")
    return {"ok": True}


def _serialize_object(row: BusinessObject) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "topic": row.topic,
        "relationCount": row.relation_count,
        "description": row.description,
        "enabled": row.enabled,
    }


def list_objects(keyword: str = "", topic: str | None = None) -> list[dict]:
    with PlatformSession() as session:
        rows = session.query(BusinessObject).order_by(BusinessObject.id).all()
    text = keyword.strip().lower()
    return [
        _serialize_object(row)
        for row in rows
        if (not text or text in row.name.lower() or text in row.id.lower()) and (not topic or row.topic == topic)
    ]


def create_object(payload: dict) -> dict:
    object_id = str(payload.get("id", "")).strip()
    name = str(payload.get("name", "")).strip()
    topic = str(payload.get("topic", "全局")).strip()
    if not object_id or not name:
        raise KnowledgeValidation("业务对象ID和名称不能为空")
    with PlatformSession() as session:
        _ensure_topic(session, topic, allow_global=True)
        if session.query(BusinessObject).filter(
            (BusinessObject.id == object_id) | (BusinessObject.name == name)
        ).first():
            raise KnowledgeConflict("业务对象ID或名称已存在")
        row = BusinessObject(
            id=object_id,
            name=name,
            topic=topic,
            description=payload.get("description") or "",
            relation_count=int(payload.get("relationCount") or 0),
            enabled=payload.get("enabled", True),
        )
        session.add(row)
        session.commit()
        result = _serialize_object(row)
    write_audit(action=f"创建业务对象 {name}", resource=object_id, result="已记录", role="管理员")
    return result


def update_object(object_id: str, payload: dict) -> dict:
    with PlatformSession() as session:
        row = session.query(BusinessObject).filter_by(id=object_id).first()
        if row is None:
            raise KnowledgeNotFound(f"业务对象 {object_id} 不存在")
        old_name = row.name
        new_name = str(payload.get("name", old_name)).strip()
        topic = str(payload.get("topic", row.topic)).strip()
        _ensure_topic(session, topic, allow_global=True)
        duplicate = session.query(BusinessObject).filter(BusinessObject.name == new_name, BusinessObject.id != object_id).first()
        if duplicate:
            raise KnowledgeConflict(f"业务对象名称已存在：{new_name}")
        row.name = new_name
        row.topic = topic
        if "description" in payload:
            row.description = payload["description"] or ""
        if "relationCount" in payload and payload["relationCount"] is not None:
            row.relation_count = int(payload["relationCount"])
        if "enabled" in payload and payload["enabled"] is not None:
            row.enabled = bool(payload["enabled"])
        if new_name != old_name:
            for metric in session.query(KnowledgeMetric).all():
                dimensions = _json_list(metric.dimensions_json)
                if old_name in dimensions:
                    metric.dimensions_json = _dump_list(new_name if item == old_name else item for item in dimensions)
                    metric.status = "待审核"
        session.commit()
        result = _serialize_object(row)
    write_audit(action=f"修改业务对象 {object_id}", resource=new_name, result="已记录", role="管理员")
    return result


def delete_object(object_id: str) -> dict:
    with PlatformSession() as session:
        row = session.query(BusinessObject).filter_by(id=object_id).first()
        if row is None:
            raise KnowledgeNotFound(f"业务对象 {object_id} 不存在")
        _block_if_referenced(session, "object", object_id)
        used_by = [metric.id for metric in session.query(KnowledgeMetric).all() if row.name in _json_list(metric.dimensions_json)]
        if used_by:
            raise KnowledgeConflict(f"业务对象“{row.name}”仍被指标 {', '.join(used_by)} 作为分析维度引用，不能删除")
        session.delete(row)
        session.commit()
    write_audit(action=f"删除业务对象 {object_id}", resource=object_id, result="已记录", role="管理员")
    return {"ok": True}


def _serialize_rule(row: BusinessRule) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "type": row.rule_type,
        "scope": row.scope,
        "content": row.content,
        "enabled": row.enabled,
    }


def list_rules(topic: str | None = None) -> list[dict]:
    with PlatformSession() as session:
        rows = session.query(BusinessRule).order_by(BusinessRule.id).all()
    return [_serialize_rule(row) for row in rows if not topic or row.scope == topic]


def create_rule(payload: dict) -> dict:
    name = str(payload.get("name", "")).strip()
    scope = str(payload.get("scope", "全局")).strip()
    if not name:
        raise KnowledgeValidation("业务规则名称不能为空")
    with PlatformSession() as session:
        _ensure_topic(session, scope, allow_global=True)
        rule_id = str(payload.get("id") or _next_code(session, BusinessRule, "R")).strip()
        if session.query(BusinessRule).filter((BusinessRule.id == rule_id) | (BusinessRule.name == name)).first():
            raise KnowledgeConflict("业务规则ID或名称已存在")
        row = BusinessRule(
            id=rule_id,
            name=name,
            rule_type=payload.get("type") or "业务规则",
            scope=scope,
            content=payload.get("content") or "",
            enabled=payload.get("enabled", True),
        )
        session.add(row)
        session.commit()
        result = _serialize_rule(row)
    write_audit(action=f"创建业务规则 {name}", resource=rule_id, result="已记录", role="管理员")
    return result


def update_rule(rule_id: str, payload: dict) -> dict:
    with PlatformSession() as session:
        row = session.query(BusinessRule).filter_by(id=rule_id).first()
        if row is None:
            raise KnowledgeNotFound(f"业务规则 {rule_id} 不存在")
        scope = str(payload.get("scope", row.scope)).strip()
        _ensure_topic(session, scope, allow_global=True)
        for key, attr in (("name", "name"), ("type", "rule_type"), ("content", "content")):
            if key in payload and payload[key] is not None:
                setattr(row, attr, payload[key])
        row.scope = scope
        if "enabled" in payload and payload["enabled"] is not None:
            row.enabled = bool(payload["enabled"])
        session.commit()
        result = _serialize_rule(row)
    write_audit(action=f"修改业务规则 {rule_id}", resource=rule_id, result="已记录", role="管理员")
    return result


def delete_rule(rule_id: str) -> dict:
    with PlatformSession() as session:
        row = session.query(BusinessRule).filter_by(id=rule_id).first()
        if row is None:
            raise KnowledgeNotFound(f"业务规则 {rule_id} 不存在")
        _block_if_referenced(session, "rule", rule_id)
        session.delete(row)
        session.commit()
    write_audit(action=f"删除业务规则 {rule_id}", resource=rule_id, result="已记录", role="管理员")
    return {"ok": True}


def _source_names(value) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [part.strip() for part in re.split(r"\s*[+，,]\s*", str(value or "")) if part.strip()]


def _resolve_source_id(session, source_id: int | None = None) -> int:
    resolved = int(source_id or _default_source_id(session))
    if session.query(DataSource).filter_by(id=resolved).first() is None:
        raise KnowledgeValidation(f"数据源 {resolved} 不存在")
    return resolved


def _normalize_field_mappings(value) -> list[dict]:
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        if not isinstance(item, dict):
            continue
        semantic_field = str(item.get("semanticField") or "").strip()
        table_name = str(item.get("tableName") or "").strip()
        field_name = str(item.get("fieldName") or "").strip()
        if semantic_field and table_name and field_name:
            result.append(
                {
                    "semanticField": semantic_field,
                    "tableName": table_name,
                    "fieldName": field_name,
                    "transformExpression": str(item.get("transformExpression") or "").strip(),
                }
            )
    return result


def _validate_metric_binding(session, payload: dict, source_id: int) -> dict:
    table_names = _source_names(payload.get("sourceTables") or payload.get("source"))
    if not table_names:
        raise KnowledgeValidation("指标至少需要绑定一张数据表")
    rows = session.query(DataTable.name).filter(
        DataTable.source_id == source_id,
        DataTable.registered.is_(True),
        DataTable.name.in_(table_names),
    ).all()
    found = {row[0] for row in rows}
    missing = [name for name in table_names if name not in found]
    if missing:
        raise KnowledgeValidation(f"指标引用了未登记的数据表：{', '.join(missing)}")
    physical_formula = str(payload.get("physicalFormula") or payload.get("formula") or "").strip()
    if not physical_formula:
        raise KnowledgeValidation("数据源绑定必须提供物理计算表达式")
    time_field = str(
        payload.get("bindingTimeField") or payload.get("physicalTimeField") or payload.get("timeField") or ""
    ).strip()
    if time_field:
        exists = session.query(TableField).filter(
            TableField.source_id == source_id,
            TableField.table_name.in_(table_names),
            TableField.name == time_field,
        ).first()
        if exists is None:
            raise KnowledgeValidation(f"默认时间字段 {time_field} 不存在于已绑定数据表")
    field_mappings = _normalize_field_mappings(payload.get("fieldMappings"))
    for mapping in field_mappings:
        if mapping["tableName"] not in table_names:
            raise KnowledgeValidation(
                f"语义字段 {mapping['semanticField']} 引用了未绑定的数据表 {mapping['tableName']}"
            )
        field = session.query(TableField).filter_by(
            source_id=source_id,
            table_name=mapping["tableName"],
            name=mapping["fieldName"],
        ).first()
        if field is None:
            raise KnowledgeValidation(
                f"语义字段 {mapping['semanticField']} 引用了不存在的物理字段 "
                f"{mapping['tableName']}.{mapping['fieldName']}"
            )
    source = session.query(DataSource).filter_by(id=source_id).first()
    return {
        "tableNames": table_names,
        "physicalFormula": physical_formula,
        "timeField": time_field,
        "filterExpression": str(payload.get("filterExpression") or "").strip(),
        "sqlDialect": str(payload.get("sqlDialect") or source.type or "mysql").strip(),
        "fieldMappings": field_mappings,
        "notes": str(payload.get("bindingNotes") or payload.get("notes") or "").strip(),
    }


def _binding_for_source(session, metric_id: str, source_id: int | None) -> MetricSourceBinding | None:
    if source_id is None:
        return None
    return session.query(MetricSourceBinding).filter_by(metric_id=metric_id, source_id=source_id).first()


def _serialize_binding(session, binding: MetricSourceBinding) -> dict:
    tables = session.query(MetricTableBinding).filter_by(binding_id=binding.id).order_by(MetricTableBinding.id).all()
    fields = session.query(MetricFieldBinding).filter_by(binding_id=binding.id).order_by(MetricFieldBinding.id).all()
    source = session.query(DataSource).filter_by(id=binding.source_id).first()
    return {
        "id": binding.id,
        "sourceId": binding.source_id,
        "sourceName": source.name if source else f"数据源 {binding.source_id}",
        "sourceTables": [item.table_name for item in tables],
        "physicalFormula": binding.physical_formula,
        "timeField": binding.time_field,
        "filterExpression": binding.filter_expression,
        "sqlDialect": binding.sql_dialect,
        "bindingStatus": binding.status,
        "bindingVersion": binding.version,
        "bindingNotes": binding.notes,
        "validatedAt": _updated_label(binding.validated_at),
        "fieldMappings": [
            {
                "semanticField": item.semantic_field,
                "tableName": item.table_name,
                "fieldName": item.field_name,
                "transformExpression": item.transform_expression,
            }
            for item in fields
        ],
    }


def _serialize_metric(session, row: KnowledgeMetric, source_id: int | None = None) -> dict:
    binding_rows = session.query(MetricSourceBinding).filter_by(metric_id=row.id).order_by(
        MetricSourceBinding.source_id
    ).all()
    selected = _binding_for_source(session, row.id, source_id)
    selected_data = _serialize_binding(session, selected) if selected else None
    sources = selected_data["sourceTables"] if selected_data else []
    references = _reference_count(session, "metric", row.id)
    warnings = []
    if row.status != "已发布":
        warnings.append("企业统一指标口径尚未发布")
    if selected is None:
        warnings.append("当前数据源尚未配置物理绑定")
    elif selected.status != "已验证":
        warnings.append("当前数据源物理绑定尚未验证")
    warning = "；".join(warnings)
    return {
        "id": row.id,
        "name": row.name,
        "topic": row.topic,
        "formula": row.formula,
        "source": " + ".join(sources),
        "sourceTables": sources,
        "sourceId": source_id,
        "sourceName": selected_data["sourceName"] if selected_data else "",
        "physicalFormula": selected_data["physicalFormula"] if selected_data else "",
        "bindingStatus": selected_data["bindingStatus"] if selected_data else "未绑定",
        "bindingVersion": selected_data["bindingVersion"] if selected_data else None,
        "filterExpression": selected_data["filterExpression"] if selected_data else "",
        "sqlDialect": selected_data["sqlDialect"] if selected_data else "",
        "fieldMappings": selected_data["fieldMappings"] if selected_data else [],
        "bindings": [_serialize_binding(session, binding) for binding in binding_rows],
        "status": row.status,
        "owner": row.owner,
        "updated": _updated_label(row.updated_at),
        "definition": row.definition,
        "synonyms": _json_list(row.synonyms_json),
        "dimensions": _json_list(row.dimensions_json),
        "semanticTimeField": row.time_field,
        "timeField": selected_data["timeField"] if selected_data else "",
        "precision": row.precision,
        "version": row.version,
        "referenceCount": references,
        "availableForAnalysis": row.status == "已发布" and selected is not None and selected.status == "已验证",
        "usageWarning": warning,
    }


def list_metrics(
    keyword: str = "",
    topic: str | None = None,
    status: str | None = None,
    source_id: int | None = None,
) -> list[dict]:
    with PlatformSession() as session:
        source_id = _resolve_source_id(session, source_id)
        query = session.query(KnowledgeMetric).order_by(KnowledgeMetric.id)
        rows = query.all()
        text = keyword.strip().lower()
        return [
            _serialize_metric(session, row, source_id)
            for row in rows
            if (not text or text in row.name.lower() or text in row.id.lower() or text in row.synonyms_json.lower())
            and (not topic or row.topic == topic)
            and (not status or row.status == status)
        ]


def get_metric(metric_id: str, source_id: int | None = None) -> dict:
    with PlatformSession() as session:
        source_id = _resolve_source_id(session, source_id)
        row = session.query(KnowledgeMetric).filter_by(id=metric_id).first()
        if row is None:
            raise KnowledgeNotFound(f"指标 {metric_id} 不存在")
        return _serialize_metric(session, row, source_id)


def save_metric(payload: dict, metric_id: str | None = None) -> dict:
    name = str(payload.get("name", "")).strip()
    formula = str(payload.get("formula", "")).strip()
    topic = str(payload.get("topic", "")).strip()
    if not name or not formula or not topic:
        raise KnowledgeValidation("指标名称、主题和计算公式不能为空")
    with PlatformSession() as session:
        _ensure_topic(session, topic)
        source_id = _resolve_source_id(session, payload.get("sourceId"))
        binding_data = _validate_metric_binding(session, payload, source_id)
        row = session.query(KnowledgeMetric).filter_by(id=metric_id).first() if metric_id else None
        duplicate = session.query(KnowledgeMetric).filter(KnowledgeMetric.name == name)
        if row is not None:
            duplicate = duplicate.filter(KnowledgeMetric.id != row.id)
        if duplicate.first():
            raise KnowledgeConflict(f"指标名称已存在：{name}")
        created = row is None
        semantic_time_field = str(payload.get("semanticTimeField") or (row.time_field if row else "统计时间")).strip()
        if created:
            code = str(payload.get("id") or _next_code(session, KnowledgeMetric, "M")).strip()
            if session.query(KnowledgeMetric).filter_by(id=code).first():
                raise KnowledgeConflict(f"指标ID已存在：{code}")
            row = KnowledgeMetric(id=code, name=name, topic=topic, formula=formula, status="待审核")
            session.add(row)
        else:
            canonical_changed = any(
                [
                    row.name != name,
                    row.topic != topic,
                    row.formula != formula,
                    row.definition != (payload.get("definition") or ""),
                    row.synonyms_json != _dump_list(payload.get("synonyms")),
                    row.dimensions_json != _dump_list(payload.get("dimensions")),
                    row.time_field != semantic_time_field,
                    row.precision != (payload.get("precision") or ""),
                ]
            )
            if canonical_changed:
                row.version += 1
                row.status = "待审核"
                row.published_at = None
                session.query(MetricSourceBinding).filter_by(metric_id=row.id).update(
                    {"status": "待验证", "validated_at": None}
                )
        row.name = name
        row.topic = topic
        row.formula = formula
        row.definition = payload.get("definition") or ""
        row.synonyms_json = _dump_list(payload.get("synonyms"))
        row.dimensions_json = _dump_list(payload.get("dimensions"))
        row.time_field = semantic_time_field
        row.precision = payload.get("precision") or ""
        row.owner = payload.get("owner") or "数据治理组"
        row.updated_at = datetime.now()
        session.flush()
        binding = _binding_for_source(session, row.id, source_id)
        if binding is None:
            binding = MetricSourceBinding(
                metric_id=row.id,
                source_id=source_id,
                physical_formula=binding_data["physicalFormula"],
                status="待验证",
            )
            session.add(binding)
            session.flush()
        else:
            binding_changed = any(
                [
                    binding.physical_formula != binding_data["physicalFormula"],
                    binding.time_field != binding_data["timeField"],
                    binding.filter_expression != binding_data["filterExpression"],
                    binding.sql_dialect != binding_data["sqlDialect"],
                ]
            )
            if binding_changed:
                binding.version += 1
                binding.status = "待验证"
                binding.validated_at = None
        binding.physical_formula = binding_data["physicalFormula"]
        binding.time_field = binding_data["timeField"]
        binding.filter_expression = binding_data["filterExpression"]
        binding.sql_dialect = binding_data["sqlDialect"]
        binding.notes = binding_data["notes"]
        binding.updated_at = datetime.now()
        session.query(MetricTableBinding).filter_by(binding_id=binding.id).delete()
        session.query(MetricFieldBinding).filter_by(binding_id=binding.id).delete()
        for table_name in binding_data["tableNames"]:
            session.add(MetricTableBinding(binding_id=binding.id, table_name=table_name))
        for mapping in binding_data["fieldMappings"]:
            session.add(
                MetricFieldBinding(
                    binding_id=binding.id,
                    semantic_field=mapping["semanticField"],
                    table_name=mapping["tableName"],
                    field_name=mapping["fieldName"],
                    transform_expression=mapping["transformExpression"],
                )
            )
        session.commit()
        result = _serialize_metric(session, row, source_id)
    action = "创建指标" if created else "修改指标并退回待审核"
    write_audit(action=f"{action} {name}", resource=result["id"], result="已记录", role="管理员")
    return result


def publish_metric(metric_id: str, source_id: int | None = None) -> dict:
    with PlatformSession() as session:
        source_id = _resolve_source_id(session, source_id)
        row = session.query(KnowledgeMetric).filter_by(id=metric_id).first()
        if row is None:
            raise KnowledgeNotFound(f"指标 {metric_id} 不存在")
        binding = _binding_for_source(session, metric_id, source_id)
        if binding is None:
            raise KnowledgeValidation("指标未绑定当前数据源，不能发布")
        tables = session.query(MetricTableBinding).filter_by(binding_id=binding.id).all()
        if not tables or not binding.physical_formula:
            raise KnowledgeValidation("当前数据源绑定不完整，不能发布")
        if row.status not in VALID_METRIC_STATUSES:
            raise KnowledgeValidation(f"不支持的指标状态：{row.status}")
        row.status = "已发布"
        row.published_at = datetime.now()
        row.updated_at = datetime.now()
        binding.status = "已验证"
        binding.validated_at = datetime.now()
        binding.updated_at = datetime.now()
        session.commit()
        result = _serialize_metric(session, row, source_id)
    write_audit(action=f"发布指标 {row.name}", resource=metric_id, result="已记录", role="管理员")
    return result


def submit_metric_for_review(metric_id: str, source_id: int | None = None) -> dict:
    with PlatformSession() as session:
        source_id = _resolve_source_id(session, source_id)
        row = session.query(KnowledgeMetric).filter_by(id=metric_id).first()
        if row is None:
            raise KnowledgeNotFound(f"指标 {metric_id} 不存在")
        row.status = "待审核"
        row.published_at = None
        row.updated_at = datetime.now()
        binding = _binding_for_source(session, metric_id, source_id)
        if binding:
            binding.status = "待验证"
            binding.validated_at = None
        session.commit()
        result = _serialize_metric(session, row, source_id)
    write_audit(action=f"指标退回待审核 {row.name}", resource=metric_id, result="已记录", role="管理员")
    return result


def delete_metric(metric_id: str) -> dict:
    with PlatformSession() as session:
        row = session.query(KnowledgeMetric).filter_by(id=metric_id).first()
        if row is None:
            raise KnowledgeNotFound(f"指标 {metric_id} 不存在")
        _block_if_referenced(session, "metric", metric_id)
        bindings = session.query(MetricSourceBinding).filter_by(metric_id=metric_id).all()
        for binding in bindings:
            session.query(MetricFieldBinding).filter_by(binding_id=binding.id).delete()
            session.query(MetricTableBinding).filter_by(binding_id=binding.id).delete()
            session.delete(binding)
        session.delete(row)
        session.commit()
    write_audit(action=f"删除指标 {metric_id}", resource=metric_id, result="已记录", role="管理员")
    return {"ok": True}


def get_metric_for_analysis(metric_id: str, source_id: int | None = None) -> dict:
    """阶段4/5调用入口：待审核指标可以返回，但必须携带强制提示。"""
    return get_metric(metric_id, source_id=source_id)
