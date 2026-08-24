"""受治理的技能学习闭环：开关、召回、候选生成、审核和 A/B 观测。"""

from __future__ import annotations

import re
from datetime import datetime

from sqlalchemy import or_

from app.agent.ids import new_id
from app.db.models import (
    AnalysisMessage,
    AnalysisRun,
    AnalysisSkill,
    AnalysisSkillUsage,
    DataSource,
    LearningFeedback,
    PlatformFeatureFlag,
)
from app.db.platform import PlatformSession


FEATURE_KEY = "analysis_skill_enhancement"
ALLOWED_STATUSES = {"candidate", "published", "rejected", "retired"}
ALLOWED_RATINGS = {"helpful", "needs_improvement", "corrected"}


class SkillNotFound(KeyError):
    pass


class SkillValidation(ValueError):
    pass


def _setting(session) -> PlatformFeatureFlag:
    row = session.query(PlatformFeatureFlag).filter_by(key=FEATURE_KEY).first()
    if row is None:
        row = PlatformFeatureFlag(
            key=FEATURE_KEY,
            enabled=False,
            config_json={
                "topK": 2,
                "minimumScore": 0.18,
                "candidateOnPositiveFeedback": True,
            },
        )
        session.add(row)
        session.flush()
    return row


def _setting_payload(row: PlatformFeatureFlag) -> dict:
    return {
        "key": row.key,
        "enabled": bool(row.enabled),
        "variant": "on" if row.enabled else "off",
        "config": row.config_json or {},
        "updatedBy": row.updated_by,
        "updatedAt": row.updated_at.isoformat() if row.updated_at else None,
    }


def get_settings() -> dict:
    with PlatformSession() as session:
        row = _setting(session)
        session.commit()
        return _setting_payload(row)


def update_settings(enabled: bool, *, updated_by: str) -> dict:
    with PlatformSession() as session:
        row = _setting(session)
        row.enabled = bool(enabled)
        row.updated_by = updated_by or "admin"
        row.updated_at = datetime.now()
        session.commit()
        return _setting_payload(row)


def snapshot_settings(session=None) -> dict:
    if session is not None:
        return _setting_payload(_setting(session))
    return get_settings()


def _skill_payload(row: AnalysisSkill, relevance: float | None = None) -> dict:
    payload = {
        "id": row.id,
        "sourceId": row.source_id,
        "name": row.name,
        "slug": row.slug,
        "description": row.description,
        "topic": row.topic,
        "status": row.status,
        "version": row.version,
        "triggers": row.trigger_json or {},
        "preconditions": row.preconditions_json or [],
        "steps": row.procedure_json or [],
        "outputPolicy": row.output_policy_json or {},
        "constraints": row.constraints_json or [],
        "evidenceRunIds": row.evidence_run_ids_json or [],
        "confidence": float(row.confidence or 0),
        "createdBy": row.created_by,
        "reviewedBy": row.reviewed_by,
        "createdAt": row.created_at.isoformat() if row.created_at else None,
        "updatedAt": row.updated_at.isoformat() if row.updated_at else None,
    }
    if relevance is not None:
        payload["relevance"] = round(relevance, 6)
    return payload


def list_skills(*, status: str | None = None, source_id: int | None = None, keyword: str = "") -> list[dict]:
    with PlatformSession() as session:
        query = session.query(AnalysisSkill)
        if status:
            if status not in ALLOWED_STATUSES:
                raise SkillValidation("无效的技能状态")
            query = query.filter_by(status=status)
        if source_id is not None:
            query = query.filter(or_(AnalysisSkill.source_id.is_(None), AnalysisSkill.source_id == source_id))
        if keyword.strip():
            like = f"%{keyword.strip()}%"
            query = query.filter(or_(AnalysisSkill.name.like(like), AnalysisSkill.description.like(like)))
        rows = query.order_by(AnalysisSkill.updated_at.desc(), AnalysisSkill.name).all()
        return [_skill_payload(row) for row in rows]


def get_skill(skill_id: str) -> dict:
    with PlatformSession() as session:
        row = session.query(AnalysisSkill).filter_by(id=skill_id).first()
        if row is None:
            raise SkillNotFound(f"技能 {skill_id} 不存在")
        return _skill_payload(row)


def _lexical_score(question: str, row: AnalysisSkill) -> float:
    normalized = re.sub(r"\s+", "", question.lower())
    terms = [str(item).strip().lower() for item in (row.trigger_json or {}).get("terms", []) if str(item).strip()]
    def matches(term: str) -> bool:
        compact = re.sub(r"\s+", "", term)
        if compact in normalized:
            return True
        # 中文业务问法常在概念与变化之间插入“为什么/最近”等词；
        # 用短语二元片段覆盖避免退化成仅靠完整字符串的脆弱匹配。
        pairs = {compact[index:index + 2] for index in range(max(0, len(compact) - 1))}
        return bool(pairs) and sum(1 for pair in pairs if pair in normalized) / len(pairs) >= 0.5

    hits = [term for term in terms if matches(term)]
    topic_hit = bool(row.topic and row.topic.removesuffix("分析") in question)
    name_hit = bool(row.name and row.name.removesuffix("调查") in question)
    return min(1.0, len(hits) * 0.34 + (0.18 if topic_hit else 0) + (0.2 if name_hit else 0))


def retrieve_skills(question: str, source_id: int, *, top_k: int | None = None) -> dict:
    with PlatformSession() as session:
        setting = _setting(session)
        if not setting.enabled:
            session.commit()
            return {"enabled": False, "variant": "off", "skills": [], "reason": "feature_disabled"}
        rows = (
            session.query(AnalysisSkill)
            .filter(
                AnalysisSkill.status == "published",
                or_(AnalysisSkill.source_id.is_(None), AnalysisSkill.source_id == source_id),
            )
            .all()
        )
        from app.retrieval import semantic

        semantic_result = semantic.search(
            session,
            question,
            source_id,
            object_types=("analysis_skill",),
            top_k=max(5, int(top_k or 2)),
        )
        semantic_scores = {
            item["objectId"]: float(item["similarity"])
            for item in semantic_result.hits
            if item.get("objectType") == "analysis_skill"
        }
        config = setting.config_json or {}
        threshold = float(config.get("minimumScore", 0.18))
        limit = max(1, min(int(top_k or config.get("topK", 2)), 5))
        ranked = sorted(
            (
                (
                    row,
                    _lexical_score(question, row) * 0.65
                    + semantic_scores.get(row.id, 0.0) * 0.35,
                )
                for row in rows
            ),
            key=lambda pair: pair[1],
            reverse=True,
        )
        matches = [_skill_payload(row, score) for row, score in ranked if score >= threshold][:limit]
        session.commit()
        return {
            "enabled": True,
            "variant": "on",
            "skills": matches,
            "reason": "matched" if matches else "no_match",
            "retrieval": {
                "semanticStatus": semantic_result.status,
                "mode": "hybrid" if semantic_result.hits else "lexical_fallback",
            },
        }


def record_usage(run_id: str, task_id: str | None, skill_context: dict, *, db_session=None) -> None:
    if not skill_context.get("enabled"):
        return
    def persist(session) -> None:
        for item in skill_context.get("skills") or []:
            exists = session.query(AnalysisSkillUsage.id).filter_by(
                run_id=run_id, task_id=task_id, skill_id=item["id"]
            ).first()
            if exists:
                continue
            session.add(
                AnalysisSkillUsage(
                    id=new_id("sku"),
                    run_id=run_id,
                    task_id=task_id,
                    skill_id=item["id"],
                    variant="on",
                    relevance_score=float(item.get("relevance") or 0),
                )
            )
    if db_session is not None:
        persist(db_session)
        return
    with PlatformSession() as session:
        persist(session)
        session.commit()


def finish_run_usage(run_id: str, outcome: str, *, db_session=None) -> None:
    def persist(session) -> None:
        rows = session.query(AnalysisSkillUsage).filter_by(run_id=run_id).all()
        now = datetime.now()
        for row in rows:
            row.outcome = outcome
            row.finished_at = now
    if db_session is not None:
        persist(db_session)
        return
    with PlatformSession() as session:
        persist(session)
        session.commit()


def _candidate_profile(question: str) -> tuple[str, str, str, list[str]]:
    if any(term in question for term in ("良率", "合格率", "不良率", "质量")):
        return "良率异常调查", "learned-yield-investigation", "质量分析", ["良率", "合格率", "不良率", "质量异常"]
    if any(term in question for term in ("停机", "故障", "设备")):
        return "设备异常调查", "learned-equipment-investigation", "设备分析", ["停机", "故障", "设备异常"]
    if any(term in question for term in ("库存", "缺货", "物料")):
        return "库存风险调查", "learned-inventory-investigation", "库存分析", ["库存", "安全库存", "缺货"]
    return "通用业务分析流程", "learned-general-investigation", "通用分析", [question[:40]]


def _upsert_candidate(session, run: AnalysisRun, question: str, user_id: str) -> AnalysisSkill:
    name, slug, topic, terms = _candidate_profile(question)
    row = (
        session.query(AnalysisSkill)
        .filter_by(source_id=run.source_id, slug=slug, status="candidate")
        .order_by(AnalysisSkill.version.desc())
        .first()
    )
    if row is None:
        published_version = session.query(AnalysisSkill.version).filter_by(
            source_id=run.source_id, slug=slug, status="published"
        ).order_by(AnalysisSkill.version.desc()).scalar()
        row = AnalysisSkill(
            id=new_id("skill"),
            source_id=run.source_id,
            name=name,
            slug=slug,
            topic=topic,
            description="由成功分析与用户反馈归纳的候选流程，发布前必须由管理员复核。",
            status="candidate",
            version=int(published_version or 0) + 1,
            trigger_json={"terms": terms},
            preconditions_json=["确认数据覆盖与时间范围", "确认指标为已发布口径或明确披露临时推导"],
            procedure_json=[
                "定位可信业务语义和当前数据源中的物理资源",
                "核对口径、时间窗和比较对象是否可比",
                "执行只读查询并按可用业务维度定位主要贡献项",
                "用结果集校验结论，并披露假设、缺失数据和置信度",
            ],
            output_policy_json={"required": ["口径", "时间范围", "关键发现", "证据限制"]},
            constraints_json=["不得扩大白名单", "不得跳过 SQL 安全校验", "不得把历史结果当作当前事实"],
            evidence_run_ids_json=[run.id],
            confidence=0.6,
            created_by=user_id,
        )
        session.add(row)
    else:
        evidence = list(row.evidence_run_ids_json or [])
        if run.id not in evidence:
            evidence.append(run.id)
        row.evidence_run_ids_json = evidence
        row.confidence = min(0.85, 0.55 + len(evidence) * 0.05)
        row.updated_at = datetime.now()
    session.flush()
    return row


def record_feedback(
    run_id: str,
    *,
    user_id: str,
    rating: str,
    comment: str = "",
    correction_type: str = "",
) -> dict:
    if rating not in ALLOWED_RATINGS:
        raise SkillValidation("无效的反馈类型")
    with PlatformSession() as session:
        run = session.query(AnalysisRun).filter_by(id=run_id).first()
        if run is None:
            raise SkillNotFound(f"Run {run_id} 不存在")
        trigger = session.query(AnalysisMessage).filter_by(id=run.trigger_message_id).first()
        feedback = LearningFeedback(
            id=new_id("fb"),
            run_id=run_id,
            user_id=user_id,
            rating=rating,
            correction_type=correction_type,
            comment=comment.strip(),
        )
        session.add(feedback)
        candidate = None
        if run.status == "succeeded" and rating in {"helpful", "corrected"}:
            candidate = _upsert_candidate(session, run, trigger.content if trigger else "", user_id)
            feedback.candidate_skill_id = candidate.id
        session.commit()
        return {
            "id": feedback.id,
            "runId": run_id,
            "rating": rating,
            "candidateSkillId": candidate.id if candidate else None,
            "message": "反馈已记录；候选技能需经管理员审核后才会生效。" if candidate else "反馈已记录。",
        }


def review_skill(skill_id: str, *, action: str, reviewer: str) -> dict:
    target = {"publish": "published", "reject": "rejected", "retire": "retired"}.get(action)
    if target is None:
        raise SkillValidation("无效的审核动作")
    with PlatformSession() as session:
        row = session.query(AnalysisSkill).filter_by(id=skill_id).first()
        if row is None:
            raise SkillNotFound(f"技能 {skill_id} 不存在")
        row.status = target
        row.reviewed_by = reviewer
        row.updated_at = datetime.now()
        if target == "published":
            row.published_at = datetime.now()
            row.retired_at = None
        elif target == "retired":
            row.retired_at = datetime.now()
        if target in {"published", "retired"}:
            from app.retrieval.index_jobs import enqueue_all_sources, enqueue_index_job

            reason = f"分析技能{target}：{row.name} v{row.version}"
            if row.source_id is None:
                enqueue_all_sources(session, mode="incremental", reason=reason)
            elif session.query(DataSource.id).filter_by(id=row.source_id).first():
                enqueue_index_job(session, row.source_id, mode="incremental", reason=reason)
        session.commit()
        return _skill_payload(row)


def experiment_summary() -> dict:
    with PlatformSession() as session:
        rows = session.query(AnalysisRun).filter(AnalysisRun.status.in_(["succeeded", "failed", "blocked"])).all()
        feedback_rows = session.query(LearningFeedback).all()
        feedback_by_run = {item.run_id: item for item in feedback_rows}
        groups = {"off": [], "on": []}
        for row in rows:
            groups["on" if row.skill_enhancement_enabled else "off"].append(row)
        result = {}
        for variant, items in groups.items():
            completed = len(items)
            durations = [
                (item.finished_at - item.started_at).total_seconds() * 1000
                for item in items
                if item.started_at and item.finished_at
            ]
            rated = [feedback_by_run[item.id] for item in items if item.id in feedback_by_run]
            result[variant] = {
                "runs": completed,
                "successRate": round(sum(1 for item in items if item.status == "succeeded") / completed, 4) if completed else None,
                "averageTokens": round(sum(item.total_tokens or 0 for item in items) / completed, 1) if completed else None,
                "averageDurationMs": round(sum(durations) / len(durations), 1) if durations else None,
                "helpfulRate": round(sum(1 for item in rated if item.rating == "helpful") / len(rated), 4) if rated else None,
            }
        return result
