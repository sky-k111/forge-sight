"""阶段 4：将自然语言问题检索为可信、最小化的语义上下文包。

检索只读取平台库中已登记的元数据和已发布、已验证的指标绑定。返回结果按
source_id 严格隔离，并显式给出后续 SQL 生成允许使用的表、字段和关联路径。
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import deque
from datetime import datetime

from app.audit.service import write_audit
from app.config import settings
from app.db.models import (
    BusinessObject,
    BusinessRule,
    DataSource,
    DataTable,
    KnowledgeMetric,
    MetricFieldBinding,
    MetricSourceBinding,
    MetricTableBinding,
    TableField,
    TableRelation,
)
from app.db.platform import PlatformSession
from app.retrieval import semantic
from app.retrieval.fusion import fuse_candidate_rows


class RetrievalValidation(ValueError):
    pass


class RetrievalNotFound(KeyError):
    pass


TOPIC_SIGNALS = {
    "生产分析": ("生产", "产量", "工单", "完工", "排产", "工序", "产线"),
    "质量分析": ("质量", "良率", "合格", "不良", "缺陷", "检验", "返工", "抽检"),
    "设备分析": ("设备", "停机", "故障", "报警", "运行效率", "oee"),
    "库存分析": ("库存", "仓库", "安全库存", "库存预警", "缺货"),
}

INTENT_SIGNALS = {
    "trend": ("趋势", "变化", "走势", "环比", "同比"),
    "ranking": ("排行", "排名", "最高", "最低", "最多", "最少", "top"),
    "correlation": ("相关", "关系", "影响", "关联性"),
    "anomaly": ("异常", "预警", "风险", "超标"),
    "summary": ("报告", "汇总", "概览", "总结"),
}

QUERY_FILLERS = (
    "帮我",
    "请问",
    "请",
    "分析一下",
    "分析",
    "统计一下",
    "统计",
    "查询一下",
    "查询",
    "查看一下",
    "查看",
    "各个",
    "各",
    "按照",
    "按",
    "其中",
    "一下",
    "的",
)
GENERIC_BIGRAMS = {"数据", "分析", "统计", "结果", "情况", "记录", "信息", "进行", "相关", "数量"}

CAPABILITY_SIGNALS = {
    "sales": {
        "label": "销售/出库",
        "questionTerms": ("卖出", "卖了", "销售", "销量", "出库", "发货", "发运"),
        "resourceTerms": (
            "销售", "销量", "销售订单", "出库", "发货", "发运", "sales", "shipment",
            "shipping", "delivery", "outbound",
        ),
    },
}

_PRODUCT_ENTITY_PATTERNS = (
    re.compile(
        r"(?:这个月|本月(?:至今)?|最近(?:一个月|\d+\s*天))?"
        r"(?P<value>[\u4e00-\u9fffA-Za-z0-9_.\-]{2,40}?)"
        r"(?:一共|总共|累计)?(?:卖出去|卖出|卖了|销售了|销售|出库了|出库|发货了|发货)"
    ),
    re.compile(r"(?:产品|物料)(?:名称|编码)?[为是：:\s]*(?P<value>[\u4e00-\u9fffA-Za-z0-9_.\-]{2,40})"),
)


def _json_list(value: str) -> list[str]:
    try:
        parsed = json.loads(value or "[]")
    except json.JSONDecodeError:
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


def _normalize(value: str) -> str:
    text = re.sub(r"[\s\W_]+", "", str(value or "").lower(), flags=re.UNICODE)
    for filler in QUERY_FILLERS:
        text = text.replace(filler, "")
    return text


def _term_score(question: str, term: str, weight: int) -> tuple[int, str | None]:
    query = _normalize(question)
    candidate = _normalize(term)
    if not candidate or len(candidate) < 2:
        return 0, None
    if query == candidate:
        return weight + 4, term
    if candidate in query:
        return weight + min(len(candidate), 6), term
    if len(query) >= 2 and query in candidate:
        return max(1, weight // 2), term
    chinese = "".join(re.findall(r"[\u4e00-\u9fff]", candidate))
    for size in range(min(4, len(chinese)), 1, -1):
        for index in range(len(chinese) - size + 1):
            phrase = chinese[index : index + size]
            if phrase in GENERIC_BIGRAMS:
                continue
            if phrase in query:
                return max(1, weight // 2) + size, phrase
    return 0, None


def _score_terms(question: str, weighted_terms: list[tuple[str, int]]) -> tuple[int, list[str]]:
    score = 0
    matched = []
    for term, weight in weighted_terms:
        points, label = _term_score(question, term, weight)
        if points:
            score += points
            if label and label not in matched:
                matched.append(label)
    return score, matched


def _best_term_score(question: str, weighted_terms: list[tuple[str, int]]) -> tuple[int, list[str]]:
    candidates = [_term_score(question, term, weight) for term, weight in weighted_terms]
    score, label = max(candidates, default=(0, None), key=lambda item: item[0])
    return score, [label] if label else []


def _core_match_quality(question: str, terms: list[str]) -> int:
    query = _normalize(question)
    best_common = 0
    for term in terms:
        candidate = _normalize(term)
        if not candidate:
            continue
        if candidate in query:
            return 2
        chinese = "".join(re.findall(r"[\u4e00-\u9fff]", candidate))
        for size in range(min(6, len(chinese)), 2, -1):
            if any(chinese[index : index + size] in query for index in range(len(chinese) - size + 1)):
                best_common = max(best_common, size)
                break
    return 1 if best_common >= 3 else 0


def _topic_matches(question: str) -> list[dict]:
    matches = []
    for topic, signals in TOPIC_SIGNALS.items():
        score, terms = _score_terms(question, [(term, 4) for term in signals])
        if score:
            matches.append({"name": topic, "score": score, "matchedTerms": terms})
    return sorted(matches, key=lambda item: (-item["score"], item["name"]))


def _detect_intents(question: str) -> list[str]:
    return [name for name, signals in INTENT_SIGNALS.items() if any(_normalize(term) in _normalize(question) for term in signals)]


def _requested_capability(question: str) -> dict | None:
    normalized = _normalize(question)
    for code, config in CAPABILITY_SIGNALS.items():
        matched = [term for term in config["questionTerms"] if _normalize(term) in normalized]
        if matched:
            return {"code": code, "label": config["label"], "matchedTerms": matched}
    return None


def _extract_product_entity(question: str) -> str:
    for pattern in _PRODUCT_ENTITY_PATTERNS:
        match = pattern.search(str(question or ""))
        if not match:
            continue
        value = match.group("value").strip(" 的，,。；;？? ")  # noqa: B005 - 有意按字符集去掉中英文标点与空白
        value = re.sub(r"^(?:请问|请|查询|查看|统计)", "", value).strip()
        if value and value not in {"产品", "物料", "这个产品", "该产品"}:
            return value
    return ""


def _no_match_diagnostics(session, question: str, source_id: int, tables: list[DataTable]) -> dict:
    requested = _requested_capability(question)
    fields = session.query(TableField).filter_by(source_id=source_id).all()
    resource_text = " ".join(
        [
            *(f"{row.name} {row.cn_name} {row.topic} {row.comment}" for row in tables),
            *(f"{row.table_name} {row.name} {row.cn_name} {row.comment}" for row in fields),
        ]
    ).lower()
    capability = None
    if requested:
        config = CAPABILITY_SIGNALS[requested["code"]]
        capability = {
            **requested,
            "available": any(term.lower() in resource_text for term in config["resourceTerms"]),
        }

    product_value = _extract_product_entity(question)
    entity_lookup = None
    if product_value:
        product_table = next(
            (
                table for table in tables
                if table.registered and (
                    table.name.lower() in {"dim_product", "product", "products"}
                    or "产品主数据" in f"{table.cn_name}{table.comment}"
                )
            ),
            None,
        )
        if product_table:
            product_fields = [field for field in fields if field.table_name == product_table.name]
            name_field = next(
                (
                    field.name for field in product_fields
                    if field.name.lower() in {"product_name", "name"}
                    or "产品名称" in f"{field.cn_name}{field.comment}"
                ),
                "",
            )
            code_field = next(
                (
                    field.name for field in product_fields
                    if field.name.lower() in {"product_code", "code"}
                    or "产品编码" in f"{field.cn_name}{field.comment}"
                ),
                next(
                    (field.name for field in product_fields if field.name.lower() == "product_id"),
                    "",
                ),
            )
            id_field = product_table.primary_key or code_field
            if name_field or code_field:
                entity_lookup = {
                    "kind": "product",
                    "label": "产品",
                    "value": product_value,
                    "table": product_table.name,
                    "idField": id_field,
                    "nameField": name_field,
                    "codeField": code_field,
                }

    return {
        "requestedCapability": capability,
        "entityLookup": entity_lookup,
        "availableTopics": sorted({table.topic for table in tables if table.topic}),
    }


def _resolve_source(session, source_id: int | None) -> DataSource:
    if source_id is None:
        source = session.query(DataSource).filter_by(is_default=True).first()
    else:
        source = session.query(DataSource).filter_by(id=int(source_id)).first()
    if source is None:
        label = source_id if source_id is not None else "默认"
        raise RetrievalNotFound(f"数据源 {label} 不存在")
    return source


def _metric_candidates(
    session,
    question: str,
    source_id: int,
    topic_matches: list[dict],
    semantic_hits: list[dict] | None = None,
) -> list[dict]:
    topic_scores = {item["name"]: item["score"] for item in topic_matches}
    semantic_hits = semantic_hits or []
    semantic_ids = {str(item["objectId"]) for item in semantic_hits}
    rows = (
        session.query(KnowledgeMetric, MetricSourceBinding)
        .join(MetricSourceBinding, MetricSourceBinding.metric_id == KnowledgeMetric.id)
        .filter(
            KnowledgeMetric.status == "已发布",
            MetricSourceBinding.source_id == source_id,
            MetricSourceBinding.status == "已验证",
        )
        .all()
    )
    result = []
    for metric, binding in rows:
        synonyms = _json_list(metric.synonyms_json)
        dimensions = _json_list(metric.dimensions_json)
        score = 0
        matched = []
        for group in (
            [(metric.name, 14)],
            [(item, 12) for item in synonyms],
            [(item, 5) for item in dimensions],
            [(metric.definition, 3), (metric.formula, 2)],
        ):
            group_score, group_matched = _best_term_score(question, group)
            score += group_score
            matched.extend(group_matched)
        if metric.topic in topic_scores:
            score += min(topic_scores[metric.topic], 8)
            matched.append(metric.topic)
        if score <= 0 and metric.id not in semantic_ids:
            continue
        table_rows = session.query(MetricTableBinding).filter_by(binding_id=binding.id).order_by(
            MetricTableBinding.id
        ).all()
        field_rows = session.query(MetricFieldBinding).filter_by(binding_id=binding.id).order_by(
            MetricFieldBinding.id
        ).all()
        result.append(
            {
                "metric": metric,
                "candidateId": metric.id,
                "binding": binding,
                "score": score,
                "lexicalScore": score,
                "matched": list(dict.fromkeys(matched)),
                "tables": [row.table_name for row in table_rows],
                "fieldRows": field_rows,
                "dimensions": dimensions,
                "synonyms": synonyms,
                "coreQuality": _core_match_quality(question, [metric.name, *synonyms]),
            }
        )
    lexical_rows = sorted(
        (item for item in result if item["lexicalScore"] > 0),
        key=lambda item: (-item["lexicalScore"], item["metric"].id),
    )
    if not semantic_hits:
        return lexical_rows
    fusion = fuse_candidate_rows(
        lexical_rows,
        semantic_hits,
        id_key="candidateId",
        rank_constant=settings.retrieval_rrf_k,
    )
    for item in result:
        ranking = fusion[item["candidateId"]]
        item["score"] = ranking["fusedScore"]
        item["semanticScore"] = ranking["semanticScore"]
        item["retrievalChannels"] = [
            channel
            for channel, rank in (
                ("lexical", ranking["lexicalRank"]),
                ("semantic", ranking["semanticRank"]),
            )
            if rank is not None
        ]
    return sorted(result, key=lambda item: (-item["score"], item["metric"].id))


def _direct_table_candidates(
    session,
    question: str,
    source_id: int,
    semantic_hits: list[dict] | None = None,
) -> list[dict]:
    semantic_hits = semantic_hits or []
    semantic_ids = {str(item["objectId"]) for item in semantic_hits}
    tables = session.query(DataTable).filter_by(source_id=source_id, registered=True).all()
    fields = session.query(TableField).filter_by(source_id=source_id).order_by(TableField.ordinal).all()
    by_table: dict[str, list[TableField]] = {}
    for field in fields:
        by_table.setdefault(field.table_name, []).append(field)
    candidates = []
    for table in tables:
        table_score, table_matched = _score_terms(question, [
            (table.cn_name, 9),
            (table.comment, 7),
            (table.name, 7),
            (table.topic, 3),
        ])
        field_matches = []
        for field in by_table.get(table.name, []):
            field_score, matched = _best_term_score(
                question, [(field.cn_name, 6), (field.comment, 5), (field.name, 4)]
            )
            if field_score:
                field_matches.append((field_score, matched))
        field_matches.sort(key=lambda item: -item[0])
        score = table_score + sum(item[0] for item in field_matches[:2])
        matched = [*table_matched, *(term for _points, labels in field_matches[:2] for term in labels)]
        if score or table.name in semantic_ids:
            candidates.append(
                {
                    "table": table,
                    "candidateId": table.name,
                    "score": score,
                    "lexicalScore": score,
                    "tableScore": table_score,
                    "matched": matched,
                }
            )
    lexical_rows = sorted(
        (item for item in candidates if item["lexicalScore"] > 0),
        key=lambda item: (-item["lexicalScore"], item["table"].name),
    )
    if not semantic_hits:
        return lexical_rows
    fusion = fuse_candidate_rows(
        lexical_rows,
        semantic_hits,
        id_key="candidateId",
        rank_constant=settings.retrieval_rrf_k,
    )
    for item in candidates:
        ranking = fusion[item["candidateId"]]
        item["score"] = ranking["fusedScore"]
        item["semanticScore"] = ranking["semanticScore"]
    return sorted(candidates, key=lambda item: (-item["score"], item["table"].name))


def _relation_graph(relations: list[TableRelation], registered: set[str]) -> dict[str, list[tuple[str, TableRelation]]]:
    graph: dict[str, list[tuple[str, TableRelation]]] = {}
    for relation in relations:
        if relation.from_table not in registered or relation.to_table not in registered:
            continue
        graph.setdefault(relation.from_table, []).append((relation.to_table, relation))
        graph.setdefault(relation.to_table, []).append((relation.from_table, relation))
    return graph


def _shortest_path(graph: dict[str, list[tuple[str, TableRelation]]], start: str, goal: str) -> list[TableRelation]:
    if start == goal:
        return []
    queue = deque([(start, [])])
    visited = {start}
    while queue:
        node, path = queue.popleft()
        for neighbor, relation in graph.get(node, []):
            if neighbor in visited:
                continue
            next_path = [*path, relation]
            if neighbor == goal:
                return next_path
            visited.add(neighbor)
            queue.append((neighbor, next_path))
    return []


def _select_tables(
    metric_items: list[dict], direct_items: list[dict], relations: list[TableRelation], registered: set[str], max_tables: int
) -> tuple[list[str], list[TableRelation], dict[str, list[str]]]:
    selected: list[str] = []
    reasons: dict[str, list[str]] = {}

    def add(table_name: str, reason: str) -> None:
        if table_name not in registered:
            return
        if table_name not in selected and len(selected) < max_tables:
            selected.append(table_name)
        if table_name in selected and reason not in reasons.setdefault(table_name, []):
            reasons[table_name].append(reason)

    for item in metric_items:
        for table_name in item["tables"]:
            add(table_name, f"指标：{item['metric'].name}")
    anchors = list(selected)
    direct_targets = []
    for item in direct_items:
        table = item["table"]
        table_name = table.name
        if metric_items and table_name not in selected:
            is_dimension_resource = (
                table.topic == "主数据"
                or table_name.lower().startswith("dim_")
                or "主数据" in f"{table.cn_name}{table.comment}"
            )
            if not is_dimension_resource or item["tableScore"] <= 0:
                continue
        direct_targets.append(table_name)
        add(table_name, "问题直接命中：" + "、".join(item["matched"][:3]))
        if len(direct_targets) >= 3:
            break

    graph = _relation_graph(relations, registered)
    path_relations: list[TableRelation] = []
    for target in direct_targets:
        if target not in selected or not anchors:
            continue
        best_path: list[TableRelation] = []
        for anchor in anchors:
            path = _shortest_path(graph, anchor, target)
            if path and (not best_path or len(path) < len(best_path)):
                best_path = path
        for relation in best_path:
            for name in (relation.from_table, relation.to_table):
                add(name, "已登记关联路径")
            if relation not in path_relations:
                path_relations.append(relation)

    selected_set = set(selected)
    included_relations = [
        relation
        for relation in relations
        if relation.from_table in selected_set and relation.to_table in selected_set
    ]
    for relation in path_relations:
        if relation not in included_relations:
            included_relations.append(relation)
    return selected, included_relations, reasons


def _field_payload(
    session,
    question: str,
    source_id: int,
    table: DataTable,
    metric_items: list[dict],
    relations: list[TableRelation],
    max_fields: int,
) -> list[dict]:
    fields = (
        session.query(TableField)
        .filter_by(source_id=source_id, table_name=table.name)
        .order_by(TableField.ordinal)
        .all()
    )
    required: dict[str, list[str]] = {}

    def require(field_name: str, reason: str) -> None:
        if field_name:
            required.setdefault(field_name, []).append(reason)

    require(table.primary_key, "主键")
    for item in metric_items:
        binding = item["binding"]
        if binding.time_field and table.name in item["tables"]:
            require(binding.time_field, "指标时间字段")
        for mapping in item["fieldRows"]:
            if mapping.table_name == table.name:
                require(mapping.field_name, f"指标字段：{mapping.semantic_field}")
    for relation in relations:
        if relation.from_table == table.name:
            require(relation.from_field, "关联字段")
        if relation.to_table == table.name:
            require(relation.to_field, "关联字段")

    scored = []
    for field in fields:
        score, matched = _score_terms(
            question,
            [(field.cn_name, 7), (field.comment, 6), (field.name, 5), (field.sample_value, 1)],
        )
        if field.name in required:
            score += 100
        scored.append((field, score, matched))
    scored.sort(key=lambda item: (-item[1], item[0].ordinal))
    selected = [item for item in scored if item[1] > 0][:max_fields]
    return [
        {
            "name": field.name,
            "semanticName": field.cn_name or field.comment or field.name,
            "dataType": field.data_type,
            "isPrimary": field.is_primary,
            "sampleValue": field.sample_value,
            "reasons": list(dict.fromkeys([*required.get(field.name, []), *matched])),
        }
        for field, _score, matched in selected
    ]


def _serialize_metric(item: dict) -> dict:
    metric: KnowledgeMetric = item["metric"]
    binding: MetricSourceBinding = item["binding"]
    return {
        "id": metric.id,
        "name": metric.name,
        "topic": metric.topic,
        "definition": metric.definition,
        "semanticFormula": metric.formula,
        "physicalFormula": binding.physical_formula,
        "filterExpression": binding.filter_expression,
        "semanticTimeField": metric.time_field,
        "physicalTimeField": binding.time_field,
        "dimensions": item["dimensions"],
        "precision": metric.precision,
        "version": metric.version,
        "bindingVersion": binding.version,
        "sqlDialect": binding.sql_dialect,
        "sourceTables": item["tables"],
        "fieldMappings": [
            {
                "semanticField": row.semantic_field,
                "tableName": row.table_name,
                "fieldName": row.field_name,
                "transformExpression": row.transform_expression,
            }
            for row in item["fieldRows"]
        ],
        "relevanceScore": item["score"],
        "matchedTerms": item["matched"],
        "retrievalChannels": item.get("retrievalChannels", ["lexical"]),
        "lexicalScore": item.get("lexicalScore", item["score"]),
        "semanticScore": item.get("semanticScore"),
    }


def build_context_package(
    question: str,
    source_id: int | None = None,
    *,
    max_metrics: int = 5,
    max_tables: int = 8,
    max_fields_per_table: int = 12,
) -> dict:
    question = str(question or "").strip()
    if not question:
        raise RetrievalValidation("问题不能为空")
    if len(question) > 1000:
        raise RetrievalValidation("问题长度不能超过 1000 个字符")
    max_metrics = max(1, min(int(max_metrics), 10))
    max_tables = max(1, min(int(max_tables), 15))
    max_fields_per_table = max(1, min(int(max_fields_per_table), 30))

    with PlatformSession() as session:
        source = _resolve_source(session, source_id)
        topic_matches = _topic_matches(question)
        intents = _detect_intents(question)
        # SQL 内按对象类型独立 TopK，避免字段很多的数据表挤掉指标或业务规则候选。
        semantic_result = semantic.search(
            session,
            question,
            source.id,
            object_types=("metric", "table", "business_object", "business_rule"),
        )
        semantic_hits = semantic_result.hits
        semantic_metric_hits = [item for item in semantic_hits if item["objectType"] == "metric"]
        semantic_table_hits = [item for item in semantic_hits if item["objectType"] == "table"]
        semantic_object_hits = [item for item in semantic_hits if item["objectType"] == "business_object"]
        semantic_rule_hits = [item for item in semantic_hits if item["objectType"] == "business_rule"]
        metric_items = _metric_candidates(
            session,
            question,
            source.id,
            topic_matches,
            semantic_metric_hits,
        )
        if metric_items and "summary" not in intents:
            best_quality = max(item["coreQuality"] for item in metric_items)
            if best_quality:
                minimum_quality = 1 if best_quality == 2 else best_quality
                metric_items = [
                    item
                    for item in metric_items
                    if item["coreQuality"] >= minimum_quality
                    or "semantic" in item.get("retrievalChannels", [])
                ]
        metric_items = metric_items[:max_metrics]
        direct_items = _direct_table_candidates(session, question, source.id, semantic_table_hits)
        table_rows = session.query(DataTable).filter_by(source_id=source.id, registered=True).all()
        table_by_name = {row.name: row for row in table_rows}
        registered = set(table_by_name)
        relation_rows = session.query(TableRelation).filter_by(source_id=source.id).all()
        table_names, selected_relations, reasons = _select_tables(
            metric_items, direct_items, relation_rows, registered, max_tables
        )

        tables = []
        for table_name in table_names:
            table = table_by_name[table_name]
            fields = _field_payload(
                session,
                question,
                source.id,
                table,
                metric_items,
                selected_relations,
                max_fields_per_table,
            )
            tables.append(
                {
                    "name": table.name,
                    "semanticName": table.cn_name or table.comment or table.name,
                    "topic": table.topic,
                    "comment": table.comment,
                    "primaryKey": table.primary_key,
                    "reasons": reasons.get(table.name, []),
                    "fields": fields,
                }
            )

        selected_topics = {item["metric"].topic for item in metric_items}
        selected_topics.update(item["name"] for item in topic_matches[:2])
        dimensions = {dimension for item in metric_items for dimension in item["dimensions"]}
        object_rows = session.query(BusinessObject).filter_by(enabled=True).all()
        objects = []
        semantic_object_ids = {str(item["objectId"]) for item in semantic_object_hits}
        for row in object_rows:
            score, matched = _score_terms(question, [(row.name, 8), (row.description, 3)])
            if row.name in dimensions:
                score += 8
                matched.append("指标维度")
            if score or row.topic in selected_topics or row.id in semantic_object_ids:
                objects.append(
                    {
                        "id": row.id,
                        "name": row.name,
                        "topic": row.topic,
                        "description": row.description,
                        "matchedTerms": list(dict.fromkeys(matched)),
                        "relevanceScore": score,
                        "lexicalScore": score,
                    }
                )
        object_fusion = fuse_candidate_rows(
            sorted(objects, key=lambda item: (-item["lexicalScore"], item["id"])),
            semantic_object_hits,
            id_key="id",
            rank_constant=settings.retrieval_rrf_k,
        ) if semantic_object_hits else {}
        for item in objects:
            if item["id"] in object_fusion:
                item["relevanceScore"] = object_fusion[item["id"]]["fusedScore"]
                item["semanticScore"] = object_fusion[item["id"]]["semanticScore"]
        objects.sort(key=lambda item: (-item["relevanceScore"], item["id"]))

        rule_rows = session.query(BusinessRule).filter_by(enabled=True).all()
        rules = []
        semantic_rule_ids = {str(item["objectId"]) for item in semantic_rule_hits}
        for row in rule_rows:
            score, matched = _score_terms(question, [(row.name, 7), (row.content, 4)])
            applicable = (
                row.scope == "全局"
                or row.scope in selected_topics
                or score > 0
                or row.id in semantic_rule_ids
            )
            if applicable:
                lexical_score = score + (
                    4 if row.scope in selected_topics else 1 if row.scope == "全局" else 0
                )
                rules.append(
                    {
                        "id": row.id,
                        "name": row.name,
                        "type": row.rule_type,
                        "scope": row.scope,
                        "content": row.content,
                        "relevanceScore": lexical_score,
                        "lexicalScore": lexical_score,
                        "matchedTerms": matched,
                    }
                )
        rule_fusion = fuse_candidate_rows(
            sorted(rules, key=lambda item: (-item["lexicalScore"], item["id"])),
            semantic_rule_hits,
            id_key="id",
            rank_constant=settings.retrieval_rrf_k,
        ) if semantic_rule_hits else {}
        for item in rules:
            if item["id"] in rule_fusion:
                item["relevanceScore"] = rule_fusion[item["id"]]["fusedScore"]
                item["semanticScore"] = rule_fusion[item["id"]]["semanticScore"]
        rules.sort(key=lambda item: (-item["relevanceScore"], item["id"]))

        relation_payload = [
            {
                "fromTable": row.from_table,
                "fromField": row.from_field,
                "toTable": row.to_table,
                "toField": row.to_field,
                "source": row.source,
            }
            for row in selected_relations
        ]
        metric_payload = [_serialize_metric(item) for item in metric_items]
        allowed_columns = {table["name"]: [field["name"] for field in table["fields"]] for table in tables}
        status = "ready" if metric_payload else "no_metric_match" if tables else "no_match"
        diagnostics = (
            _no_match_diagnostics(session, question, source.id, table_rows)
            if status == "no_match"
            else {}
        )
        warnings = []
        if not metric_payload:
            warnings.append("未命中当前数据源上已发布且已验证的指标口径")
        if tables and not all(table["fields"] for table in tables):
            warnings.append("部分候选表没有可用于本次问题的字段")
        if len(metric_payload) >= 2 and metric_payload[0]["relevanceScore"] == metric_payload[1]["relevanceScore"]:
            warnings.append("存在多个相近指标，后续 Agent 应先澄清口径")
            status = "needs_clarification"

        fingerprint_input = json.dumps(
            {
                "question": question,
                "sourceId": source.id,
                "metrics": [(item["id"], item["version"], item["bindingVersion"]) for item in metric_payload],
                "tables": allowed_columns,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        fingerprint = hashlib.sha256(fingerprint_input.encode("utf-8")).hexdigest()[:20]
        package = {
            "contextPackageId": f"ctx-{fingerprint}",
            "generatedAt": datetime.now().isoformat(timespec="seconds"),
            "question": question,
            "source": {
                "id": source.id,
                "name": source.name,
                "type": source.type,
                "database": source.db_name,
            },
            "retrieval": {
                "strategy": "hybrid-rrf" if semantic_hits else "lexical-rule",
                "version": "2.0",
                "intents": intents,
                "topicMatches": topic_matches,
                "channels": {
                    "lexical": True,
                    "semantic": bool(semantic_hits),
                    "semanticModel": settings.embedding_model if semantic_hits else None,
                    "semanticHitCount": len(semantic_hits),
                    "semanticStatus": semantic_result.status,
                    "fallbackApplied": semantic_result.status in {"unavailable", "circuit_open"},
                    "fallbackReason": semantic_result.reason or None,
                },
                "limits": {
                    "metrics": max_metrics,
                    "tables": max_tables,
                    "fieldsPerTable": max_fields_per_table,
                },
            },
            "metrics": metric_payload,
            "businessObjects": objects[:10],
            "businessRules": rules[:8],
            "tables": tables,
            "relations": relation_payload,
            "executionPolicy": {
                "readOnly": True,
                "allowUnregisteredTables": False,
                "allowUnregisteredRelations": False,
                "allowedTables": table_names,
                "allowedColumns": allowed_columns,
                "joinPaths": relation_payload,
                "instruction": "SQL 只能使用本上下文包列出的表、字段、指标物理公式和关联路径。",
            },
            "coverage": {
                "status": status,
                "metricCount": len(metric_payload),
                "tableCount": len(tables),
                "fieldCount": sum(len(table["fields"]) for table in tables),
                "relationCount": len(relation_payload),
                "warnings": warnings,
            },
            "diagnostics": diagnostics,
        }

    write_audit(
        action=f"语义检索：{question[:80]}",
        resource=f"数据源 {package['source']['id']} / {package['contextPackageId']}",
        result="允许",
        detail=(
            f"召回 {package['coverage']['metricCount']} 个指标、{package['coverage']['tableCount']} 张表、"
            f"{package['coverage']['fieldCount']} 个字段、{package['coverage']['relationCount']} 条关系"
        ),
    )
    return package
