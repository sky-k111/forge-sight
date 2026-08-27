"""Governed metric-candidate generation and review.

The LLM may propose a canonical metric or a physical binding, but it never
publishes one.  Every proposal is persisted with evidence and deterministic
validation so an administrator can review it before it enters the existing
``待审核 -> 已发布`` knowledge lifecycle.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime

import sqlglot
from sqlglot import exp

from app.audit.service import write_audit
from app.db.models import (
    DataSource,
    DataTable,
    KnowledgeMetric,
    KnowledgeTopic,
    MetricCandidate,
    MetricSourceBinding,
    TableField,
    TableRelation,
)
from app.db.platform import PlatformSession
from app.knowledge import service
from app.llm.provider import LLMError, chat_completion

GENERATOR_VERSION = "metric-candidate-v1"
VALID_STATUSES = {"pending", "accepted", "rejected", "superseded"}

_SYSTEM_PROMPT = """你是制造业指标治理助手。你的任务是根据真实、已登记的数据库元数据提出指标口径候选。
你只能引用输入中出现的表、字段和关联关系，不得编造字段或连接条件。
你可以做两类候选：
1. source_binding：为给定的企业统一指标补充当前数据源物理绑定，targetMetricId 必须来自待绑定指标列表；
2. new_metric：当数据确实支持一个有业务价值、且现有指标未覆盖的口径时提出新指标。

物理公式必须是可放入 SELECT 的 SQL 表达式，不要在 physicalFormula 中写 SELECT、GROUP BY 或 ORDER BY。
跨表候选只能使用输入中明确存在的关联路径。置信度必须反映证据完整度，不能因为文字看起来合理就给高分。
只输出 JSON，不要输出 Markdown。格式：
{
  "candidates": [{
    "kind": "source_binding 或 new_metric",
    "targetMetricId": "仅 source_binding 填写",
    "name": "指标名",
    "topic": "输入中的业务主题名",
    "definition": "业务定义",
    "formula": "企业统一业务公式",
    "physicalFormula": "当前数据源 SQL 表达式",
    "sourceTables": ["真实表名"],
    "semanticTimeField": "统一语义时间字段",
    "bindingTimeField": "真实时间字段",
    "dimensions": ["可用业务维度"],
    "synonyms": ["同义词"],
    "fieldMappings": [{"semanticField":"语义字段","tableName":"真实表名","fieldName":"真实字段名"}],
    "filterExpression": "可选，只允许只读过滤表达式",
    "precision": "展示精度",
    "confidence": 0.0,
    "reason": "引用具体表字段说明推荐依据"
  }]
}
最多返回 12 项；没有可靠候选时返回空数组。"""


def _parse_json(text: str) -> dict:
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end <= start:
            raise service.KnowledgeValidation("模型未返回可解析的指标候选")
        try:
            return json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError as exc:
            raise service.KnowledgeValidation("模型返回的指标候选不是合法 JSON") from exc


def _list(value) -> list:
    return value if isinstance(value, list) else []


def _confidence_score(value) -> int:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0
    if number <= 1:
        number *= 100
    return max(0, min(100, round(number)))


def _canonical_payload(item: dict, metric: KnowledgeMetric | None) -> dict:
    if metric is not None:
        return {
            "name": metric.name,
            "topic": metric.topic,
            "definition": metric.definition,
            "formula": metric.formula,
            "synonyms": service._json_list(metric.synonyms_json),
            "dimensions": service._json_list(metric.dimensions_json),
            "semanticTimeField": metric.time_field,
            "precision": metric.precision,
            "owner": metric.owner,
        }
    return {
        "name": str(item.get("name") or "").strip(),
        "topic": str(item.get("topic") or "").strip(),
        "definition": str(item.get("definition") or "").strip(),
        "formula": str(item.get("formula") or "").strip(),
        "synonyms": [str(value).strip() for value in _list(item.get("synonyms")) if str(value).strip()],
        "dimensions": [str(value).strip() for value in _list(item.get("dimensions")) if str(value).strip()],
        "semanticTimeField": str(item.get("semanticTimeField") or "统计时间").strip(),
        "precision": str(item.get("precision") or "").strip(),
        "owner": "数据治理组",
    }


def _binding_payload(item: dict, source: DataSource) -> dict:
    return {
        "sourceId": source.id,
        "sourceTables": [str(value).strip() for value in _list(item.get("sourceTables")) if str(value).strip()],
        "physicalFormula": str(item.get("physicalFormula") or "").strip(),
        "bindingTimeField": str(item.get("bindingTimeField") or "").strip(),
        "filterExpression": str(item.get("filterExpression") or "").strip(),
        "sqlDialect": source.type,
        "fieldMappings": [value for value in _list(item.get("fieldMappings")) if isinstance(value, dict)],
        "bindingNotes": "AI 生成候选；接受后仍需管理员审核并显式发布。",
    }


def _relation_is_connected(session, source_id: int, table_names: list[str]) -> bool:
    if len(table_names) < 2:
        return True
    allowed = set(table_names)
    graph = {name: set() for name in allowed}
    rows = session.query(TableRelation).filter_by(source_id=source_id).all()
    for row in rows:
        if row.from_table in allowed and row.to_table in allowed:
            graph[row.from_table].add(row.to_table)
            graph[row.to_table].add(row.from_table)
    seen = set()
    stack = [table_names[0]]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        stack.extend(graph[current] - seen)
    return seen == allowed


def _column_reference_errors(session, source_id: int, binding: dict, tree, label: str) -> list[str]:
    errors: list[str] = []
    table_names = binding.get("sourceTables") or []
    field_rows = session.query(TableField).filter(
        TableField.source_id == source_id,
        TableField.table_name.in_(table_names),
    ).all()
    fields_by_name: dict[str, set[str]] = {}
    for row in field_rows:
        fields_by_name.setdefault(row.name.lower(), set()).add(row.table_name.lower())
    for column in tree.find_all(exp.Column):
        name = column.name.lower()
        tables = fields_by_name.get(name, set())
        if not tables:
            errors.append(f"{label}引用了不存在的字段：{column.name}")
            continue
        qualifier = (column.table or "").lower()
        if qualifier and qualifier not in tables:
            errors.append(f"{label}中的字段归属不正确：{column.sql()}")
        elif not qualifier and len(tables) > 1:
            errors.append(f"{label}字段存在跨表歧义，请加表名前缀：{column.name}")
    return errors


def _validate_sql_expression(session, source_id: int, binding: dict) -> list[str]:
    errors: list[str] = []
    formula = binding.get("physicalFormula") or ""
    dialect = "postgres" if binding.get("sqlDialect") == "postgresql" else "mysql"
    if re.search(r"\b(SELECT|INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE)\b", formula, re.I):
        return ["物理公式只能是计算表达式，不能包含 SQL 语句"]
    try:
        tree = sqlglot.parse_one(f"SELECT {formula} AS metric_value", read=dialect)
    except sqlglot.errors.ParseError:
        return ["物理公式无法通过 SQL 语法解析"]
    errors.extend(_column_reference_errors(session, source_id, binding, tree, "物理公式"))
    filter_expression = str(binding.get("filterExpression") or "").strip()
    if filter_expression:
        if re.search(r"\b(SELECT|INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE)\b", filter_expression, re.I):
            errors.append("过滤条件只能是布尔表达式，不能包含 SQL 语句")
        else:
            try:
                filter_tree = sqlglot.parse_one(f"SELECT 1 WHERE {filter_expression}", read=dialect)
                errors.extend(_column_reference_errors(session, source_id, binding, filter_tree, "过滤条件"))
            except sqlglot.errors.ParseError:
                errors.append("过滤条件无法通过 SQL 语法解析")
    return errors


def _validate_payload(session, canonical: dict, binding: dict, tenant_id: str) -> dict:
    errors: list[str] = []
    warnings: list[str] = []
    if not canonical.get("name"):
        errors.append("缺少指标名称")
    if not canonical.get("formula"):
        errors.append("缺少企业统一业务公式")
    if session.query(KnowledgeTopic).filter_by(
        tenant_id=tenant_id,
        name=canonical.get("topic"),
    ).first() is None:
        errors.append(f"业务主题未登记：{canonical.get('topic') or '空'}")
    try:
        normalized = service._validate_metric_binding(session, {**canonical, **binding}, int(binding["sourceId"]))
        binding = {**binding, **normalized, "sourceTables": normalized["tableNames"]}
    except (service.KnowledgeValidation, KeyError, TypeError, ValueError) as exc:
        errors.append(str(exc))
    if not errors:
        errors.extend(_validate_sql_expression(session, int(binding["sourceId"]), binding))
    table_names = binding.get("sourceTables") or []
    if not errors and not _relation_is_connected(session, int(binding["sourceId"]), table_names):
        errors.append("候选使用了多张表，但数据目录中没有完整的已登记关联路径")
    if not binding.get("fieldMappings"):
        warnings.append("未提供语义字段到物理字段的映射，审核时需要重点确认")
    if not binding.get("bindingTimeField") and not binding.get("timeField"):
        warnings.append("未指定默认时间字段，趋势与周期分析将受限")
    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "checkedAt": datetime.now().isoformat(timespec="seconds"),
        "checks": {
            "registeredTablesAndFields": not any("不存在" in error or "未登记" in error for error in errors),
            "sqlExpression": not any("物理公式" in error or "过滤条件" in error for error in errors),
            "relationPath": not any("关联路径" in error for error in errors),
            "reviewRequired": True,
        },
    }


def _fingerprint(source_id: int, target_metric_id: str | None, canonical: dict, binding: dict) -> str:
    payload = {
        "sourceId": source_id,
        "targetMetricId": target_metric_id,
        "name": canonical.get("name"),
        "formula": canonical.get("formula"),
        "physicalFormula": binding.get("physicalFormula"),
        "sourceTables": sorted(binding.get("sourceTables") or []),
        "fieldMappings": binding.get("fieldMappings") or [],
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _serialize(row: MetricCandidate) -> dict:
    return {
        "id": row.id,
        "sourceId": row.source_id,
        "kind": row.candidate_kind,
        "targetMetricId": row.target_metric_id,
        "name": row.name,
        "topic": row.topic,
        "canonical": row.canonical_payload_json or {},
        "binding": row.binding_payload_json or {},
        "evidence": row.evidence_json or {},
        "validation": row.validation_json or {},
        "confidence": row.confidence_score,
        "status": row.status,
        "reviewNote": row.review_note,
        "acceptedMetricId": row.accepted_metric_id,
        "createdAt": row.created_at.isoformat(timespec="seconds") if row.created_at else None,
        "reviewedAt": row.reviewed_at.isoformat(timespec="seconds") if row.reviewed_at else None,
    }


def _generation_context(session, source_id: int) -> tuple[DataSource, str, dict[str, KnowledgeMetric]]:
    source = session.query(DataSource).filter_by(id=source_id).first()
    if source is None:
        raise service.KnowledgeValidation(f"数据源 {source_id} 不存在")
    tables = session.query(DataTable).filter_by(source_id=source_id, registered=True).order_by(DataTable.name).all()
    if not tables:
        raise service.KnowledgeValidation("当前数据源没有已确认登记的数据表，不能生成口径候选")
    field_rows = session.query(TableField).filter_by(source_id=source_id).order_by(
        TableField.table_name, TableField.ordinal
    ).all()
    fields_by_table: dict[str, list[dict]] = {table.name: [] for table in tables}
    for field in field_rows:
        if field.table_name in fields_by_table:
            fields_by_table[field.table_name].append(
                {
                    "name": field.name,
                    "cnName": field.cn_name,
                    "type": field.data_type,
                    "sample": field.sample_value,
                }
            )
    relations = [
        {
            "from": f"{row.from_table}.{row.from_field}",
            "to": f"{row.to_table}.{row.to_field}",
            "source": row.source,
        }
        for row in session.query(TableRelation).filter_by(source_id=source_id).all()
    ]
    metric_rows = session.query(KnowledgeMetric).filter_by(
        tenant_id=source.tenant_id
    ).order_by(KnowledgeMetric.id).all()
    bound_ids = {
        value for (value,) in session.query(MetricSourceBinding.metric_id).filter_by(source_id=source_id).all()
    }
    missing = {row.id: row for row in metric_rows if row.id not in bound_ids}
    context = {
        "dataSource": {"id": source.id, "name": source.name, "dialect": source.type},
        "registeredTables": [
            {
                "name": table.name,
                "cnName": table.cn_name,
                "topic": table.topic,
                "comment": table.comment,
                "fields": fields_by_table.get(table.name, []),
            }
            for table in tables
        ],
        "registeredRelations": relations,
        "businessTopics": [
            value
            for (value,) in session.query(KnowledgeTopic.name).filter_by(
                tenant_id=source.tenant_id,
                enabled=True,
            ).all()
        ],
        "metricsMissingThisSourceBinding": [
            {
                "id": row.id,
                "name": row.name,
                "topic": row.topic,
                "definition": row.definition,
                "formula": row.formula,
                "semanticTimeField": row.time_field,
                "dimensions": service._json_list(row.dimensions_json),
            }
            for row in missing.values()
        ],
        "existingMetricNames": [row.name for row in metric_rows],
    }
    return source, json.dumps(context, ensure_ascii=False), missing


def generate_candidates(source_id: int, tenant_id: str = "tenant-default") -> dict:
    with PlatformSession() as session:
        # 请求者只能为当前企业的数据源生成候选，跨租户视为不存在。
        source = session.query(DataSource).filter_by(id=source_id, tenant_id=tenant_id).first()
        if source is None:
            raise service.KnowledgeNotFound(f"数据源 {source_id} 不存在")
        source, context, missing_metrics = _generation_context(session, source_id)
    try:
        response = chat_completion(_SYSTEM_PROMPT, context, temperature=0.05, max_tokens=5000)
    except LLMError as exc:
        raise service.KnowledgeValidation(f"生成口径候选失败：{exc}") from exc
    parsed = _parse_json(response.content)
    raw_items = _list(parsed.get("candidates"))[:12]
    results: list[dict] = []
    discarded = 0
    with PlatformSession() as session:
        source = session.query(DataSource).filter_by(id=source_id).one()
        existing_names = {
            value
            for (value,) in session.query(KnowledgeMetric.name).filter_by(
                tenant_id=source.tenant_id
            ).all()
        }
        for item in raw_items:
            if not isinstance(item, dict):
                discarded += 1
                continue
            kind = str(item.get("kind") or "new_metric").strip()
            target_id = str(item.get("targetMetricId") or "").strip() or None
            target_metric = missing_metrics.get(target_id) if kind == "source_binding" else None
            if kind == "source_binding" and target_metric is None:
                discarded += 1
                continue
            canonical = _canonical_payload(item, target_metric)
            if kind == "new_metric" and canonical["name"] in existing_names:
                discarded += 1
                continue
            binding = _binding_payload(item, source)
            validation = _validate_payload(session, canonical, binding, source.tenant_id)
            fingerprint = _fingerprint(source_id, target_id, canonical, binding)
            row = session.query(MetricCandidate).filter_by(source_id=source_id, fingerprint=fingerprint).first()
            if row is None:
                row = MetricCandidate(id=str(uuid.uuid4()), source_id=source_id, fingerprint=fingerprint)
                session.add(row)
            elif row.status == "accepted":
                results.append(_serialize(row))
                continue
            row.candidate_kind = kind if kind in {"new_metric", "source_binding"} else "new_metric"
            row.target_metric_id = target_id
            row.name = canonical["name"]
            row.topic = canonical["topic"]
            row.canonical_payload_json = canonical
            row.binding_payload_json = binding
            row.evidence_json = {
                "reason": str(item.get("reason") or "").strip(),
                "providerRequestId": response.provider_request_id,
                "model": response.model,
                "referencedTables": binding.get("sourceTables") or [],
                "generatedFromRegisteredMetadataOnly": True,
            }
            row.validation_json = validation
            row.confidence_score = min(_confidence_score(item.get("confidence")), 95 if validation["valid"] else 49)
            row.status = "pending"
            row.generator_version = GENERATOR_VERSION
            row.review_note = ""
            row.reviewed_by = None
            row.reviewed_at = None
            row.updated_at = datetime.now()
            session.flush()
            results.append(_serialize(row))
        session.commit()
    write_audit(
        action=f"为数据源生成指标口径候选 {len(results)} 项",
        resource=f"data-source:{source_id}",
        result="已记录",
        role="管理员",
        tenant_id=source.tenant_id,
    )
    return {"items": results, "generated": len(results), "discarded": discarded}


def list_candidates(
    source_id: int,
    status: str | None = None,
    tenant_id: str = "tenant-default",
) -> list[dict]:
    if status and status not in VALID_STATUSES:
        raise service.KnowledgeValidation(f"不支持的候选状态：{status}")
    with PlatformSession() as session:
        service._resolve_source_id(session, source_id, tenant_id)
        query = session.query(MetricCandidate).filter_by(source_id=source_id)
        if status:
            query = query.filter_by(status=status)
        rows = query.order_by(MetricCandidate.created_at.desc()).all()
        return [_serialize(row) for row in rows]


def accept_candidate(
    candidate_id: str,
    reviewer: str = "管理员",
    tenant_id: str = "tenant-default",
) -> dict:
    with PlatformSession() as session:
        row = session.query(MetricCandidate).filter_by(id=candidate_id).first()
        if row is None:
            raise service.KnowledgeNotFound(f"口径候选 {candidate_id} 不存在")
        source = session.query(DataSource).filter_by(id=row.source_id, tenant_id=tenant_id).first()
        if source is None:
            raise service.KnowledgeNotFound(f"口径候选 {candidate_id} 不存在")
        if row.status != "pending":
            raise service.KnowledgeConflict("只有待审核候选可以接受")
        validation = _validate_payload(
            session,
            row.canonical_payload_json or {},
            row.binding_payload_json or {},
            tenant_id,
        )
        row.validation_json = validation
        session.commit()
        if not validation["valid"]:
            raise service.KnowledgeValidation("候选未通过自动校验：" + "；".join(validation["errors"]))
        metric_id = row.target_metric_id
        payload = {**(row.canonical_payload_json or {}), **(row.binding_payload_json or {})}
    saved = service.save_metric(payload, metric_id=metric_id, tenant_id=tenant_id)
    with PlatformSession() as session:
        row = session.query(MetricCandidate).filter_by(id=candidate_id).first()
        row.status = "accepted"
        row.accepted_metric_id = saved["id"]
        row.reviewed_by = reviewer
        row.reviewed_at = datetime.now()
        row.updated_at = datetime.now()
        session.commit()
        result = _serialize(row)
    write_audit(
        action=f"接受口径候选 {result['name']}，进入正式审核",
        resource=saved["id"],
        result="已记录",
        role="管理员",
        tenant_id=tenant_id,
    )
    return {"candidate": result, "metric": saved, "requiresPublish": True}


def reject_candidate(
    candidate_id: str,
    reason: str = "",
    reviewer: str = "管理员",
    tenant_id: str = "tenant-default",
) -> dict:
    with PlatformSession() as session:
        row = session.query(MetricCandidate).filter_by(id=candidate_id).first()
        if row is None:
            raise service.KnowledgeNotFound(f"口径候选 {candidate_id} 不存在")
        source = session.query(DataSource).filter_by(id=row.source_id, tenant_id=tenant_id).first()
        if source is None:
            raise service.KnowledgeNotFound(f"口径候选 {candidate_id} 不存在")
        if row.status != "pending":
            raise service.KnowledgeConflict("只有待审核候选可以驳回")
        row.status = "rejected"
        row.review_note = reason.strip()
        row.reviewed_by = reviewer
        row.reviewed_at = datetime.now()
        row.updated_at = datetime.now()
        session.commit()
        result = _serialize(row)
    write_audit(
        action=f"驳回口径候选 {result['name']}",
        resource=candidate_id,
        result="已记录",
        role="管理员",
        tenant_id=tenant_id,
    )
    return result
