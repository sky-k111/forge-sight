"""Scoped, auditable long-term memory for ForgeSight.

This module deliberately keeps memory separate from enterprise semantic knowledge:
knowledge defines what a metric means; memory records user preferences and prior
investigations.  Every read is scope-filtered before ranking.
"""

from __future__ import annotations

import hashlib
import math
import re
from datetime import datetime, timedelta

from sqlalchemy import and_, func, or_, text

from app.agent.ids import new_id
from app.db.models import (
    AgentMemory,
    AgentMemoryAccessLog,
    AgentMemoryEvidence,
    AgentMemoryFeedback,
    AgentMemoryJob,
    AgentMemoryRelation,
    AnalysisMessage,
    AnalysisRun,
    AnalysisSession,
    AnalysisSkill,
    ExternalChatJob,
    PlatformFeatureFlag,
)
from app.db.platform import PlatformSession
from app.config import settings


FEATURE_KEY = "agent_memory"
MEMORY_KINDS = {"semantic", "episodic", "preference", "procedural"}
ACTIVE_STATUSES = {"active", "candidate"}


class MemoryNotFound(KeyError):
    pass


class MemoryValidation(ValueError):
    pass


def _setting(session) -> PlatformFeatureFlag:
    row = session.query(PlatformFeatureFlag).filter_by(key=FEATURE_KEY).first()
    if row is None:
        row = PlatformFeatureFlag(
            key=FEATURE_KEY,
            enabled=False,
            config_json={
                "variant": "off",
                "topK": 6,
                "minimumScore": 0.16,
                "captureEpisodes": True,
                "captureExplicitPreferences": True,
                "retentionDays": 180,
            },
        )
        session.add(row)
        session.flush()
    return row


def _setting_payload(row: PlatformFeatureFlag) -> dict:
    config = dict(row.config_json or {})
    variant = "on" if row.enabled else "off"
    config["variant"] = variant
    return {
        "key": FEATURE_KEY,
        "enabled": bool(row.enabled),
        "variant": variant,
        "config": config,
        "updatedBy": row.updated_by,
        "updatedAt": row.updated_at.isoformat() if row.updated_at else None,
    }


def get_settings() -> dict:
    with PlatformSession() as session:
        row = _setting(session)
        session.commit()
        return _setting_payload(row)


def update_settings(enabled: bool, *, updated_by: str, config: dict | None = None) -> dict:
    with PlatformSession() as session:
        row = _setting(session)
        row.enabled = bool(enabled)
        if config is not None:
            merged = dict(row.config_json or {})
            for key in ("topK", "minimumScore", "captureEpisodes", "captureExplicitPreferences", "retentionDays"):
                if key in config:
                    merged[key] = config[key]
            row.config_json = merged
        row.updated_by = updated_by or "admin"
        row.updated_at = datetime.now()
        session.commit()
        return _setting_payload(row)


def snapshot_settings(session=None) -> dict:
    if session is not None:
        return _setting_payload(_setting(session))
    return get_settings()


def _normalize(text: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", (text or "").lower())


def _terms(text: str) -> set[str]:
    raw = re.findall(r"[a-zA-Z][a-zA-Z0-9_]{1,}|[\u4e00-\u9fff]{2,}", (text or "").lower())
    terms: set[str] = set(raw)
    compact = _normalize(text)
    terms.update(compact[index:index + 2] for index in range(max(0, len(compact) - 1)))
    return {item for item in terms if item}


def _memory_payload(row: AgentMemory, *, score: float | None = None, evidence: list | None = None) -> dict:
    payload = {
        "id": row.id,
        "tenantId": row.tenant_id,
        "sourceId": row.source_id,
        "userId": row.user_id,
        "roleScope": row.role_scope,
        "sessionId": row.session_id,
        "channel": row.channel,
        "kind": row.memory_kind,
        "status": row.status,
        "title": row.title,
        "content": row.content,
        "keywords": row.keywords_json or [],
        "metadata": row.metadata_json or {},
        "confidence": float(row.confidence or 0),
        "importance": float(row.importance or 0),
        "explicit": bool(row.explicit),
        "validFrom": row.valid_from.isoformat() if row.valid_from else None,
        "validUntil": row.valid_until.isoformat() if row.valid_until else None,
        "accessCount": row.access_count,
        "supersedesId": row.supersedes_id,
        "createdBy": row.created_by,
        "createdAt": row.created_at.isoformat() if row.created_at else None,
        "updatedAt": row.updated_at.isoformat() if row.updated_at else None,
    }
    if score is not None:
        payload["relevance"] = round(score, 6)
    if evidence is not None:
        payload["evidence"] = evidence
    return payload


def remember(
    content: str,
    *,
    user_id: str,
    source_id: int | None,
    memory_kind: str = "preference",
    title: str = "",
    tenant_id: str = "default",
    session_id: str | None = None,
    channel: str = "web",
    explicit: bool = True,
    confidence: float = 0.95,
    importance: float = 0.7,
    status: str | None = None,
    metadata: dict | None = None,
    evidence_type: str = "message",
    reference_id: str = "manual",
    excerpt: str = "",
    db_session=None,
) -> dict:
    text = " ".join((content or "").split()).strip()
    if not text:
        raise MemoryValidation("记忆内容不能为空")
    if len(text) > 4000:
        raise MemoryValidation("记忆内容不能超过 4000 个字符")
    if memory_kind not in MEMORY_KINDS:
        raise MemoryValidation("无效的记忆类型")
    owns_session = db_session is None
    session = db_session or PlatformSession()
    try:
        normalized = _normalize(text)
        duplicate = (
            session.query(AgentMemory)
            .filter(
                AgentMemory.tenant_id == tenant_id,
                AgentMemory.source_id == source_id,
                AgentMemory.user_id == user_id,
                AgentMemory.memory_kind == memory_kind,
                AgentMemory.normalized_content == normalized,
                AgentMemory.status.in_(ACTIVE_STATUSES),
            )
            .first()
        )
        if duplicate:
            duplicate.confidence = max(float(duplicate.confidence or 0), confidence)
            duplicate.importance = max(float(duplicate.importance or 0), importance)
            duplicate.updated_at = datetime.now()
            row = duplicate
        else:
            row = AgentMemory(
                id=new_id("mem"), tenant_id=tenant_id, source_id=source_id, user_id=user_id,
                session_id=session_id, channel=channel, memory_kind=memory_kind,
                status=status or ("active" if explicit else "candidate"), title=title or text[:80],
                content=text, normalized_content=normalized, keywords_json=sorted(_terms(text))[:40],
                metadata_json=metadata or {}, confidence=max(0, min(1, confidence)),
                importance=max(0, min(1, importance)), explicit=explicit,
                valid_from=datetime.now(), created_by=user_id if explicit else "memory-worker",
            )
            session.add(row)
            session.flush()
        if not session.query(AgentMemoryEvidence).filter_by(
            memory_id=row.id, evidence_type=evidence_type, reference_id=reference_id
        ).first():
            session.add(AgentMemoryEvidence(
                id=new_id("mev"), memory_id=row.id, evidence_type=evidence_type,
                reference_id=reference_id, excerpt=(excerpt or text)[:1000], metadata_json={},
            ))
        if session.get_bind().dialect.name == "postgresql" and settings.retrieval_semantic_enabled:
            embedding_job = session.query(AgentMemoryJob).filter_by(
                job_type="rebuild_embedding", reference_id=row.id
            ).first()
            if embedding_job is None:
                session.add(AgentMemoryJob(
                    id=new_id("mjob"), job_type="rebuild_embedding", reference_id=row.id,
                    status="queued", payload_json={"memoryId": row.id}, max_attempts=3,
                ))
            elif embedding_job.status == "succeeded" and duplicate is not None:
                embedding_job.status = "queued"
        if owns_session:
            session.commit()
        return _memory_payload(row)
    finally:
        if owns_session:
            session.close()


def list_memories(
    *, user_id: str | None = None, source_id: int | None = None, tenant_id: str = "default",
    status: str | None = None, memory_kind: str | None = None, include_global: bool = True,
    limit: int = 100,
) -> list[dict]:
    with PlatformSession() as session:
        query = session.query(AgentMemory).filter(AgentMemory.tenant_id == tenant_id)
        if user_id is not None:
            query = query.filter(AgentMemory.user_id == user_id)
        if source_id is not None:
            query = query.filter(
                or_(AgentMemory.source_id == source_id, AgentMemory.source_id.is_(None))
                if include_global else AgentMemory.source_id == source_id
            )
        if status:
            query = query.filter(AgentMemory.status == status)
        if memory_kind:
            query = query.filter(AgentMemory.memory_kind == memory_kind)
        rows = query.order_by(AgentMemory.updated_at.desc()).limit(max(1, min(limit, 500))).all()
        ids = [row.id for row in rows]
        evidence_map: dict[str, list] = {item: [] for item in ids}
        if ids:
            for evidence in session.query(AgentMemoryEvidence).filter(AgentMemoryEvidence.memory_id.in_(ids)).all():
                evidence_map[evidence.memory_id].append({
                    "type": evidence.evidence_type, "referenceId": evidence.reference_id,
                    "excerpt": evidence.excerpt, "createdAt": evidence.created_at.isoformat(),
                })
        return [_memory_payload(row, evidence=evidence_map.get(row.id, [])) for row in rows]


def retrieve_memories(
    question: str,
    source_id: int,
    user_id: str,
    *,
    session_id: str | None = None,
    run_id: str | None = None,
    tenant_id: str = "default",
    top_k: int | None = None,
) -> dict:
    with PlatformSession() as session:
        setting = _setting(session)
        config = dict(setting.config_json or {})
        if not setting.enabled:
            session.commit()
            return {"enabled": False, "variant": "off", "memories": [], "reason": "feature_disabled"}
        now = datetime.now()
        rows = (
            session.query(AgentMemory)
            .filter(
                AgentMemory.tenant_id == tenant_id,
                AgentMemory.status == "active",
                or_(AgentMemory.valid_until.is_(None), AgentMemory.valid_until > now),
                or_(AgentMemory.source_id == source_id, AgentMemory.source_id.is_(None)),
                or_(
                    AgentMemory.user_id == user_id,
                    and_(AgentMemory.user_id.is_(None), AgentMemory.memory_kind.in_(["semantic", "procedural"])),
                ),
            )
            .all()
        )
        query_terms = _terms(question)
        semantic_scores = _semantic_scores(session, question, tenant_id, source_id, user_id)
        ranked = []
        for row in rows:
            memory_terms = set(row.keywords_json or []) | _terms(row.content)
            overlap = len(query_terms & memory_terms) / max(1, len(query_terms | memory_terms))
            preference_boost = 0.26 if row.memory_kind == "preference" else 0
            session_boost = 0.18 if session_id and row.session_id == session_id else 0
            age_days = max(0, (now - row.updated_at).total_seconds() / 86400)
            recency = math.exp(-age_days / 90) * 0.12
            semantic_score = semantic_scores.get(row.id, 0.0)
            score = overlap * 0.34 + semantic_score * 0.32 + preference_boost + session_boost + recency
            score += float(row.importance or 0) * 0.07 + float(row.confidence or 0) * 0.05
            ranked.append((score, row))
        minimum = float(config.get("minimumScore", 0.16))
        limit = max(1, min(int(top_k or config.get("topK", 6)), 12))
        selected = [(score, row) for score, row in sorted(ranked, key=lambda item: item[0], reverse=True) if score >= minimum][:limit]
        for score, row in selected:
            row.access_count += 1
            row.last_accessed_at = now
            session.add(AgentMemoryAccessLog(
                id=new_id("mal"), memory_id=row.id, run_id=run_id, user_id=user_id,
                source_id=source_id, query_text=question[:1000], relevance_score=score,
                used_in_context=True, variant="on",
            ))
        session.commit()
        return {
            "enabled": True,
            "variant": "on",
            "memories": [_memory_payload(row, score=score) for score, row in selected],
            "scope": {"tenantId": tenant_id, "sourceId": source_id, "userId": user_id},
            "retrievalMode": "hybrid" if semantic_scores else "lexical",
        }


def _ensure_embedding_schema(session) -> None:
    dimensions = int(settings.embedding_dimensions)
    session.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    session.execute(text(f"""
        CREATE TABLE IF NOT EXISTS agent_memory_embedding (
            memory_id VARCHAR(40) PRIMARY KEY,
            tenant_id VARCHAR(64) NOT NULL,
            source_id INTEGER,
            user_id VARCHAR(64),
            embedding vector({dimensions}) NOT NULL,
            embedding_model VARCHAR(160) NOT NULL,
            content_hash VARCHAR(64) NOT NULL,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """))
    session.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_agent_memory_embedding_scope "
        "ON agent_memory_embedding (tenant_id, source_id, user_id)"
    ))


def _sync_embedding(memory_id: str) -> None:
    from app.retrieval.semantic import _vector_literal, embed_texts

    with PlatformSession() as session:
        if session.get_bind().dialect.name != "postgresql" or not settings.retrieval_semantic_enabled:
            return
        row = session.query(AgentMemory).filter_by(id=memory_id).first()
        if row is None or row.status == "deleted":
            session.execute(text("DELETE FROM agent_memory_embedding WHERE memory_id = :memory_id"), {"memory_id": memory_id})
            session.commit()
            return
        _ensure_embedding_schema(session)
        vector = embed_texts([f"{row.title}\n{row.content}"])[0]
        content_hash = hashlib.sha256(row.content.encode("utf-8")).hexdigest()
        session.execute(text("""
            INSERT INTO agent_memory_embedding (
                memory_id, tenant_id, source_id, user_id, embedding, embedding_model, content_hash, updated_at
            ) VALUES (
                :memory_id, :tenant_id, :source_id, :user_id, CAST(:embedding AS vector), :model, :content_hash, CURRENT_TIMESTAMP
            )
            ON CONFLICT (memory_id) DO UPDATE SET
                tenant_id = EXCLUDED.tenant_id, source_id = EXCLUDED.source_id,
                user_id = EXCLUDED.user_id, embedding = EXCLUDED.embedding,
                embedding_model = EXCLUDED.embedding_model, content_hash = EXCLUDED.content_hash,
                updated_at = CURRENT_TIMESTAMP
        """), {
            "memory_id": row.id, "tenant_id": row.tenant_id, "source_id": row.source_id,
            "user_id": row.user_id, "embedding": _vector_literal(vector),
            "model": settings.embedding_model, "content_hash": content_hash,
        })
        session.commit()


def _semantic_scores(session, question: str, tenant_id: str, source_id: int, user_id: str) -> dict[str, float]:
    if session.get_bind().dialect.name != "postgresql" or not settings.retrieval_semantic_enabled:
        return {}
    try:
        from app.retrieval.semantic import _embed_query, _vector_literal

        _ensure_embedding_schema(session)
        query_vector = list(_embed_query(settings.embedding_model, settings.embedding_dimensions, question))
        rows = session.execute(text("""
            SELECT memory_id, 1 - (embedding <=> CAST(:embedding AS vector)) AS similarity
            FROM agent_memory_embedding
            WHERE tenant_id = :tenant_id
              AND (source_id = :source_id OR source_id IS NULL)
              AND (user_id = :user_id OR user_id IS NULL)
              AND embedding_model = :model
            ORDER BY embedding <=> CAST(:embedding AS vector)
            LIMIT 30
        """), {
            "embedding": _vector_literal(query_vector), "tenant_id": tenant_id,
            "source_id": source_id, "user_id": user_id, "model": settings.embedding_model,
        }).mappings().all()
        return {row["memory_id"]: max(0.0, float(row["similarity"])) for row in rows}
    except Exception:
        session.rollback()
        return {}


def correct_memory(memory_id: str, content: str, *, user_id: str, admin: bool = False) -> dict:
    with PlatformSession() as session:
        old = session.query(AgentMemory).filter_by(id=memory_id).with_for_update().first()
        if old is None or (not admin and old.user_id != user_id):
            raise MemoryNotFound(f"记忆 {memory_id} 不存在")
        if _normalize(content) == old.normalized_content:
            return _memory_payload(old)
        new_payload = remember(
            content, user_id=old.user_id or user_id, source_id=old.source_id,
            memory_kind=old.memory_kind, title=old.title, tenant_id=old.tenant_id,
            session_id=old.session_id, channel=old.channel, explicit=True, confidence=1.0,
            importance=float(old.importance or 0.7), status="active", metadata=old.metadata_json,
            evidence_type="feedback", reference_id=new_id("cor"), db_session=session,
        )
        replacement = session.query(AgentMemory).filter_by(id=new_payload["id"]).one()
        replacement.supersedes_id = old.id
        old.status = "superseded"
        session.add(AgentMemoryRelation(
            id=new_id("mrl"), from_memory_id=replacement.id, to_memory_id=old.id,
            relation_type="supersedes", metadata_json={"correctedBy": user_id},
        ))
        session.add(AgentMemoryFeedback(
            id=new_id("mfb"), memory_id=old.id, user_id=user_id, action="correct", comment=content[:1000],
        ))
        session.commit()
        return _memory_payload(replacement)


def forget_memory(memory_id: str, *, user_id: str, admin: bool = False) -> dict:
    with PlatformSession() as session:
        row = session.query(AgentMemory).filter_by(id=memory_id).with_for_update().first()
        if row is None or (not admin and row.user_id != user_id):
            raise MemoryNotFound(f"记忆 {memory_id} 不存在")
        row.status = "deleted"
        row.valid_until = datetime.now()
        session.add(AgentMemoryFeedback(
            id=new_id("mfb"), memory_id=row.id, user_id=user_id, action="forget",
            comment="用户要求遗忘" if not admin else "管理员按治理策略删除",
        ))
        session.commit()
        return {"memoryId": row.id, "deleted": True}


def extract_explicit_preferences(text: str) -> list[dict]:
    """Extract only user-controlled preferences; never infer sensitive identity facts."""
    normalized = " ".join((text or "").split())
    markers = ("请记住", "以后", "默认", "我习惯", "我更喜欢", "下次")
    if not any(marker in normalized for marker in markers):
        return []
    if not any(term in normalized for term in ("图", "时间", "范围", "产线", "工序", "排序", "单位", "报告", "展示", "分析")):
        return []
    return [{
        "kind": "preference", "title": "分析展示偏好", "content": normalized[:1000],
        "confidence": 0.98, "importance": 0.8, "explicit": True,
    }]


def capture_run(run_id: str, *, db_session=None) -> list[dict]:
    owns_session = db_session is None
    session = db_session or PlatformSession()
    try:
        run = session.query(AnalysisRun).filter_by(id=run_id).first()
        if run is None:
            raise MemoryNotFound(f"Run {run_id} 不存在")
        setting = _setting(session)
        if not setting.enabled:
            if owns_session:
                session.commit()
            return []
        business_session = session.query(AnalysisSession).filter_by(id=run.session_id).first()
        trigger = session.query(AnalysisMessage).filter_by(id=run.trigger_message_id).first()
        if business_session is None or trigger is None:
            return []
        external_job = (
            session.query(ExternalChatJob)
            .filter_by(run_id=run.id)
            .order_by(ExternalChatJob.created_at.desc())
            .first()
        )
        channel = external_job.channel if external_job is not None else "web"
        created: list[dict] = []
        config = dict(setting.config_json or {})
        if config.get("captureExplicitPreferences", True):
            for candidate in extract_explicit_preferences(trigger.content):
                created.append(remember(
                    candidate["content"], user_id=business_session.user_id, source_id=run.source_id,
                    memory_kind=candidate["kind"], title=candidate["title"], session_id=run.session_id,
                    channel=channel,
                    explicit=True, confidence=candidate["confidence"], importance=candidate["importance"],
                    evidence_type="message", reference_id=trigger.id, db_session=session,
                ))
        if config.get("captureEpisodes", True) and run.status == "succeeded":
            answer = (
                session.query(AnalysisMessage)
                .filter_by(run_id=run.id, role="assistant")
                .order_by(AnalysisMessage.sequence_no.desc())
                .first()
            )
            # Store a compact outcome, not raw query rows or hidden model reasoning.
            content = f"问题：{trigger.content[:500]}\n结果：{(answer.content if answer else '分析成功')[:1000]}"
            created.append(remember(
                content, user_id=business_session.user_id, source_id=run.source_id,
                memory_kind="episodic", title=trigger.content[:100], session_id=run.session_id,
                channel=channel,
                explicit=False, confidence=0.76, importance=0.56, status="active",
                metadata={
                    "runId": run.id,
                    "outcome": run.status,
                    "workflowVersion": run.workflow_version,
                    "externalTenantKey": external_job.tenant_key if external_job is not None else "",
                },
                evidence_type="run", reference_id=run.id, excerpt=trigger.content, db_session=session,
            ))
        if owns_session:
            session.commit()
        return created
    finally:
        if owns_session:
            session.close()


def enqueue_run_capture(run_id: str, *, db_session=None) -> dict:
    owns_session = db_session is None
    session = db_session or PlatformSession()
    try:
        existing = session.query(AgentMemoryJob).filter_by(job_type="capture_run", reference_id=run_id).first()
        if existing:
            return {"jobId": existing.id, "status": existing.status}
        row = AgentMemoryJob(
            id=new_id("mjob"), job_type="capture_run", reference_id=run_id,
            status="queued", payload_json={"runId": run_id}, max_attempts=3,
        )
        session.add(row)
        if owns_session:
            session.commit()
        return {"jobId": row.id, "status": row.status}
    finally:
        if owns_session:
            session.close()


def process_job(job_id: str) -> dict:
    with PlatformSession() as session:
        job = session.query(AgentMemoryJob).filter_by(id=job_id).with_for_update().first()
        if job is None:
            raise MemoryNotFound(f"记忆任务 {job_id} 不存在")
        if job.status == "succeeded":
            return {"jobId": job.id, "status": job.status}
        if job.status == "queued":
            job.status = "running"
            job.attempt_count += 1
            session.commit()
        job_type = job.job_type
        reference_id = job.reference_id
    try:
        if job_type == "capture_run":
            capture_run(reference_id)
        elif job_type == "rebuild_embedding":
            _sync_embedding(reference_id)
        elif job_type == "consolidate":
            consolidate()
        else:
            raise MemoryValidation(f"不支持的记忆任务：{job_type}")
    except Exception as exc:
        with PlatformSession() as session:
            failed = session.query(AgentMemoryJob).filter_by(id=job_id).with_for_update().one()
            failed.error = f"{type(exc).__name__}: {exc}"[:2000]
            failed.status = "failed" if failed.attempt_count >= failed.max_attempts else "queued"
            failed.lease_owner = None
            failed.lease_expires_at = None
            session.commit()
        raise
    with PlatformSession() as session:
        completed = session.query(AgentMemoryJob).filter_by(id=job_id).with_for_update().one()
        completed.status = "succeeded"
        completed.error = ""
        completed.lease_owner = None
        completed.lease_expires_at = None
        session.commit()
        return {"jobId": completed.id, "status": completed.status}


def claim_next_job(worker_id: str) -> str | None:
    """Atomically lease one job; PostgreSQL skip-locked permits safe horizontal workers."""
    with PlatformSession() as session:
        now = datetime.now()
        exhausted = (
            session.query(AgentMemoryJob)
            .filter(
                AgentMemoryJob.status == "running",
                AgentMemoryJob.lease_expires_at.is_not(None),
                AgentMemoryJob.lease_expires_at < now,
                AgentMemoryJob.attempt_count >= AgentMemoryJob.max_attempts,
            )
            .all()
        )
        for item in exhausted:
            item.status = "failed"
            item.error = item.error or "memory worker lease expired after maximum attempts"
            item.lease_owner = None
            item.lease_expires_at = None
        if exhausted:
            session.flush()
        query = (
            session.query(AgentMemoryJob)
            .filter(
                AgentMemoryJob.attempt_count < AgentMemoryJob.max_attempts,
                or_(
                    and_(
                        AgentMemoryJob.status == "queued",
                        or_(
                            AgentMemoryJob.lease_expires_at.is_(None),
                            AgentMemoryJob.lease_expires_at < now,
                        ),
                    ),
                    and_(
                        AgentMemoryJob.status == "running",
                        AgentMemoryJob.lease_expires_at.is_not(None),
                        AgentMemoryJob.lease_expires_at < now,
                    ),
                ),
            )
            .order_by(AgentMemoryJob.created_at)
        )
        if session.get_bind().dialect.name == "postgresql":
            query = query.with_for_update(skip_locked=True)
        else:
            query = query.with_for_update()
        row = query.first()
        if row is None:
            return None
        row.status = "running"
        row.attempt_count += 1
        row.lease_owner = worker_id
        row.lease_expires_at = now + timedelta(minutes=5)
        session.commit()
        return row.id


def consolidate(*, tenant_id: str = "default") -> dict:
    with PlatformSession() as session:
        now = datetime.now()
        expired = (
            session.query(AgentMemory)
            .filter(AgentMemory.tenant_id == tenant_id, AgentMemory.status == "active", AgentMemory.valid_until <= now)
            .all()
        )
        for row in expired:
            row.status = "expired"
        candidates = session.query(AgentMemory).filter_by(tenant_id=tenant_id, status="candidate").all()
        activated = 0
        for row in candidates:
            evidence_count = session.query(func.count(AgentMemoryEvidence.id)).filter_by(memory_id=row.id).scalar() or 0
            if row.explicit or (float(row.confidence or 0) >= 0.82 and evidence_count >= 2):
                row.status = "active"
                activated += 1
        session.commit()
        return {"expired": len(expired), "activated": activated}


def promote_to_skill(memory_id: str, *, reviewer: str) -> dict:
    with PlatformSession() as session:
        row = session.query(AgentMemory).filter_by(id=memory_id).first()
        if row is None:
            raise MemoryNotFound(f"记忆 {memory_id} 不存在")
        if row.memory_kind not in {"episodic", "procedural"}:
            raise MemoryValidation("只有调查经验或程序性记忆可提升为候选技能")
        slug = "memory-" + hashlib.sha256(row.normalized_content.encode()).hexdigest()[:16]
        existing = session.query(AnalysisSkill).filter_by(slug=slug, source_id=row.source_id).first()
        if existing is None:
            run_id = str((row.metadata_json or {}).get("runId") or "")
            existing = AnalysisSkill(
                id=new_id("skill"), source_id=row.source_id, name=row.title[:160], slug=slug,
                description="由经过验证的调查记忆沉淀，发布前必须由管理员复核。",
                topic="记忆沉淀", status="candidate", trigger_json={"terms": list(row.keywords_json or [])[:12]},
                preconditions_json=["仍受当前用户、数据源与只读安全边界约束"],
                procedure_json=[{"step": "参考历史调查路径", "instruction": row.content[:1200]}],
                output_policy_json={"discloseMemoryEvidence": True},
                constraints_json=["不得扩大表或字段白名单", "不得覆盖企业已发布指标口径"],
                evidence_run_ids_json=[run_id] if run_id else [], confidence=min(0.9, float(row.confidence or 0.7)),
                created_by=reviewer,
            )
            session.add(existing)
            session.flush()
        session.add(AgentMemoryRelation(
            id=new_id("mrl"), from_memory_id=row.id, to_memory_id=existing.id,
            relation_type="promotes", metadata_json={"reviewer": reviewer},
        ))
        session.commit()
        return {"memoryId": row.id, "candidateSkillId": existing.id, "status": existing.status}


def governance_summary() -> dict:
    with PlatformSession() as session:
        counts = dict(session.query(AgentMemory.status, func.count(AgentMemory.id)).group_by(AgentMemory.status).all())
        kinds = dict(session.query(AgentMemory.memory_kind, func.count(AgentMemory.id)).group_by(AgentMemory.memory_kind).all())
        total_access = session.query(func.count(AgentMemoryAccessLog.id)).scalar() or 0
        used_access = session.query(func.count(AgentMemoryAccessLog.id)).filter_by(used_in_context=True).scalar() or 0
        setting = _setting(session)
        experiment = {"off": [], "on": []}
        for run in session.query(AnalysisRun).filter(AnalysisRun.finished_at.is_not(None)).order_by(AnalysisRun.created_at.desc()).limit(500).all():
            variant = run.memory_experiment_variant if run.memory_experiment_variant in experiment else "off"
            duration_ms = None
            if run.started_at and run.finished_at:
                duration_ms = int((run.finished_at - run.started_at).total_seconds() * 1000)
            experiment[variant].append({"success": run.status == "succeeded", "tokens": int(run.total_tokens or 0), "durationMs": duration_ms})
        experiment_payload = {}
        for variant, runs in experiment.items():
            durations = [item["durationMs"] for item in runs if item["durationMs"] is not None]
            experiment_payload[variant] = {
                "runs": len(runs),
                "successRate": round(sum(1 for item in runs if item["success"]) / max(1, len(runs)), 4),
                "avgTokens": round(sum(item["tokens"] for item in runs) / max(1, len(runs))),
                "avgDurationMs": round(sum(durations) / max(1, len(durations))) if durations else 0,
            }
        session.commit()
        return {
            "settings": _setting_payload(setting), "total": sum(counts.values()),
            "byStatus": counts, "byKind": kinds, "recallCount": total_access,
            "contextUseRate": round(used_access / max(1, total_access), 4),
            "experiment": experiment_payload,
            "guardrails": ["作用域先过滤后排序", "不存储隐式思维链", "记忆不能扩大数据权限", "发布指标口径优先"],
        }
