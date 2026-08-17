"""LangGraph 运行适配器：节点更新投影为 Step/Event/Artifact，并记录 LLM usage。"""

from __future__ import annotations

import hashlib
import json
import re
import time
from calendar import monthrange
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from langgraph.types import Command
from sqlalchemy import func

from app.agent import usage
from app.agent.contracts import RunStatus
from app.agent.event_store import append_event
from app.agent.graph import (
    GraphDependencies,
    _model_route_decision,
    _route_model_prompt,
    build_analysis_graph,
)
from app.agent.ids import new_id
from app.agent.intent import build_request_route
from app.config import settings
from app.db.models import (
    AnalysisArtifact,
    AnalysisCheckpoint,
    AnalysisInteraction,
    AnalysisMessage,
    AnalysisRun,
    AnalysisRunStep,
    AnalysisSession,
    AnalysisTask,
    AuditLog,
    DataSource,
    DataTable,
    LlmCallAttempt,
)
from app.db.platform import PlatformSession
from app.gateway.cost_guard import ExplainCheckResult, explain_readonly
from app.gateway.executor import ExecutionResult, execute_readonly
from app.gateway.sql_validator import validate_sql
from app.llm import provider
from app.retrieval.service import build_context_package

CHAT_REPORT_TIMEZONE = ZoneInfo("Asia/Shanghai")
CHAT_REPORT_TYPE_OPTIONS = (
    {"label": "制造运营综合周报", "value": "tpl-operations-weekly"},
    {"label": "生产周报", "value": "tpl-production-weekly"},
    {"label": "质量周报", "value": "tpl-quality-weekly"},
    {"label": "设备周报", "value": "tpl-equipment-weekly"},
    {"label": "库存周报", "value": "tpl-inventory-weekly"},
)


TERMINAL_STATUSES = {"succeeded", "failed", "blocked", "cancelled"}

NODE_META = {
    "route_request": ("intent.route", 0, "判断问题类型"),
    "intent_guard": ("intent.guard", 1, "检查只读分析边界"),
    "plan_tasks": ("intent.parse", 2, "理解并拆解业务问题"),
    "retrieve_context": ("context.retrieve", 3, "定位数据资源"),
    "propose_modeling": ("modeling.propose", 4, "生成建模方案"),
    "generate_query": ("query.generate", 4, "生成只读 SQL"),
    "validate_query": ("query.validate", 5, "安全与语义校验"),
    "execute_query": ("query.execute", 6, "执行并检查结果"),
    "prepare_retry": ("query.retry", 5, "准备自动修正"),
    "explain_result": ("result.explain", 7, "生成图表与结论"),
}

CHECKPOINT_NODES = {"plan_tasks", "retrieve_context", "validate_query", "execute_query", "explain_result"}


def _latest_call_id(run_id: str, task_id: str | None, step_key: str) -> str | None:
    scope = f"{run_id}:{task_id or 'run'}:{step_key}"
    with PlatformSession() as session:
        row = (
            session.query(LlmCallAttempt.id)
            .filter_by(step_scope=scope)
            .order_by(LlmCallAttempt.attempt.desc())
            .first()
        )
        return row[0] if row else None


def _tracked_generate(state: dict, step_key: str, system: str, user: str) -> str:
    task_id = (state.get("task") or {}).get("id")
    retry_of = _latest_call_id(state["run_id"], task_id, step_key) if state.get("retry_count", 0) else None
    call_id = usage.start_call(
        state["run_id"],
        provider="openai-compatible",
        model=settings.llm_model,
        step_key=step_key,
        task_id=task_id,
        retry_of_call_id=retry_of,
    )
    started = time.perf_counter()
    try:
        max_tokens = {
            "intent.route": 500,
            "conversation.reply": 600,
            "result.explain": 1200,
        }.get(step_key, 2400)
        response = provider.chat_completion(
            system,
            user,
            temperature=0.1,
            max_tokens=max_tokens,
        )
    except Exception as exc:
        usage.finish_call(
            call_id,
            status="failed",
            latency_ms=int((time.perf_counter() - started) * 1000),
            error_code="LLM_UNAVAILABLE",
        )
        raise
    u = response.usage
    usage.finish_call(
        call_id,
        status="succeeded",
        latency_ms=int((time.perf_counter() - started) * 1000),
        input_tokens=u.input_tokens,
        cached_input_tokens=u.cached_input_tokens,
        output_tokens=u.output_tokens,
        reasoning_tokens=u.reasoning_tokens,
        total_tokens=u.total_tokens,
        provider_request_id=response.provider_request_id,
        usage_raw=u.raw,
    )
    return response.content


def _execute_for_source(sql: str, source_id: int) -> ExecutionResult:
    with PlatformSession() as session:
        source = session.query(DataSource).filter_by(id=source_id).first()
        if source is None:
            return ExecutionResult(ok=False, error=f"数据源 {source_id} 不存在")
    return execute_readonly(sql, action="Agent 执行只读查询", source=source)


def _explain_for_source(sql: str, source_id: int) -> ExplainCheckResult:
    with PlatformSession() as session:
        source = session.query(DataSource).filter_by(id=source_id).first()
        if source is None:
            return ExplainCheckResult(
                ok=False,
                allowed=False,
                dialect="unknown",
                reason=f"数据源 {source_id} 不存在",
            )
        session.expunge(source)
    return explain_readonly(sql, source)


def default_dependencies() -> GraphDependencies:
    return GraphDependencies(
        retrieve_context=lambda question, source_id: build_context_package(question, source_id),
        generate_text=_tracked_generate,
        validate_sql=validate_sql,
        execute_sql=_execute_for_source,
        explain_sql=_explain_for_source,
    )


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _artifact(session, run: AnalysisRun, task_id: str | None, artifact_type: str, content: Any) -> str:
    safe = _json_safe(content)
    encoded = json.dumps(safe, ensure_ascii=False, sort_keys=True).encode("utf-8")
    artifact_id = new_id("art")
    session.add(
        AnalysisArtifact(
            id=artifact_id,
            run_id=run.id,
            task_id=task_id,
            type=artifact_type,
            content_json=safe,
            summary_json={"type": artifact_type},
            checksum=hashlib.sha256(encoded).hexdigest(),
        )
    )
    append_event(
        session,
        run,
        "artifact.created",
        {"artifactId": artifact_id, "type": artifact_type},
        task_id=task_id,
    )
    return artifact_id


def _persist_update(run_id: str, node: str, update: dict, state: dict) -> None:
    if node == "activate_task":
        task = state.get("task") or {}
        with PlatformSession() as session:
            run = session.query(AnalysisRun).filter_by(id=run_id).with_for_update().one()
            row = session.query(AnalysisTask).filter_by(id=task.get("id")).first()
            if row:
                row.status = "running"
                row.started_at = row.started_at or datetime.now()
            append_event(
                session,
                run,
                "task.started",
                {"status": "running", "title": task.get("title"), "order": task.get("order")},
                task_id=task.get("id"),
            )
            session.commit()
        return
    if node == "advance_task":
        completed = (state.get("task_results") or [])[-1] if state.get("task_results") else {}
        task = completed.get("task") or state.get("task") or {}
        with PlatformSession() as session:
            run = session.query(AnalysisRun).filter_by(id=run_id).with_for_update().one()
            row = session.query(AnalysisTask).filter_by(id=task.get("id")).first()
            if row:
                row.status = "succeeded"
                row.finished_at = datetime.now()
            append_event(
                session,
                run,
                "task.completed",
                {"status": "succeeded", "title": task.get("title"), "order": task.get("order")},
                task_id=task.get("id"),
            )
            session.commit()
        return

    step_key, order, label = NODE_META[node]
    task = state.get("task") or {}
    task_id = task.get("id")
    attempt = state.get("retry_count", 0) + 1 if node in {"generate_query", "validate_query", "execute_query"} else 1
    if node == "prepare_retry":
        attempt = state.get("retry_count", 0)
    with PlatformSession() as session:
        run = session.query(AnalysisRun).filter_by(id=run_id).with_for_update().one()
        expected_owner = state.get("expected_lease_owner")
        if run.status in TERMINAL_STATUSES or (
            expected_owner and run.lease_owner != expected_owner
        ):
            return
        run.current_step_key = step_key
        if node == "prepare_retry":
            run.status = "retrying"
        elif run.status == "retrying":
            run.status = "running"
        append_event(
            session,
            run,
            "step.started" if node != "prepare_retry" else "step.retrying",
            {"status": "running", "label": label, "attempt": attempt},
            task_id=task_id,
            step_key=step_key,
            attempt=attempt,
        )
        status = "succeeded"
        if update.get("blocked"):
            status = "blocked"
        elif update.get("waiting_user"):
            status = "waiting_user"
        elif update.get("error"):
            status = "failed"
        step = AnalysisRunStep(
            id=new_id("step"),
            run_id=run.id,
            task_id=task_id,
            task_scope=task_id or "run",
            step_key=step_key,
            step_order=order,
            attempt=max(1, attempt),
            status=status,
            label=label,
            detail=(update.get("error") or {}).get("message", ""),
            error_json=update.get("error"),
            started_at=datetime.now(),
            finished_at=datetime.now(),
        )
        session.add(step)

        if node == "plan_tasks":
            planned = update.get("tasks") or []
            for item in planned:
                if session.query(AnalysisTask).filter_by(id=item["id"]).first() is None:
                    session.add(
                        AnalysisTask(
                            id=item["id"],
                            run_id=run.id,
                            task_order=item["order"],
                            title=item["title"],
                            normalized_question=item["normalizedQuestion"],
                            mode=item["mode"],
                            analysis_type=item["analysisType"],
                            status="pending",
                            depends_on_json=item.get("dependsOn", []),
                            intent_json={
                                "originalQuestion": item.get("originalQuestion"),
                                "requestedChart": item.get("requestedChart"),
                                "timeConstraint": item.get("timeConstraint"),
                                "groupBy": item.get("groupBy", []),
                            },
                        )
                    )
            append_event(session, run, "tasks.planned", {"count": len(planned), "tasks": planned})

        if node == "intent_guard" and update.get("blocked"):
            business_session = session.query(AnalysisSession).filter_by(id=run.session_id).first()
            session.add(
                AuditLog(
                    user_name=business_session.user_id if business_session else "未知用户",
                    role="业务人员",
                    action="Agent 写操作意图拦截",
                    resource=f"Run {run.id}",
                    result="已拦截",
                    detail=(update.get("error") or {}).get("userSafeMessage", "检测到写操作意图"),
                )
            )

        artifact_map = {
            "retrieve_context": ("context", update.get("semantic_context")),
            "propose_modeling": ("modeling-proposal", update.get("modeling_proposal")),
            "generate_query": (
                "sql",
                {
                    "sql": update.get("sql"),
                    "metricResolution": update.get("metric_resolution"),
                },
            ),
            "validate_query": ("validation", update.get("validation")),
            "execute_query": ("result", update.get("execution")),
            "explain_result": (
                "conclusion",
                {
                    "text": update.get("conclusion"),
                    "confidence": update.get("confidence"),
                    "metricResolution": update.get("metric_resolution"),
                },
            ),
        }
        if node in artifact_map and artifact_map[node][1] is not None:
            artifact_id = _artifact(session, run, task_id, *artifact_map[node])
            step.output_refs_json = [artifact_id]

        event_type = {
            "succeeded": "modeling.proposed" if node == "propose_modeling" else "step.completed",
            "failed": "step.failed",
            "blocked": "step.blocked",
            "waiting_user": "clarification.requested",
        }[status]
        append_event(
            session,
            run,
            event_type,
            {"status": status, "label": label, "attempt": attempt, "error": update.get("error")},
            task_id=task_id,
            step_key=step_key,
            attempt=attempt,
        )
        session.commit()


def _interrupt_payload(raw: Any) -> dict[str, Any]:
    interrupts = raw if isinstance(raw, (list, tuple)) else [raw]
    if not interrupts:
        return {}
    value = getattr(interrupts[0], "value", interrupts[0])
    return _json_safe(value) if isinstance(value, dict) else {"question": str(value)}


def _persist_interrupt(run_id: str, payload: dict[str, Any], state: dict) -> str:
    task_id = payload.get("taskId") or (state.get("task") or {}).get("id")
    resume_node = payload.get("resumeNode") or "context.retrieve"
    with PlatformSession() as session:
        run = session.query(AnalysisRun).filter_by(id=run_id).with_for_update().one()
        existing = session.query(AnalysisInteraction).filter_by(run_id=run_id, status="pending").first()
        if existing is None:
            existing = AnalysisInteraction(
                id=new_id("int"),
                run_id=run.id,
                task_id=task_id,
                prompt_json=payload,
                resume_node=resume_node,
            )
            session.add(existing)
        run.status = "waiting_user"
        run.current_step_key = resume_node
        run.lease_owner = None
        run.lease_expires_at = None
        task = session.query(AnalysisTask).filter_by(id=task_id).first() if task_id else None
        if task:
            task.status = "waiting"
        append_event(
            session,
            run,
            "clarification.requested",
            {"interactionId": existing.id, **payload},
            task_id=task_id,
            step_key=resume_node,
        )
        session.commit()
        return existing.id


def _project_checkpoint(run_id: str, node: str, state: dict, graph, config: dict) -> None:
    if node not in CHECKPOINT_NODES:
        return
    snapshot = graph.get_state(config)
    checkpoint_config = getattr(snapshot, "config", None) or {}
    configurable = checkpoint_config.get("configurable", {})
    checkpoint_id = configurable.get("checkpoint_id")
    if not checkpoint_id:
        return
    with PlatformSession() as session:
        if session.query(AnalysisCheckpoint.id).filter_by(
            run_id=run_id, langgraph_checkpoint_id=str(checkpoint_id)
        ).first():
            return
        run = session.query(AnalysisRun).filter_by(id=run_id).with_for_update().one()
        parent = (
            session.query(AnalysisCheckpoint)
            .filter_by(run_id=run_id)
            .order_by(AnalysisCheckpoint.created_at.desc())
            .first()
        )
        task = state.get("task") or {}
        artifacts = [
            row[0]
            for row in session.query(AnalysisArtifact.id)
            .filter_by(run_id=run_id, task_id=task.get("id"))
            .order_by(AnalysisArtifact.created_at)
            .all()
        ]
        summary = {
            "node": node,
            "taskId": task.get("id"),
            "taskTitle": task.get("title"),
            "taskOrder": task.get("order"),
            "completedTaskCount": len(state.get("task_results", [])),
            "totalTaskCount": len(state.get("tasks", [])),
            "hasSql": bool(state.get("sql")),
            "hasResult": bool(state.get("execution")),
        }
        checksum = hashlib.sha256(
            json.dumps(summary, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        checkpoint = AnalysisCheckpoint(
            id=new_id("ckpt"),
            session_id=run.session_id,
            run_id=run.id,
            step_key=NODE_META[node][0],
            event_seq=run.last_event_seq,
            parent_checkpoint_id=parent.id if parent else None,
            workflow_version=run.workflow_version,
            langgraph_thread_id=str(configurable.get("thread_id", run.session_id)),
            langgraph_checkpoint_ns=str(configurable.get("checkpoint_ns", "")),
            langgraph_checkpoint_id=str(checkpoint_id),
            state_summary_json=summary,
            artifact_refs_json=artifacts,
            rollbackable=node in {"plan_tasks", "retrieve_context", "validate_query", "execute_query", "explain_result"},
            checksum=checksum,
        )
        session.add(checkpoint)
        append_event(
            session,
            run,
            "checkpoint.created",
            {
                "checkpointId": checkpoint.id,
                "stepKey": checkpoint.step_key,
                "rollbackable": checkpoint.rollbackable,
                "summary": summary,
            },
            task_id=task.get("id"),
            step_key=checkpoint.step_key,
        )
        session.commit()


def _finish_run(run_id: str, state: dict) -> None:
    with PlatformSession() as session:
        run = session.query(AnalysisRun).filter_by(id=run_id).with_for_update().one()
        expected_owner = state.get("expected_lease_owner")
        if run.status in TERMINAL_STATUSES or (
            expected_owner and run.lease_owner != expected_owner
        ):
            return
        if state.get("blocked"):
            run.status = "blocked"
            run.error_code = (state.get("error") or {}).get("code", "WRITE_INTENT_BLOCKED")
            event_type = "run.blocked"
        elif state.get("error"):
            run.status = "failed"
            run.error_code = state["error"].get("code", "ANALYSIS_FAILED")
            event_type = "run.failed"
        elif state.get("modeling_proposal"):
            # 建模提案待确认：非终态，用户可在会话中确认或调整后创建训练任务
            run.status = RunStatus.MODELING_PROPOSAL.value
            run.error_code = None
            event_type = "run.modeling_proposed"
            proposal = state["modeling_proposal"]
            sequence_no = (
                session.query(func.coalesce(func.max(AnalysisMessage.sequence_no), 0))
                .filter_by(session_id=run.session_id)
                .scalar()
                + 1
            )
            content = (
                f"已生成建模方案：{proposal.get('dataset')}·{proposal.get('taskTypeLabel')}模型，"
                f"预测目标「{proposal.get('targetLabel') or proposal.get('target')}」。"
                "确认后将在智能建模中创建任务，训练前可继续调整特征与候选算法。"
            )
            session.add(
                AnalysisMessage(
                    id=new_id("msg"),
                    session_id=run.session_id,
                    run_id=run.id,
                    role="assistant",
                    content=content,
                    sequence_no=sequence_no,
                )
            )
        else:
            run.status = "succeeded"
            run.error_code = None
            event_type = "run.completed"
            sequence_no = (
                session.query(func.coalesce(func.max(AnalysisMessage.sequence_no), 0))
                .filter_by(session_id=run.session_id)
                .scalar()
                + 1
            )
            conclusions = [item.get("conclusion", "") for item in state.get("task_results", []) if item.get("conclusion")]
            analysis_content = "\n\n".join(
                f"{index + 1}. {text}" if len(conclusions) > 1 else text
                for index, text in enumerate(conclusions)
            ) or state.get("conclusion", "")
            content = "\n\n".join(
                part for part in (state.get("direct_response", ""), analysis_content) if part
            ) or "分析完成"
            session.add(
                AnalysisMessage(
                    id=new_id("msg"),
                    session_id=run.session_id,
                    run_id=run.id,
                    role="assistant",
                    content=content,
                    sequence_no=sequence_no,
                    metadata_json={"report": state["report_reference"]} if state.get("report_reference") else {},
                )
            )
        if run.status != "succeeded":
            for task in session.query(AnalysisTask).filter_by(run_id=run.id).all():
                if task.status in {"pending", "running", "waiting"}:
                    task.status = run.status
                    task.finished_at = datetime.now()
        run.finished_at = datetime.now()
        run.current_step_key = None
        run.lease_owner = None
        run.lease_expires_at = None
        append_event(
            session,
            run,
            event_type,
            {"status": run.status, "error": state.get("error")},
        )
        session.commit()
    # 报告 Run 完成后立即冻结章节产物。普通问析 Run 没有关联报告时该调用为空操作。
    from app.reports.service import sync_report_from_run

    sync_report_from_run(run_id, session_factory=PlatformSession)


def _mark_unexpected_failure(run_id: str, exc: Exception) -> None:
    with PlatformSession() as session:
        run = session.query(AnalysisRun).filter_by(id=run_id).with_for_update().first()
        if run is None or run.status in TERMINAL_STATUSES:
            return
        run.status = "failed"
        run.error_code = "AGENT_RUNTIME_FAILED"
        run.finished_at = datetime.now()
        run.lease_owner = None
        run.lease_expires_at = None
        append_event(
            session,
            run,
            "run.failed",
            {
                "status": "failed",
                "error": {
                    "code": "AGENT_RUNTIME_FAILED",
                    "userSafeMessage": "分析运行出现异常，请使用错误编号联系管理员。",
                    "debugType": type(exc).__name__,
                },
            },
        )
        session.commit()


def execute_inline_conversation(run_id: str, direct_response: str | None = None) -> dict:
    """Complete a non-data turn without entering retrieval/SQL worker stages."""

    with PlatformSession() as session:
        run = session.query(AnalysisRun).filter_by(id=run_id).with_for_update().one()
        if run.status in TERMINAL_STATUSES:
            return {"runId": run.id, "status": run.status}
        message = session.query(AnalysisMessage).filter_by(id=run.trigger_message_id).one()
        history_rows = (
            session.query(AnalysisMessage)
            .filter(
                AnalysisMessage.session_id == run.session_id,
                AnalysisMessage.sequence_no < message.sequence_no,
            )
            .order_by(AnalysisMessage.sequence_no.desc())
            .limit(6)
            .all()
        )
        context = [
            {"role": item.role, "content": item.content}
            for item in reversed(history_rows)
        ]
        run.status = "running"
        run.started_at = run.started_at or datetime.now()
        session.commit()
        state = {
            "session_id": run.session_id,
            "run_id": run.id,
            "source_id": run.source_id,
            "user_question": message.content,
            "conversation_context": context,
            "retry_count": 0,
            "attempted_sqls": [],
            "expected_lease_owner": "inline-conversation",
        }

    response = direct_response
    if response is None:
        system = (
            "你是 ForgeSight，面向制造企业数据底座的智能问析 Agent。"
            "你可以自然、友善、简洁地正常对话；你的核心职责是帮助用户理解生产、质量、设备和库存数据，"
            "在企业数据场景中只做已授权的只读分析，绝不假装查询过数据，也不编造业务事实。"
            "当前问题不需要查询数据库，请直接回应，不输出 JSON，不复述规则；通常控制在 180 字以内。"
        )
        user = json.dumps(
            {"recentConversation": context[-4:], "currentMessage": state["user_question"]},
            ensure_ascii=False,
        )
        try:
            response = _tracked_generate(state, "conversation.reply", system, user).strip()[:2000]
        except Exception:  # noqa: BLE001 - keep ordinary dialogue available during provider outages
            response = (
                "我是 ForgeSight，主要负责生产、质量、设备和库存数据的只读问析。"
                "当前对话模型暂时不可用，你仍可以直接告诉我想分析的业务问题。"
            )

    update = {
        "intent_kind": "conversation",
        "analysis_question": "",
        "direct_response": response,
        "tasks": [],
        "blocked": False,
        "error": None,
    }
    state.update(update)
    _persist_update(run_id, "route_request", update, state)
    _finish_run(run_id, state)
    return {"runId": run_id, "status": "succeeded"}


_CHAT_REPORT_PERIOD_RE = re.compile(
    r"(?:最近|近)\s*(?:\d+|一|两|三|四|五|六|七|八|九|十|半|一个)\s*(?:天|日|周|星期|礼拜|个月|月)"
    r"|(?:最后|最新|过去|上一|上个)\s*(?:一|1)?\s*(?:个)?\s*(?:有数据的)?\s*(?:周|星期|礼拜)"
    r"|(?:本周|这周|这个星期|这个礼拜|这个月|本月|这个季度|本季度|今年|今天|昨天|上周|上月)"
    r"|20\d{2}\s*(?:[-年/])\s*\d{1,2}(?:\s*(?:[-月/])\s*\d{1,2})?"
)
_CHAT_REPORT_FOCUS_TERMS = ("生产", "产量", "产线", "质量", "良率", "缺陷", "不良", "设备", "停机", "库存", "物料", "经营", "运营", "综合")
_CHAT_REPORT_DEFAULT_SCOPE_TERMS = ("周报", "月报", "综合", "经营", "运营", "业务总结", "工作总结")


def report_brief_missing_details(brief: str) -> list[str]:
    """Return the report inputs that cannot safely be assumed from a chat turn."""

    normalized = str(brief or "")
    missing = []
    has_period = bool(_CHAT_REPORT_PERIOD_RE.search(normalized))
    if not has_period:
        missing.append("统计时间")
    has_focus = any(term in normalized for term in _CHAT_REPORT_FOCUS_TERMS)
    # A generic weekly/monthly report with an explicit period has a safe,
    # auditable default: the operations summary template. Do not apply that
    # default to a bare “给我一份周报”, which still needs a time range.
    has_default_scope = has_period and any(
        term in normalized for term in _CHAT_REPORT_DEFAULT_SCOPE_TERMS
    )
    if not has_focus and not has_default_scope:
        missing.append("关注内容")
    return missing


def _report_brief_question(brief: str) -> str:
    missing = report_brief_missing_details(brief)
    if len(missing) == 2:
        return "可以。为了生成准确的报告，请告诉我统计时间和关注内容（生产、质量、设备、库存或经营）。例如：‘生成最近 30 天的质量与设备专题报告’。"
    if missing == ["统计时间"]:
        return "已了解报告关注内容。请再告诉我统计时间，例如‘最近 30 天’、‘上周’或‘2026 年 7 月’。"
    return "已了解统计时间。请再告诉我希望关注的内容，例如生产、质量、设备、库存或经营。"


def execute_inline_report_clarification(run_id: str, report_brief: str | None = None) -> dict:
    """Pause a chat report request until its missing business details are supplied."""

    with PlatformSession() as session:
        run = session.query(AnalysisRun).filter_by(id=run_id).with_for_update().one()
        if run.status in TERMINAL_STATUSES:
            return {"runId": run.id, "status": run.status}
        message = session.query(AnalysisMessage).filter_by(id=run.trigger_message_id).one()
        brief = (report_brief or message.content).strip()
        prompt = {
            "kind": "clarification",
            "question": _report_brief_question(brief),
            "options": [],
            "allowCustom": True,
            "inputPlaceholder": "例如：最近 30 天的质量与设备专题报告",
            "reportBrief": brief,
        }
        existing = session.query(AnalysisInteraction).filter_by(
            run_id=run.id, status="pending"
        ).first()
        if existing is None:
            existing = AnalysisInteraction(
                id=new_id("int"),
                run_id=run.id,
                kind="clarification",
                status="pending",
                prompt_json=prompt,
                resume_node="report.create",
            )
            session.add(existing)
        run.status = "waiting_user"
        run.current_step_key = "report.create"
        run.lease_owner = None
        run.lease_expires_at = None
        append_event(
            session,
            run,
            "clarification.requested",
            {"interactionId": existing.id, **prompt},
            step_key="report.create",
        )
        session.commit()
        return {"runId": run.id, "status": run.status, "interactionId": existing.id}


def _chat_report_template(question: str) -> str:
    normalized = str(question or "")
    if "质量" in normalized:
        return "tpl-quality-weekly"
    if "设备" in normalized or "停机" in normalized:
        return "tpl-equipment-weekly"
    if "库存" in normalized:
        return "tpl-inventory-weekly"
    if "生产" in normalized or "产量" in normalized or "产线" in normalized:
        return "tpl-production-weekly"
    return "tpl-operations-weekly"


def _latest_report_data_date(source_id: int) -> date | None:
    """Use catalogued freshness so a local/demo data source never yields an empty 'current week' report."""

    with PlatformSession() as session:
        values = [
            row.data_updated for row in session.query(DataTable)
            .filter_by(source_id=source_id).all() if row.data_updated
        ]
    parsed = []
    for value in values:
        try:
            parsed.append(datetime.fromisoformat(str(value)[:10]).date())
        except ValueError:
            continue
    return max(parsed) if parsed else None


def _chat_report_period(question: str, source_id: int | None = None) -> tuple[datetime, datetime]:
    now = datetime.now(CHAT_REPORT_TIMEZONE).replace(tzinfo=None)
    today = _latest_report_data_date(source_id) if source_id else None
    today = today or now.date()
    latest_day_end = datetime.combine(today, datetime.max.time())
    month_match = re.search(r"(20\d{2})\s*(?:年|[-/])\s*(\d{1,2})\s*(?:月)?", question)
    if month_match:
        year, month = (int(value) for value in month_match.groups())
        if 1 <= month <= 12:
            start = date(year, month, 1)
            end = date(year, month, monthrange(year, month)[1])
            return datetime.combine(start, datetime.min.time()), datetime.combine(end, datetime.max.time())
    if re.search(r"(?:上周|上一周|上个星期|上个礼拜)", question):
        current_monday = today - timedelta(days=today.weekday())
        start = current_monday - timedelta(days=7)
        end = current_monday - timedelta(days=1)
        return datetime.combine(start, datetime.min.time()), datetime.combine(end, datetime.max.time())
    if re.search(r"(?:本周|这周|这个星期|这个礼拜)", question):
        start = today - timedelta(days=today.weekday())
        return datetime.combine(start, datetime.min.time()), latest_day_end
    if "本月" in question:
        start = today.replace(day=1)
        return datetime.combine(start, datetime.min.time()), latest_day_end
    match = re.search(r"最近\s*(\d+)\s*(?:天|日)", question)
    days = int(match.group(1)) if match else 7
    days = min(max(days, 1), 366)
    start = today - timedelta(days=days - 1)
    end = latest_day_end
    return datetime.combine(start, datetime.min.time()), end


def execute_inline_report_request(run_id: str, template_id: str | None = None, report_brief: str | None = None) -> dict:
    """Create a real report from a direct chat request and keep the chat run inline."""

    with PlatformSession() as session:
        run = session.query(AnalysisRun).filter_by(id=run_id).with_for_update().one()
        if run.status in TERMINAL_STATUSES:
            return {"runId": run.id, "status": run.status}
        message = session.query(AnalysisMessage).filter_by(id=run.trigger_message_id).one()
        business_session = session.query(AnalysisSession).filter_by(id=run.session_id).one()
        state = {
            "session_id": run.session_id,
            "run_id": run.id,
            "source_id": run.source_id,
            "user_question": message.content,
            "conversation_context": [],
            "retry_count": 0,
            "attempted_sqls": [],
            "expected_lease_owner": "inline-conversation",
        }
        user_id = business_session.user_id
        report_question = report_brief or message.content
        session.commit()

    from app.reports import service as report_service
    from app.reports.contracts import ReportCreate

    period_start, period_end = _chat_report_period(report_question, state["source_id"])
    template_id = template_id or _chat_report_template(report_question)
    try:
        report = report_service.create_report(
            ReportCreate(
                templateId=template_id,
                sourceId=state["source_id"],
                periodStart=period_start,
                periodEnd=period_end,
                ownerId=user_id,
                # 对话发起的周报默认在生成完成后投递到用户已绑定邮箱。
                emailEnabled=True,
            )
        )
        report_id = report.get("id", "")
        title = report.get("title") or "新报告"
        response = (
            f"已创建报告《{title}》，正在后台生成。"
            f"报告 ID：{report_id}。完成后可在“报告中心”查看并下载 PDF。"
        )
    except report_service.ReportConflict as exc:
        response = f"报告创建未完成：{exc}。请先在“报告中心”确认模板覆盖率或调整报告范围。"
    except Exception:  # noqa: BLE001 - keep the chat run readable while preserving the report error in logs
        response = "报告任务创建失败，请稍后重试；你也可以打开“报告中心”手动选择模板和统计周期。"

    update = {
        "intent_kind": "report",
        "analysis_question": "",
        "direct_response": response,
        "report_reference": ({
            "id": report_id,
            "title": title,
            "status": report.get("status", "queued"),
            "emailEnabled": True,
        } if "report_id" in locals() and report_id else None),
        "tasks": [],
        "blocked": False,
        "error": None,
    }
    state.update(update)
    _persist_update(run_id, "route_request", update, state)
    _finish_run(run_id, state)
    return {"runId": run_id, "status": "succeeded"}


def execute_inline_route(run_id: str) -> dict:
    """Use one small call to route ambiguous text, then finish chat or queue analysis."""

    with PlatformSession() as session:
        run = session.query(AnalysisRun).filter_by(id=run_id).with_for_update().one()
        if run.status in TERMINAL_STATUSES:
            return {"runId": run.id, "status": run.status}
        message = session.query(AnalysisMessage).filter_by(id=run.trigger_message_id).one()
        history_rows = (
            session.query(AnalysisMessage)
            .filter(
                AnalysisMessage.session_id == run.session_id,
                AnalysisMessage.sequence_no < message.sequence_no,
            )
            .order_by(AnalysisMessage.sequence_no.desc())
            .limit(6)
            .all()
        )
        context = [{"role": item.role, "content": item.content} for item in reversed(history_rows)]
        state = {
            "session_id": run.session_id,
            "run_id": run.id,
            "source_id": run.source_id,
            "user_question": message.content,
            "conversation_context": context,
            "retry_count": 0,
            "attempted_sqls": [],
            "expected_lease_owner": "inline-conversation",
        }

    local_route = build_request_route(state["user_question"], conversation_context=context)
    unknown = local_route["unknownSegments"]
    system, user = _route_model_prompt(unknown, context)
    try:
        labels, responses = _model_route_decision(
            _tracked_generate(state, "intent.route", system, user),
            unknown,
        )
    except Exception:  # noqa: BLE001 - let the worker retry ambiguous routing later
        labels, responses = {}, {}

    routed = build_request_route(
        state["user_question"],
        labels,
        context,
    )
    # The router may recognize a report request from its meaning rather than
    # from fixed report keywords (for example, “把最近一周的经营情况整理成
    # 一份总结”).  Reuse the same semantic completeness check as explicit
    # report requests: create directly when time/focus are clear, and ask only
    # for the missing detail when an assumption would be unsafe.
    if routed.get("reportSegments") and not routed.get("analysisQuestion"):
        report_brief = "；".join(item["text"] for item in routed["reportSegments"])
        if report_brief_missing_details(report_brief):
            return execute_inline_report_clarification(run_id, report_brief=report_brief)
        return execute_inline_report_request(run_id, report_brief=report_brief)
    if not labels or routed["kind"] in {"analysis", "mixed"}:
        with PlatformSession() as session:
            run = session.query(AnalysisRun).filter_by(id=run_id).with_for_update().one()
            if run.status in TERMINAL_STATUSES or run.lease_owner != "inline-conversation":
                return {"runId": run.id, "status": run.status}
            message = session.query(AnalysisMessage).filter_by(id=run.trigger_message_id).one()
            metadata = dict(message.metadata_json or {})
            if labels:
                metadata["routeLabels"] = {str(index): kind for index, kind in labels.items()}
                metadata["routeResponses"] = {
                    str(index): response for index, response in responses.items()
                }
            message.metadata_json = metadata
            run.status = "queued"
            run.started_at = None
            run.current_step_key = None
            run.lease_owner = None
            run.lease_expires_at = None
            append_event(
                session,
                run,
                "run.routed",
                {"status": "queued", "kind": routed["kind"] if labels else "unknown"},
                step_key="intent.route",
            )
            session.commit()
        return {"runId": run_id, "status": "queued"}

    direct_response = "\n\n".join(
        responses[item["index"]]
        for item in routed["segments"]
        if item["kind"] == "conversation" and item["index"] in responses
    ) or routed["conversationResponse"]
    update = {
        "intent_kind": "conversation",
        "analysis_question": "",
        "direct_response": direct_response,
        "tasks": [],
        "blocked": False,
        "error": None,
    }
    state.update(update)
    _persist_update(run_id, "route_request", update, state)
    _finish_run(run_id, state)
    return {"runId": run_id, "status": "succeeded"}


def execute_run(run_id: str, checkpointer=None, dependencies: GraphDependencies | None = None) -> dict:
    """执行一个已入队 Run。Worker 和测试均通过此入口，避免两套执行逻辑。"""
    with PlatformSession() as session:
        run = session.query(AnalysisRun).filter_by(id=run_id).with_for_update().first()
        if run is None:
            raise KeyError(f"Run {run_id} 不存在")
        if run.status in TERMINAL_STATUSES:
            return {"runId": run.id, "status": run.status}
        message = session.query(AnalysisMessage).filter_by(id=run.trigger_message_id).one()
        history_rows = (
            session.query(AnalysisMessage)
            .filter(
                AnalysisMessage.session_id == run.session_id,
                AnalysisMessage.sequence_no < message.sequence_no,
            )
            .order_by(AnalysisMessage.sequence_no.desc())
            .limit(6)
            .all()
        )
        conversation_context = [
            {"role": item.role, "content": item.content}
            for item in reversed(history_rows)
        ]
        business_session = session.query(AnalysisSession).filter_by(id=run.session_id).first()
        if business_session and business_session.fork_checkpoint_id:
            fork = session.query(AnalysisCheckpoint).filter_by(id=business_session.fork_checkpoint_id).first()
            if fork:
                source_run = session.query(AnalysisRun).filter_by(id=fork.run_id).first()
                source_message = (
                    session.query(AnalysisMessage).filter_by(id=source_run.trigger_message_id).first()
                    if source_run
                    else None
                )
                branch_artifacts = (
                    session.query(AnalysisArtifact)
                    .filter(AnalysisArtifact.id.in_(fork.artifact_refs_json or []))
                    .all()
                )
                inherited = {
                    "originalQuestion": source_message.content if source_message else "",
                    "checkpoint": fork.state_summary_json or {},
                    "adjustments": (message.metadata_json or {}).get("branchAdjustment", {}),
                    "reuseArtifacts": (message.metadata_json or {}).get("reuseArtifacts", "auto"),
                    "artifacts": [
                        {
                            "type": item.type,
                            "content": item.content_json if item.type in {"context", "sql", "validation"} else item.summary_json,
                        }
                        for item in branch_artifacts
                    ],
                }
                conversation_context.append(
                    {
                        "role": "system",
                        "content": "分析分支起点：" + json.dumps(inherited, ensure_ascii=False, default=str),
                    }
                )
        resolved_interaction = (
            session.query(AnalysisInteraction)
            .filter_by(run_id=run_id, status="resolved")
            .order_by(AnalysisInteraction.resolved_at.desc())
            .first()
        )
        is_resume = resolved_interaction is not None
        run.status = "running"
        run.started_at = run.started_at or datetime.now()
        append_event(
            session,
            run,
            "run.resumed" if is_resume else "run.started",
            {"status": "running", "interactionId": resolved_interaction.id if resolved_interaction else None},
        )
        initial = {
            "session_id": run.session_id,
            "run_id": run.id,
            "source_id": run.source_id,
            "user_question": message.content,
            "conversation_context": conversation_context,
            "preclassified_route_labels": {
                int(index): kind
                for index, kind in (message.metadata_json or {}).get("routeLabels", {}).items()
            },
            "preclassified_route_responses": {
                int(index): response
                for index, response in (message.metadata_json or {}).get("routeResponses", {}).items()
            },
            "retry_count": 0,
            "attempted_sqls": [],
            "tasks": [],
            "task_index": 0,
            "task_results": [],
            "blocked": False,
            "waiting_user": False,
            "error": None,
        }
        session.commit()

    graph = build_analysis_graph(dependencies or default_dependencies(), checkpointer=checkpointer)
    # 一个 Run 对应一个顶层 LangGraph thread。checkpoint_ns 只用于子图命名空间，
    # 不能拿业务 Run ID 充当 namespace，否则 get_state/time-travel 会被识别成不存在的子图。
    config = {"configurable": {"thread_id": run_id}}
    snapshot = graph.get_state(config)
    has_checkpoint = bool(snapshot.values)
    if is_resume:
        state = dict(snapshot.values or initial)
        stream_input = Command(resume=resolved_interaction.response_json or {})
    elif has_checkpoint:
        state = dict(snapshot.values)
        with PlatformSession() as session:
            recovering = session.query(AnalysisRun).filter_by(id=run_id).with_for_update().one()
            append_event(
                session,
                recovering,
                "run.recovered",
                {
                    "status": "running",
                    "checkpointAvailable": True,
                    "nextNodes": list(snapshot.next or []),
                },
            )
            session.commit()
        if not snapshot.next:
            _finish_run(run_id, state)
            with PlatformSession() as session:
                recovered_status = session.query(AnalysisRun.status).filter_by(id=run_id).scalar()
            return {"runId": run_id, "status": recovered_status, "recovered": True}
        # Passing None continues from the durable LangGraph checkpoint instead of
        # merging a fresh input and replaying already completed nodes.
        stream_input = None
    else:
        state = dict(initial)
        stream_input = initial
    try:
        for item in graph.stream(stream_input, config=config, stream_mode="updates"):
            for node, update in item.items():
                if node == "__interrupt__":
                    interaction_id = _persist_interrupt(run_id, _interrupt_payload(update), state)
                    return {
                        "runId": run_id,
                        "status": "waiting_user",
                        "interactionId": interaction_id,
                        "state": state,
                    }
                if not isinstance(update, dict):
                    continue
                state.update(update)
                _persist_update(run_id, node, update, state)
                _project_checkpoint(run_id, node, state, graph, config)
                with PlatformSession() as session:
                    current_status = session.query(AnalysisRun.status).filter_by(id=run_id).scalar()
                if current_status == "cancelled":
                    return {"runId": run_id, "status": "cancelled", "state": state}
        if is_resume:
            with PlatformSession() as session:
                interaction = session.query(AnalysisInteraction).filter_by(id=resolved_interaction.id).first()
                if interaction:
                    interaction.status = "consumed"
                session.commit()
        # A queued run can reach the worker when the first inline router call
        # was unavailable.  If the worker's router then recognizes the same
        # natural-language report intent, keep the UX consistent with the
        # inline path and ask for a template instead of completing an empty
        # “分析完成” run.
        if state.get("intent_kind") == "report" and not state.get("analysis_question"):
            report_brief = state.get("user_question") or ""
            if report_brief_missing_details(report_brief):
                return execute_inline_report_clarification(run_id, report_brief=report_brief)
            return execute_inline_report_request(run_id, report_brief=report_brief)
        _finish_run(run_id, state)
    except Exception as exc:
        _mark_unexpected_failure(run_id, exc)
        raise
    return {"runId": run_id, "status": state.get("error") and "failed" or "succeeded", "state": state}
