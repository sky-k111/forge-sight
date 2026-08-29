"""Persistent semantic index and pgvector retrieval for business metadata.

The vector channel only expands candidates. Callers must still resolve every
hit through the trusted ORM rows and source bindings before it can influence
the SQL execution allowlist.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import threading
import time
from dataclasses import dataclass
from functools import lru_cache

import httpx
from openai import OpenAI
from sqlalchemy import bindparam, or_, text

from app.config import settings
from app.db.models import (
    BusinessObject,
    BusinessRule,
    AnalysisSkill,
    DataSource,
    DataTable,
    KnowledgeMetric,
    MetricSourceBinding,
    TableField,
)


log = logging.getLogger(__name__)
_breaker_lock = threading.Lock()
_breaker_until = 0.0


@dataclass(frozen=True)
class SemanticDocument:
    source_id: int
    object_type: str
    object_id: str
    topic: str
    content: str
    metadata: dict

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SemanticSearchResult:
    hits: list[dict]
    status: str
    reason: str = ""


def _embedding_client() -> OpenAI:
    base_url = settings.embedding_base_url.strip()
    if not base_url:
        raise RuntimeError("EMBEDDING_BASE_URL 未配置")
    http_client = httpx.Client(
        proxy=settings.embedding_proxy_url or None,
        trust_env=False,
        timeout=settings.embedding_timeout_seconds,
    )
    return OpenAI(
        api_key=settings.embedding_api_key or "local-embedding-service",
        base_url=base_url.rstrip("/"),
        timeout=settings.embedding_timeout_seconds,
        max_retries=settings.embedding_max_retries,
        http_client=http_client,
    )


def _validate_vector(vector: list[float]) -> list[float]:
    dimensions = int(settings.embedding_dimensions)
    if len(vector) != dimensions:
        raise RuntimeError(f"Embedding 维度不一致：期望 {dimensions}，实际 {len(vector)}")
    if not all(math.isfinite(float(value)) for value in vector):
        raise RuntimeError("Embedding 包含非有限数值")
    return [float(value) for value in vector]


def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    request: dict = {
        "model": settings.embedding_model,
        "input": texts,
    }
    if settings.embedding_send_dimensions:
        request["dimensions"] = int(settings.embedding_dimensions)
    response = _embedding_client().embeddings.create(
        **request,
    )
    ordered = sorted(response.data, key=lambda item: item.index)
    if len(ordered) != len(texts):
        raise RuntimeError("Embedding 服务返回条数与输入不一致")
    return [_validate_vector(list(item.embedding)) for item in ordered]


@lru_cache(maxsize=512)
def _embed_query(model: str, dimensions: int, question: str) -> tuple[float, ...]:
    """Bounded per-process query cache; model and dimension are part of the key."""

    del model, dimensions  # values intentionally participate in the cache key
    return tuple(embed_texts([question])[0])


def _vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(format(value, ".9g") for value in vector) + "]"


def collect_documents(session, source_id: int) -> list[SemanticDocument]:
    """Build object-type-separated semantic documents for one source."""

    documents: list[SemanticDocument] = []
    tenant_id = session.query(DataSource.tenant_id).filter_by(id=source_id).scalar()
    if not tenant_id:
        return documents
    metric_rows = (
        session.query(KnowledgeMetric, MetricSourceBinding)
        .join(
            MetricSourceBinding,
            (MetricSourceBinding.metric_id == KnowledgeMetric.id)
            & (KnowledgeMetric.tenant_id == tenant_id),
        )
        .filter(
            KnowledgeMetric.tenant_id == tenant_id,
            KnowledgeMetric.status == "已发布",
            MetricSourceBinding.source_id == source_id,
            MetricSourceBinding.status == "已验证",
        )
        .all()
    )
    for metric, binding in metric_rows:
        content = "\n".join(
            part for part in (
                f"指标名称：{metric.name}",
                f"分析主题：{metric.topic}",
                f"同义词：{metric.synonyms_json}",
                f"定义：{metric.definition}",
                f"业务公式：{metric.formula}",
                f"可用维度：{metric.dimensions_json}",
            ) if part.split("：", 1)[-1].strip(" []\"")
        )
        documents.append(
            SemanticDocument(
                source_id=source_id,
                object_type="metric",
                object_id=metric.id,
                topic=metric.topic,
                content=content,
                metadata={"bindingId": binding.id, "metricVersion": metric.version},
            )
        )

    tables = session.query(DataTable).filter_by(source_id=source_id, registered=True).all()
    fields = session.query(TableField).filter_by(source_id=source_id).order_by(TableField.ordinal).all()
    by_table: dict[tuple[str, str], list[TableField]] = {}
    for field in fields:
        by_table.setdefault((field.schema_name, field.table_name), []).append(field)
    for table in tables:
        qualified_name = f"{table.schema_name}.{table.name}" if table.schema_name else table.name
        field_text = "；".join(
            f"{field.name}（{field.cn_name or field.comment or field.name}）"
            for field in by_table.get((table.schema_name, table.name), [])
        )
        documents.append(
            SemanticDocument(
                source_id=source_id,
                object_type="table",
                object_id=qualified_name,
                topic=table.topic,
                content="\n".join(
                    (
                        f"数据表：{qualified_name}",
                        f"Schema：{table.schema_name}",
                        f"业务名称：{table.cn_name or table.comment or table.name}",
                        f"分析主题：{table.topic}",
                        f"字段：{field_text}",
                    )
                ),
                metadata={"registered": True, "schemaName": table.schema_name, "tableName": table.name},
            )
        )

    for row in session.query(BusinessObject).filter_by(tenant_id=tenant_id, enabled=True).all():
        documents.append(
            SemanticDocument(
                source_id=source_id,
                object_type="business_object",
                object_id=row.id,
                topic=row.topic,
                content=f"业务对象：{row.name}\n分析主题：{row.topic}\n说明：{row.description}",
                metadata={},
            )
        )
    for row in session.query(BusinessRule).filter_by(tenant_id=tenant_id, enabled=True).all():
        documents.append(
            SemanticDocument(
                source_id=source_id,
                object_type="business_rule",
                object_id=row.id,
                topic=row.scope,
                content=f"业务规则：{row.name}\n类型：{row.rule_type}\n范围：{row.scope}\n内容：{row.content}",
                metadata={},
            )
        )
    skill_rows = (
        session.query(AnalysisSkill)
        .filter(
            AnalysisSkill.status == "published",
            or_(AnalysisSkill.source_id.is_(None), AnalysisSkill.source_id == source_id),
        )
        .all()
    )
    for row in skill_rows:
        documents.append(
            SemanticDocument(
                source_id=source_id,
                object_type="analysis_skill",
                object_id=row.id,
                topic=row.topic,
                content="\n".join(
                    (
                        f"分析技能：{row.name}",
                        f"主题：{row.topic}",
                        f"适用表达：{(row.trigger_json or {}).get('terms', [])}",
                        f"说明：{row.description}",
                        f"前置条件：{row.preconditions_json or []}",
                        f"分析步骤：{row.procedure_json or []}",
                    )
                ),
                metadata={"version": row.version, "scopeSourceId": row.source_id},
            )
        )
    return documents


def ensure_schema(engine) -> None:
    """Create the extension/table with a model-specific vector dimension."""

    dimensions = int(settings.embedding_dimensions)
    if dimensions < 1 or dimensions > 2000:
        raise RuntimeError("EMBEDDING_DIMENSIONS 超出 HNSW vector 索引支持范围")
    statements = [
        "CREATE EXTENSION IF NOT EXISTS vector",
        f"""
        CREATE TABLE IF NOT EXISTS semantic_search_document (
            id BIGSERIAL PRIMARY KEY,
            source_id INTEGER NOT NULL,
            object_type VARCHAR(32) NOT NULL,
            object_id VARCHAR(128) NOT NULL,
            topic VARCHAR(80) NOT NULL DEFAULT '',
            content TEXT NOT NULL,
            content_hash VARCHAR(64) NOT NULL,
            embedding_model VARCHAR(160) NOT NULL,
            embedding vector({dimensions}) NOT NULL,
            metadata_json JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            enabled BOOLEAN NOT NULL DEFAULT TRUE,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_semantic_search_object UNIQUE
                (source_id, object_type, object_id, embedding_model)
        )
        """,
        "CREATE INDEX IF NOT EXISTS ix_semantic_search_scope ON semantic_search_document (source_id, object_type, enabled)",
        f"CREATE INDEX IF NOT EXISTS ix_semantic_search_embedding_hnsw ON semantic_search_document USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)",
    ]
    with engine.begin() as connection:
        for statement in statements:
            connection.exec_driver_sql(statement)


def rebuild_source_index(
    session,
    source_id: int,
    *,
    batch_size: int = 10,
    force: bool = False,
) -> dict:
    """Synchronize one source, embedding only changed objects unless forced."""

    ensure_schema(session.get_bind())
    documents = collect_documents(session, source_id)
    active_keys = {(doc.object_type, doc.object_id) for doc in documents}
    existing_rows = session.execute(
        text(
            """
            SELECT object_type, object_id, content_hash
            FROM semantic_search_document
            WHERE source_id = :source_id AND embedding_model = :model
            """
        ),
        {"source_id": source_id, "model": settings.embedding_model},
    ).mappings().all()
    existing = {(row["object_type"], row["object_id"]): row["content_hash"] for row in existing_rows}
    changed = [
        doc
        for doc in documents
        if force or existing.get((doc.object_type, doc.object_id)) != doc.content_hash
    ]

    for start in range(0, len(changed), max(1, batch_size)):
        batch = changed[start : start + max(1, batch_size)]
        vectors = embed_texts([doc.content for doc in batch])
        for doc, vector in zip(batch, vectors, strict=True):
            session.execute(
                text(
                    """
                    INSERT INTO semantic_search_document (
                        source_id, object_type, object_id, topic, content, content_hash,
                        embedding_model, embedding, metadata_json, enabled, updated_at
                    ) VALUES (
                        :source_id, :object_type, :object_id, :topic, :content, :content_hash,
                        :model, CAST(:embedding AS vector), CAST(:metadata AS jsonb), TRUE, CURRENT_TIMESTAMP
                    )
                    ON CONFLICT (source_id, object_type, object_id, embedding_model)
                    DO UPDATE SET topic = EXCLUDED.topic, content = EXCLUDED.content,
                        content_hash = EXCLUDED.content_hash, embedding = EXCLUDED.embedding,
                        metadata_json = EXCLUDED.metadata_json, enabled = TRUE,
                        updated_at = CURRENT_TIMESTAMP
                    """
                ),
                {
                    "source_id": doc.source_id,
                    "object_type": doc.object_type,
                    "object_id": doc.object_id,
                    "topic": doc.topic,
                    "content": doc.content,
                    "content_hash": doc.content_hash,
                    "model": settings.embedding_model,
                    "embedding": _vector_literal(vector),
                    "metadata": json.dumps(doc.metadata, ensure_ascii=False),
                },
            )

    stale = [key for key in existing if key not in active_keys]
    for object_type, object_id in stale:
        session.execute(
            text(
                """
                UPDATE semantic_search_document SET enabled = FALSE, updated_at = CURRENT_TIMESTAMP
                WHERE source_id = :source_id AND object_type = :object_type
                  AND object_id = :object_id AND embedding_model = :model
                """
            ),
            {
                "source_id": source_id,
                "object_type": object_type,
                "object_id": object_id,
                "model": settings.embedding_model,
            },
        )
    session.commit()
    return {
        "sourceId": source_id,
        "mode": "full" if force else "incremental",
        "documents": len(documents),
        "updated": len(changed),
        "disabled": len(stale),
    }


def search(
    session,
    question: str,
    source_id: int,
    *,
    object_types: tuple[str, ...],
    top_k: int | None = None,
) -> SemanticSearchResult:
    """Search one source with mandatory metadata pushdown and safe fallback."""

    global _breaker_until

    if not settings.retrieval_semantic_enabled:
        return SemanticSearchResult([], "disabled")
    if session.get_bind().dialect.name != "postgresql":
        return SemanticSearchResult([], "unsupported_dialect", session.get_bind().dialect.name)
    with _breaker_lock:
        if time.monotonic() < _breaker_until:
            return SemanticSearchResult([], "circuit_open", "Embedding 服务暂时熔断")
    try:
        vector = list(_embed_query(settings.embedding_model, settings.embedding_dimensions, question))
        limit = max(1, min(int(top_k or settings.retrieval_semantic_top_k), 100))
        rows = session.execute(
            text(
                """
                WITH ranked AS MATERIALIZED (
                    SELECT object_type, object_id, topic,
                           1 - (embedding <=> CAST(:embedding AS vector)) AS similarity,
                           ROW_NUMBER() OVER (
                               PARTITION BY object_type
                               ORDER BY embedding <=> CAST(:embedding AS vector)
                           ) AS type_rank
                    FROM semantic_search_document
                    WHERE source_id = :source_id
                      AND enabled = TRUE
                      AND embedding_model = :model
                      AND object_type IN :object_types
                )
                SELECT object_type, object_id, topic, similarity
                FROM ranked
                WHERE type_rank <= :limit
                ORDER BY object_type, type_rank
                """
            ).bindparams(bindparam("object_types", expanding=True)),
            {
                "embedding": _vector_literal(vector),
                "source_id": source_id,
                "model": settings.embedding_model,
                "object_types": list(object_types),
                "limit": limit,
            },
        ).mappings().all()
        threshold = float(settings.retrieval_semantic_score_threshold)
        hits = [
            {
                "objectType": row["object_type"],
                "objectId": row["object_id"],
                "topic": row["topic"],
                "similarity": float(row["similarity"]),
            }
            for row in rows
            if float(row["similarity"]) >= threshold
        ]
        with _breaker_lock:
            _breaker_until = 0.0
        return SemanticSearchResult(hits, "ready")
    except Exception as exc:  # noqa: BLE001 - semantic recall must degrade to trusted lexical retrieval
        log.warning("Semantic retrieval degraded to lexical rules: %s", exc)
        with _breaker_lock:
            _breaker_until = time.monotonic() + max(
                1, int(settings.retrieval_semantic_circuit_breaker_seconds)
            )
        return SemanticSearchResult([], "unavailable", type(exc).__name__)
