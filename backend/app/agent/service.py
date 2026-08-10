"""Session/Run 当前投影和提交入口。"""

from datetime import datetime

from sqlalchemy import func

from app.agent.budgets import default_budget
from app.agent.event_store import append_event, serialize_event
from app.agent.ids import new_id
from app.db.models import (
    AnalysisArtifact,
    AnalysisBranch,
    AnalysisCheckpoint,
    AnalysisEvent,
    AnalysisInteraction,
    AnalysisMessage,
    AnalysisRun,
    AnalysisRunStep,
    AnalysisSession,
    AnalysisTask,
    DataSource,
)
from app.db.platform import PlatformSession


class AnalysisNotFound(KeyError):
    pass


class AnalysisConflict(RuntimeError):
    pass


def create_session(title: str, source_id: int, user_id: str) -> dict:
    with PlatformSession() as session:
        source = session.query(DataSource).filter_by(id=source_id).first()
        if source is None:
            raise AnalysisNotFound(f"数据源 {source_id} 不存在")
        row = AnalysisSession(id=new_id("ses"), title=title.strip(), user_id=user_id, source_id=source_id)
        session.add(row)
        session.commit()
        return _session_dict(row, source.name)


def list_sessions(user_id: str) -> list[dict]:
    with PlatformSession() as session:
        rows = (
            session.query(AnalysisSession, DataSource.name)
            .join(DataSource, DataSource.id == AnalysisSession.source_id)
            .filter(
                AnalysisSession.user_id == user_id,
                AnalysisSession.status != "deleted",
            )
            .order_by(AnalysisSession.updated_at.desc())
            .all()
        )
        if not rows:
            return []

        session_ids = [row.id for row, _ in rows]
        latest_questions: dict[str, AnalysisMessage] = {}
        for message in (
            session.query(AnalysisMessage)
            .filter(
                AnalysisMessage.session_id.in_(session_ids),
                AnalysisMessage.role == "user",
            )
            .order_by(AnalysisMessage.session_id, AnalysisMessage.sequence_no.desc())
            .all()
        ):
            latest_questions.setdefault(message.session_id, message)

        run_counts = dict(
            session.query(AnalysisRun.session_id, func.count(AnalysisRun.id))
            .filter(AnalysisRun.session_id.in_(session_ids))
            .group_by(AnalysisRun.session_id)
            .all()
        )
        latest_runs: dict[str, AnalysisRun] = {}
        for run in (
            session.query(AnalysisRun)
            .filter(AnalysisRun.session_id.in_(session_ids))
            .order_by(AnalysisRun.session_id, AnalysisRun.created_at.desc())
            .all()
        ):
            latest_runs.setdefault(run.session_id, run)

        result = []
        for row, source_name in rows:
            item = _session_dict(row, source_name)
            latest_question = latest_questions.get(row.id)
            latest_run = latest_runs.get(row.id)
            item["displayTitle"] = (
                " ".join(latest_question.content.split())[:120]
                if latest_question
                else row.title
            )
            item["runCount"] = int(run_counts.get(row.id, 0))
            item["latestRunStatus"] = latest_run.status if latest_run else None
            result.append(item)
        return result


def delete_session(session_id: str, user_id: str) -> dict:
    """Soft-delete a user conversation while retaining its evidence for audit."""
    with PlatformSession() as session:
        row = (
            session.query(AnalysisSession)
            .filter_by(id=session_id, user_id=user_id)
            .with_for_update()
            .first()
        )
        if row is None or row.status == "deleted":
            raise AnalysisNotFound(f"会话 {session_id} 不存在")
        row.status = "deleted"
        row.updated_at = datetime.now()
        session.commit()
        return {
            "sessionId": row.id,
            "deleted": True,
            "retention": "audit",
        }


def get_session(session_id: str) -> dict:
    with PlatformSession() as session:
        row = session.query(AnalysisSession).filter_by(id=session_id).first()
        if row is None:
            raise AnalysisNotFound(f"会话 {session_id} 不存在")
        source = session.query(DataSource).filter_by(id=row.source_id).first()
        messages = session.query(AnalysisMessage).filter_by(session_id=session_id).order_by(AnalysisMessage.sequence_no).all()
        runs = session.query(AnalysisRun).filter_by(session_id=session_id).order_by(AnalysisRun.created_at).all()
        result = _session_dict(row, source.name if source else f"数据源 {row.source_id}")
        result["messages"] = [_message_dict(item) for item in messages]
        result["runs"] = [_run_summary(item) for item in runs]
        result["branchContext"] = _branch_context(session, row)
        return result


def create_run(session_id: str, question: str, idempotency_key: str | None = None) -> dict:
    with PlatformSession() as session:
        parent = session.query(AnalysisSession).filter_by(id=session_id).with_for_update().first()
        if parent is None:
            raise AnalysisNotFound(f"会话 {session_id} 不存在")
        if parent.status != "active":
            raise AnalysisConflict("已归档会话不能创建新运行")
        if idempotency_key:
            existing = session.query(AnalysisRun).filter_by(
                session_id=session_id, idempotency_key=idempotency_key
            ).first()
            if existing:
                return _run_acceptance(existing)
        sequence_no = (
            session.query(func.coalesce(func.max(AnalysisMessage.sequence_no), 0))
            .filter_by(session_id=session_id)
            .scalar()
            + 1
        )
        message = AnalysisMessage(
            id=new_id("msg"),
            session_id=session_id,
            role="user",
            content=question.strip(),
            sequence_no=sequence_no,
        )
        run = AnalysisRun(
            id=new_id("run"),
            session_id=session_id,
            trigger_message_id=message.id,
            idempotency_key=idempotency_key,
            source_id=parent.source_id,
            budget_snapshot_json=default_budget(),
        )
        message.run_id = run.id
        session.add_all([message, run])
        parent.updated_at = datetime.now()
        append_event(
            session,
            run,
            "run.accepted",
            {"status": "queued", "question": question.strip(), "sourceId": parent.source_id},
        )
        session.commit()
        return _run_acceptance(run)


def get_run(run_id: str) -> dict:
    with PlatformSession() as session:
        run = session.query(AnalysisRun).filter_by(id=run_id).first()
        if run is None:
            raise AnalysisNotFound(f"Run {run_id} 不存在")
        tasks = session.query(AnalysisTask).filter_by(run_id=run_id).order_by(AnalysisTask.task_order).all()
        steps = session.query(AnalysisRunStep).filter_by(run_id=run_id).order_by(
            AnalysisRunStep.step_order, AnalysisRunStep.attempt
        ).all()
        artifacts = session.query(AnalysisArtifact).filter_by(run_id=run_id).order_by(AnalysisArtifact.created_at).all()
        checkpoints = session.query(AnalysisCheckpoint).filter_by(run_id=run_id).order_by(AnalysisCheckpoint.created_at).all()
        interaction = (
            session.query(AnalysisInteraction)
            .filter(AnalysisInteraction.run_id == run_id, AnalysisInteraction.status.in_(["pending", "resolved"]))
            .order_by(AnalysisInteraction.created_at.desc())
            .first()
        )
        trigger = session.query(AnalysisMessage).filter_by(id=run.trigger_message_id).first()
        assistant = (
            session.query(AnalysisMessage)
            .filter_by(run_id=run_id, role="assistant")
            .order_by(AnalysisMessage.sequence_no.desc())
            .first()
        )
        duration_events = session.query(AnalysisEvent).filter_by(run_id=run_id).order_by(AnalysisEvent.seq).all()
        result = _run_summary(run)
        result["activeDurationMs"] = _active_duration_ms(duration_events)
        result["question"] = trigger.content if trigger else ""
        result["assistantMessage"] = _message_dict(assistant) if assistant else None
        result["budget"] = run.budget_snapshot_json
        result["tasks"] = [
            {
                "id": item.id,
                "order": item.task_order,
                "title": item.title,
                "question": item.normalized_question,
                "mode": item.mode,
                "analysisType": item.analysis_type,
                "status": item.status,
                "dependsOn": item.depends_on_json or [],
                "intent": item.intent_json or {},
                "assumptions": item.assumptions_json or [],
            }
            for item in tasks
        ]
        result["steps"] = [
            {
                "id": item.id,
                "taskId": item.task_id,
                "stepKey": item.step_key,
                "stepOrder": item.step_order,
                "attempt": item.attempt,
                "status": item.status,
                "label": item.label,
                "detail": item.detail,
                "error": item.error_json,
            }
            for item in steps
        ]
        result["error"] = next(
            (
                item.error_json
                for item in reversed(steps)
                if item.error_json and item.status == "failed"
            ),
            None,
        )
        result["artifacts"] = [_artifact_dict(item) for item in artifacts]
        result["checkpoints"] = [_checkpoint_dict(item) for item in checkpoints]
        result["interaction"] = _interaction_dict(interaction) if interaction else None
        return result


def respond_to_interaction(interaction_id: str, answer: str) -> dict:
    with PlatformSession() as session:
        interaction = session.query(AnalysisInteraction).filter_by(id=interaction_id).with_for_update().first()
        if interaction is None:
            raise AnalysisNotFound(f"交互 {interaction_id} 不存在")
        if interaction.status != "pending":
            raise AnalysisConflict("该澄清问题已经回答或失效")
        run = session.query(AnalysisRun).filter_by(id=interaction.run_id).with_for_update().one()
        if run.status != "waiting_user":
            raise AnalysisConflict("当前运行不在等待用户状态")
        interaction.status = "resolved"
        interaction.response_json = {"answer": answer.strip()}
        interaction.resolved_at = datetime.now()
        run.status = "queued"
        run.finished_at = None
        run.lease_owner = None
        run.lease_expires_at = None
        parent_session = session.query(AnalysisSession).filter_by(id=run.session_id).first()
        if parent_session:
            parent_session.updated_at = datetime.now()
        task = session.query(AnalysisTask).filter_by(id=interaction.task_id).first() if interaction.task_id else None
        if task:
            task.status = "pending"
        append_event(
            session,
            run,
            "clarification.resolved",
            {"interactionId": interaction.id, "answer": answer.strip(), "status": "queued"},
            task_id=interaction.task_id,
            step_key=interaction.resume_node,
        )
        session.commit()
        return {**_run_acceptance(run), "interactionId": interaction.id, "resumed": True}


def list_checkpoints(run_id: str) -> list[dict]:
    with PlatformSession() as session:
        if session.query(AnalysisRun.id).filter_by(id=run_id).first() is None:
            raise AnalysisNotFound(f"Run {run_id} 不存在")
        rows = session.query(AnalysisCheckpoint).filter_by(run_id=run_id).order_by(AnalysisCheckpoint.created_at).all()
        return [_checkpoint_dict(item) for item in rows]


def create_branch(
    checkpoint_id: str,
    user_id: str,
    title: str | None = None,
    *,
    instruction: str | None = None,
    adjustments: dict | None = None,
    reuse_artifacts: str = "auto",
) -> dict:
    with PlatformSession() as session:
        checkpoint = session.query(AnalysisCheckpoint).filter_by(id=checkpoint_id).first()
        if checkpoint is None or not checkpoint.rollbackable:
            raise AnalysisNotFound(f"可分支检查点 {checkpoint_id} 不存在")
        parent = session.query(AnalysisSession).filter_by(id=checkpoint.session_id).first()
        parent_run = session.query(AnalysisRun).filter_by(id=checkpoint.run_id).first()
        if parent is None or parent_run is None:
            raise AnalysisNotFound("检查点对应的父会话不存在")
        child = AnalysisSession(
            id=new_id("ses"),
            title=(title or f"{parent.title} · 分支").strip(),
            user_id=user_id,
            source_id=parent.source_id,
            parent_session_id=parent.id,
            fork_checkpoint_id=checkpoint.id,
        )
        session.add(child)
        session.flush()
        branch = AnalysisBranch(
            id=new_id("br"),
            parent_session_id=parent.id,
            child_session_id=child.id,
            fork_checkpoint_id=checkpoint.id,
            created_by=user_id,
        )
        session.add(branch)
        accepted = None
        normalized_adjustments = {
            key: value
            for key, value in (adjustments or {}).items()
            if value not in (None, "", [], {})
        }
        if instruction and instruction.strip():
            message = AnalysisMessage(
                id=new_id("msg"),
                session_id=child.id,
                role="user",
                content=instruction.strip(),
                sequence_no=1,
                metadata_json={
                    "branchAdjustment": normalized_adjustments,
                    "branchId": branch.id,
                    "sourceSessionId": parent.id,
                    "sourceRunId": parent_run.id,
                    "forkCheckpointId": checkpoint.id,
                    "reuseArtifacts": reuse_artifacts,
                },
            )
            run = AnalysisRun(
                id=new_id("run"),
                session_id=child.id,
                trigger_message_id=message.id,
                source_id=child.source_id,
                budget_snapshot_json=default_budget(),
            )
            message.run_id = run.id
            session.add_all([message, run])
            child.updated_at = datetime.now()
            append_event(
                session,
                run,
                "run.accepted",
                {
                    "status": "queued",
                    "question": instruction.strip(),
                    "sourceId": child.source_id,
                    "branchId": branch.id,
                    "sourceRunId": parent_run.id,
                    "adjustments": normalized_adjustments,
                    "reuseArtifacts": reuse_artifacts,
                },
            )
            session.flush()
            accepted = _run_acceptance(run)
        append_event(
            session,
            parent_run,
            "branch.created",
            {
                "branchId": branch.id,
                "checkpointId": checkpoint.id,
                "parentSessionId": parent.id,
                "childSessionId": child.id,
                "adjustments": normalized_adjustments,
                "reuseArtifacts": reuse_artifacts,
            },
            step_key=checkpoint.step_key,
        )
        source = session.query(DataSource).filter_by(id=child.source_id).one()
        session.commit()
        branch_context = _branch_context(session, child)
        return {
            "branchId": branch.id,
            "checkpoint": _checkpoint_dict(checkpoint),
            "session": _session_dict(child, source.name),
            "branchContext": branch_context,
            "run": accepted,
        }


def get_run_status(run_id: str) -> str:
    with PlatformSession() as session:
        row = session.query(AnalysisRun.status).filter_by(id=run_id).first()
        if row is None:
            raise AnalysisNotFound(f"Run {run_id} 不存在")
        return row[0]


def cancel_run(run_id: str) -> dict:
    with PlatformSession() as session:
        run = session.query(AnalysisRun).filter_by(id=run_id).with_for_update().first()
        if run is None:
            raise AnalysisNotFound(f"Run {run_id} 不存在")
        if run.status in {"succeeded", "failed", "blocked", "cancelled"}:
            return {"runId": run.id, "status": run.status, "cancelled": False}
        run.status = "cancelled"
        run.error_code = "RUN_CANCELLED"
        run.finished_at = datetime.now()
        append_event(session, run, "run.cancelled", {"status": "cancelled"})
        session.commit()
        return {"runId": run.id, "status": run.status, "cancelled": True}


def list_events(run_id: str, after_seq: int = 0, limit: int = 500, *, include_admin: bool = False) -> list[dict]:
    with PlatformSession() as session:
        if session.query(AnalysisRun.id).filter_by(id=run_id).first() is None:
            raise AnalysisNotFound(f"Run {run_id} 不存在")
        query = session.query(AnalysisEvent).filter(
            AnalysisEvent.run_id == run_id,
            AnalysisEvent.seq > after_seq,
            AnalysisEvent.replayable.is_(True),
        )
        if not include_admin:
            query = query.filter(AnalysisEvent.visibility == "user")
        rows = query.order_by(AnalysisEvent.seq).limit(min(max(limit, 1), 500)).all()
        return [serialize_event(item) for item in rows]


def _session_dict(row: AnalysisSession, source_name: str) -> dict:
    return {
        "id": row.id,
        "title": row.title,
        "userId": row.user_id,
        "sourceId": row.source_id,
        "sourceName": source_name,
        "status": row.status,
        "parentSessionId": row.parent_session_id,
        "forkCheckpointId": row.fork_checkpoint_id,
        "createdAt": row.created_at.isoformat(),
        "updatedAt": row.updated_at.isoformat(),
    }


def _branch_context(session, row: AnalysisSession) -> dict | None:
    if not row.parent_session_id or not row.fork_checkpoint_id:
        return None
    checkpoint = session.query(AnalysisCheckpoint).filter_by(id=row.fork_checkpoint_id).first()
    if checkpoint is None:
        return None
    source_run = session.query(AnalysisRun).filter_by(id=checkpoint.run_id).first()
    if source_run is None:
        return None
    source_message = session.query(AnalysisMessage).filter_by(id=source_run.trigger_message_id).first()
    source_answer = (
        session.query(AnalysisMessage)
        .filter_by(run_id=source_run.id, role="assistant")
        .order_by(AnalysisMessage.sequence_no.desc())
        .first()
    )
    child_message = (
        session.query(AnalysisMessage)
        .filter_by(session_id=row.id, role="user")
        .order_by(AnalysisMessage.sequence_no)
        .first()
    )
    metadata = child_message.metadata_json if child_message else {}
    branch = session.query(AnalysisBranch).filter_by(child_session_id=row.id).first()
    return {
        "branchId": branch.id if branch else metadata.get("branchId"),
        "sourceSessionId": row.parent_session_id,
        "sourceRunId": source_run.id,
        "sourceQuestion": source_message.content if source_message else "",
        "sourceConclusion": source_answer.content if source_answer else "",
        "checkpoint": _checkpoint_dict(checkpoint),
        "instruction": child_message.content if child_message else "",
        "adjustments": metadata.get("branchAdjustment", {}),
        "reuseArtifacts": metadata.get("reuseArtifacts", "auto"),
        "createdAt": row.created_at.isoformat(),
    }


def _active_duration_ms(events: list[AnalysisEvent]) -> int | None:
    active_since = None
    total_seconds = 0.0
    has_active_interval = False
    terminal = {"run.completed", "run.failed", "run.blocked", "run.cancelled"}
    for event in events:
        if event.event_type in {"run.started", "run.resumed"}:
            if active_since is None:
                active_since = event.occurred_at
        elif event.event_type == "clarification.requested" or event.event_type in terminal:
            if active_since is not None:
                total_seconds += max(0.0, (event.occurred_at - active_since).total_seconds())
                has_active_interval = True
                active_since = None
    return round(total_seconds * 1000) if has_active_interval else None


def _message_dict(row: AnalysisMessage) -> dict:
    return {
        "id": row.id,
        "runId": row.run_id,
        "role": row.role,
        "content": row.content,
        "status": row.status,
        "sequenceNo": row.sequence_no,
        "metadata": row.metadata_json or {},
        "createdAt": row.created_at.isoformat(),
    }


def _artifact_dict(row: AnalysisArtifact) -> dict:
    return {
        "id": row.id,
        "runId": row.run_id,
        "taskId": row.task_id,
        "type": row.type,
        "schemaVersion": row.schema_version,
        "content": row.content_json,
        "summary": row.summary_json or {},
        "checksum": row.checksum,
        "createdAt": row.created_at.isoformat(),
    }


def _checkpoint_dict(row: AnalysisCheckpoint) -> dict:
    return {
        "id": row.id,
        "runId": row.run_id,
        "sessionId": row.session_id,
        "stepKey": row.step_key,
        "eventSeq": row.event_seq,
        "parentCheckpointId": row.parent_checkpoint_id,
        "summary": row.state_summary_json or {},
        "artifactRefs": row.artifact_refs_json or [],
        "rollbackable": row.rollbackable,
        "createdAt": row.created_at.isoformat(),
    }


def _interaction_dict(row: AnalysisInteraction) -> dict:
    return {
        "id": row.id,
        "runId": row.run_id,
        "taskId": row.task_id,
        "kind": row.kind,
        "status": row.status,
        "prompt": row.prompt_json or {},
        "response": row.response_json,
        "resumeNode": row.resume_node,
        "createdAt": row.created_at.isoformat(),
        "resolvedAt": row.resolved_at.isoformat() if row.resolved_at else None,
    }


def _run_summary(run: AnalysisRun) -> dict:
    return {
        "id": run.id,
        "sessionId": run.session_id,
        "sourceId": run.source_id,
        "status": run.status,
        "currentStepKey": run.current_step_key,
        "workflowVersion": run.workflow_version,
        "lastSeq": run.last_event_seq,
        "errorCode": run.error_code,
        "usage": {
            "callCount": run.llm_call_count,
            "retryCallCount": run.retry_call_count,
            "inputTokens": run.input_tokens,
            "outputTokens": run.output_tokens,
            "totalTokens": run.total_tokens,
            "retryTokens": run.retry_tokens,
            "estimatedCost": float(run.estimated_cost or 0),
            "currency": run.currency,
        },
        "startedAt": run.started_at.isoformat() if run.started_at else None,
        "finishedAt": run.finished_at.isoformat() if run.finished_at else None,
        "createdAt": run.created_at.isoformat(),
        "updatedAt": run.updated_at.isoformat(),
    }


def _run_acceptance(run: AnalysisRun) -> dict:
    return {
        "runId": run.id,
        "sessionId": run.session_id,
        "status": run.status,
        "streamUrl": f"/api/analysis/runs/{run.id}/stream",
        "snapshotUrl": f"/api/analysis/runs/{run.id}",
        "acceptedAt": run.created_at.isoformat(),
    }
