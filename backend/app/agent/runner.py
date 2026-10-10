"""LangGraph 运行适配器：节点更新投影为 Step/Event/Artifact，并记录 LLM usage。"""

from __future__ import annotations

import hashlib
import json
import re
import time
from contextvars import ContextVar
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from langgraph.types import Command
from sqlalchemy import func

from app.agent import usage
from app.agent.contracts import RunStatus
from app.agent.event_store import append_event
from app.agent.graph import (
    GraphDependencies,
    WRITE_TERMS,
    _model_route_decision,
    _route_model_prompt,
    build_analysis_graph,
)
from app.agent.ids import new_id
from app.agent.intent import (
    _has_analysis_verb,
    _has_business_analysis_signal,
    _is_turn_switch,
    _requested_chart,
    build_request_route,
    should_downgrade_to_conversation,
)
from app.agent.dialogue import has_previous_result as _has_previous_result
from app.agent.llm_intent import resolve_intent
from app.agent.system_utility import build_system_utility_response, is_system_utility_request
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
    LlmCallAttempt,
)
from app.db.platform import PlatformSession
from app.gateway.cost_guard import ExplainCheckResult, explain_readonly
from app.gateway.executor import ExecutionResult, execute_readonly
from app.gateway.sql_validator import validate_sql
from app.llm import provider
from app.llm.provider import LLMError
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

# Monotonic timestamps are process-local and must never be trusted after a
# durable checkpoint is resumed.  The active invocation context overrides the
# serialized state while graph-only unit tests can still use state values.
_ACTIVE_RUNTIME: ContextVar[tuple[str, float, float] | None] = ContextVar(
    "agent_active_runtime",
    default=None,
)

NODE_META = {
    "route_request": ("intent.route", 0, "判断问题类型"),
    "system_utility": ("system.utility", 1, "读取系统与数据范围"),
    "intent_guard": ("intent.guard", 1, "检查只读分析边界"),
    "decide_planning_path": ("planner.route", 2, "选择快速路径或规划路径"),
    "build_business_plan": ("planner.build", 3, "生成受限业务计划"),
    "validate_business_plan": ("planner.validate", 4, "校验计划能力、依赖与预算"),
    "select_business_step": ("planner.select", 5, "选择下一项业务能力"),
    "authorize_capability": ("capability.authorize", 6, "校验能力权限与副作用"),
    "invoke_business_capability": ("capability.invoke", 7, "执行业务能力"),
    "record_business_observation": ("observation.record", 8, "记录结构化观察"),
    "record_delegated_observation": ("observation.record", 8, "记录受治理子图观察"),
    "evaluate_business_progress": ("planner.evaluate", 9, "判断继续、调整或结束"),
    "revise_business_plan": ("planner.revise", 10, "有限调整业务计划"),
    "finalize_business_plan": ("planner.finalize", 11, "汇总计划结果与限制"),
    "plan_tasks": ("intent.parse", 2, "理解并拆解业务问题"),
    "reuse_presentation": ("result.reuse_presentation", 7, "复用结果并调整展示"),
    "retrieve_memory": ("memory.retrieve", 3, "召回相关记忆"),
    "retrieve_skill": ("skill.retrieve", 3, "匹配分析技能"),
    "retrieve_context": ("context.retrieve", 4, "定位数据资源"),
    "propose_modeling": ("modeling.propose", 4, "生成建模方案"),
    "generate_query": ("query.generate", 4, "生成只读 SQL"),
    "validate_query": ("query.validate", 5, "安全与语义校验"),
    "execute_query": ("query.execute", 6, "执行并检查结果"),
    "prepare_retry": ("query.retry", 5, "准备自动修正"),
    "explain_result": ("result.explain", 7, "生成图表与结论"),
}

CHECKPOINT_NODES = {
    "build_business_plan", "validate_business_plan", "record_business_observation",
    "record_delegated_observation", "revise_business_plan", "finalize_business_plan",
    "plan_tasks", "reuse_presentation", "retrieve_context", "validate_query", "execute_query", "explain_result",
}
PLANNER_LOOP_NODES = {
    "select_business_step", "authorize_capability", "invoke_business_capability",
    "record_business_observation", "record_delegated_observation", "evaluate_business_progress",
}


def _extract_previous_task(session: Any, run: Any) -> dict[str, Any] | None:
    """从同一会话里最近一个已结束的 run 提取任务意图结构（groupBy/time/chart），
    用作本轮多轮追问的继承槽位。多轮继承只在同 session 范围内生效，避免跨租户/跨会话串味。"""
    try:
        prev_run = (
            session.query(AnalysisRun)
            .filter(
                AnalysisRun.session_id == run.session_id,
                AnalysisRun.id != run.id,
                AnalysisRun.status.in_(["succeeded", "completed"]),
            )
            .order_by(AnalysisRun.created_at.desc(), AnalysisRun.id.desc())
            .first()
        )
        if not prev_run:
            return None
        previous_message = session.query(AnalysisMessage).filter_by(run_id=prev_run.id, role="assistant").order_by(AnalysisMessage.sequence_no.desc()).first()
        snapshot = (previous_message.metadata_json or {}).get("analysisContext") if previous_message else None
        if snapshot and snapshot.get("sourceId") == run.source_id:
            return {**(snapshot.get("task") or {}), "analysisContext": snapshot}
        tasks = (
            session.query(AnalysisTask)
            .filter_by(run_id=prev_run.id)
            .order_by(AnalysisTask.task_order.asc())
            .all()
        )
        if not tasks:
            return None
        for row in tasks:
            intent = row.intent_json or {}
            if intent.get("groupBy") or intent.get("timeConstraint") or intent.get("requestedChart") or intent.get("metricHint"):
                return {
                    "groupBy": list(intent.get("groupBy") or []),
                    "timeConstraint": intent.get("timeConstraint"),
                    "requestedChart": intent.get("requestedChart"),
                    "metricHint": intent.get("metricHint") or intent.get("metric"),
                    "title": row.title or "",
                    "normalizedQuestion": row.normalized_question or "",
                }
        row = tasks[0]
        intent = row.intent_json or {}
        return {
            "groupBy": list(intent.get("groupBy") or []),
            "timeConstraint": intent.get("timeConstraint"),
            "requestedChart": intent.get("requestedChart"),
            "metricHint": intent.get("metricHint"),
            "title": row.title or "",
            "normalizedQuestion": row.normalized_question or "",
        }
    except Exception:
        return None


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


def _llm_error_is_transient(exc: Exception) -> bool:
    """瞬时 LLM 失败（空内容/网络/限流）才在步骤内重试；配置、鉴权错误不重试放大成本。"""
    msg = str(exc)
    if "未配置" in msg or "API_KEY" in msg or "api_key" in msg:
        return False
    if any(tag in msg for tag in ("AuthenticationError", "PermissionDeniedError", "BadRequestError")):
        return False
    lowered = msg.lower()
    return (
        "返回空内容" in msg
        or "调用失败" in msg
        or "调用超时" in msg
        or "无法连接" in msg
        or "请求过于频繁" in msg
        or "暂时不可用" in msg
        or "timeout" in lowered
        or "timed out" in lowered
    )


def _tracked_generate(state: dict, step_key: str, system: str, user: str) -> str:
    task_id = (state.get("task") or {}).get("id")
    model_profile_id = state.get("model_profile_id")
    model_runtime = None
    if model_profile_id:
        try:
            from app.api.model_settings import resolve_runtime_profile

            model_runtime = resolve_runtime_profile(str(model_profile_id))
        except (KeyError, ValueError) as exc:
            raise LLMError("所选模型配置已失效，请重新选择") from exc
    model_name = str((model_runtime or {}).get("model") or settings.llm_model)
    provider_name = str((model_runtime or {}).get("protocol") or "openai_compatible").replace("_", "-")
    max_tokens = {
        "intent.route": 500,
        # AgentIntent may contain several independently deliverable steps.  Reasoning
        # models count their hidden reasoning against max_tokens, so the old 2400
        # default could leave only a few hundred tokens for the JSON and truncate it.
        "intent.parse": 6000,
        "intent.repair": 6000,
        "conversation.reply": 600,
        "result.explain": 1200,
    }.get(step_key, 2400)
    max_attempts = max(1, int(settings.llm_retry_max_attempts))
    base_delay = max(0.0, float(settings.llm_retry_delay_seconds))
    previous_call_id: str | None = None
    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        active_runtime = _ACTIVE_RUNTIME.get()
        if active_runtime and active_runtime[0] == state.get("run_id"):
            _, runtime_started, runtime_limit = active_runtime
        else:
            runtime_started = float(state.get("runtime_started_monotonic") or 0)
            runtime_limit = float(state.get("runtime_duration_limit") or 0)
        elapsed = time.monotonic() - runtime_started if runtime_started else 0.0
        remaining = runtime_limit - elapsed if runtime_limit > 0 else None
        if remaining is not None and remaining <= 0:
            raise BudgetExceeded("max_duration_seconds", elapsed, runtime_limit)
        retry_of = previous_call_id or (
            _latest_call_id(state["run_id"], task_id, step_key) if state.get("retry_count") else None
        )
        call_id = usage.start_call(
            state["run_id"],
            provider=provider_name,
            model=model_name,
            step_key=step_key,
            task_id=task_id,
            retry_of_call_id=retry_of,
            model_profile_id=str(model_profile_id) if model_profile_id else None,
        )
        started = time.perf_counter()
        try:
            completion_options = {
                "temperature": 0.1,
                "max_tokens": max_tokens,
                "timeout_seconds": min(float(settings.llm_timeout_seconds), remaining) if remaining is not None else None,
                **({"structured": True} if step_key in {
                    "intent.parse", "intent.repair", "planner.build", "planner.evaluate", "visual.contract"
                } else {}),
            }
            if model_runtime is not None:
                completion_options["runtime"] = model_runtime
            response = provider.chat_completion(
                system,
                user,
                **completion_options,
            )
        except Exception as exc:  # noqa: BLE001
            usage.finish_call(
                call_id,
                status="failed",
                latency_ms=int((time.perf_counter() - started) * 1000),
                error_code="LLM_UNAVAILABLE",
            )
            previous_call_id = call_id
            last_error = exc
            if attempt < max_attempts and _llm_error_is_transient(exc):
                delay = base_delay * attempt
                if remaining is not None:
                    delay = min(delay, max(0.0, remaining))
                if delay:
                    time.sleep(delay)
                continue
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
    raise last_error if last_error else LLMError("LLM 调用失败")


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


def _probe_horizon(source_id: int, table: str, field: str) -> str | None:
    """对业务数据源探测某时间字段的最大值，用于把“上个月/最近 N 天”锚定到真实数据地平线。"""
    with PlatformSession() as session:
        source = session.query(DataSource).filter_by(id=source_id).first()
        if source is None:
            return None
    if not table or not field:
        return None
    sql = f"SELECT MAX({field}) AS horizon FROM {table}"
    result = execute_readonly(sql, action="Agent 探测数据时间地平线", source=source)
    if not result.ok or not result.rows:
        return None
    return str(result.rows[0][0])


def default_dependencies() -> GraphDependencies:
    from app.modeling.service import find_reusable_models
    from app.memory.service import retrieve_memories
    from app.skills.service import retrieve_skills

    return GraphDependencies(
        retrieve_context=lambda question, source_id: build_context_package(question, source_id),
        generate_text=_tracked_generate,
        validate_sql=validate_sql,
        execute_sql=_execute_for_source,
        explain_sql=_explain_for_source,
        retrieve_skills=retrieve_skills,
        retrieve_memories=lambda question, source_id, user_id, session_id, run_id: retrieve_memories(
            question, source_id, user_id, session_id=session_id, run_id=run_id
        ),
        find_reusable_models=find_reusable_models,
        resolve_time_horizon=lambda source_id, table, field: _probe_horizon(source_id, table, field),
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
            if row is None and task.get("id"):
                row = AnalysisTask(
                    id=task["id"],
                    run_id=run.id,
                    task_order=max(1, int(task.get("order") or 1)),
                    title=str(task.get("title") or "计划分析")[:200],
                    normalized_question=str(
                        task.get("normalizedQuestion")
                        or task.get("question")
                        or state.get("analysis_question")
                        or state.get("user_question")
                        or ""
                    ),
                    mode=str(task.get("mode") or "planned"),
                    analysis_type=str(task.get("analysisType") or "query"),
                    status="pending",
                    depends_on_json=list(task.get("dependsOn") or []),
                    intent_json={"planStepId": state.get("active_plan_step_id")},
                )
                session.add(row)
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
                row.intent_json = {**(row.intent_json or {}), **{key: task.get(key) for key in (
                    "groupBy", "timeConstraint", "metricHint", "filters", "sort", "topN",
                    "relation", "operation", "understanding", "inheritedFrom", "queryBinding",
                )}}
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
        if node in PLANNER_LOOP_NODES:
            attempt = (
                session.query(func.coalesce(func.max(AnalysisRunStep.attempt), 0))
                .filter_by(run_id=run.id, task_scope=task_id or "run", step_key=step_key)
                .scalar()
                + 1
            )
        start_event_type = {
            "build_business_plan": "planner.started",
            "invoke_business_capability": "capability.started",
        }.get(node, "step.started" if node != "prepare_retry" else "step.retrying")
        append_event(
            session,
            run,
            start_event_type,
            {
                "status": "running",
                "label": label,
                "attempt": attempt,
                "capability": (state.get("active_plan_step") or {}).get("capability"),
                "planStepId": state.get("active_plan_step_id"),
            },
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
        capability_result = update.get("capability_result") or {}
        observation = (update.get("observations") or [None])[-1] or {}
        if capability_result.get("status") == "failed" or observation.get("status") == "failed":
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
            # A rejected business plan can fall back to the fast task planner.
            # Its provisional rows use the same (run_id, task_order) values but
            # different task IDs, so remove only those unused plan rows first.
            planned_ids = {item["id"] for item in planned}
            provisional = session.query(AnalysisTask).filter_by(run_id=run.id).all()
            for existing in provisional:
                if (
                    existing.id not in planned_ids
                    and existing.status == "pending"
                    and (existing.intent_json or {}).get("planStepId")
                ):
                    session.delete(existing)
            session.flush()
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

        if node == "build_business_plan":
            plan = update.get("plan") or {}
            analysis_order = 0
            for item in plan.get("steps") or []:
                if item.get("capability") not in {"analysis.query_data", "modeling.propose"}:
                    continue
                analysis_order += 1
                task_payload = (item.get("arguments") or {}).get("task") or {}
                task_id_value = task_payload.get("id") or f"{run.id[:18]}-{analysis_order}"
                if session.query(AnalysisTask.id).filter_by(id=task_id_value).first() is not None:
                    continue
                session.add(
                    AnalysisTask(
                        id=task_id_value,
                        run_id=run.id,
                        task_order=analysis_order,
                        title=(task_payload.get("title") or item.get("purpose") or "计划分析")[:200],
                        normalized_question=(
                            task_payload.get("normalizedQuestion")
                            or (item.get("arguments") or {}).get("question")
                            or plan.get("goal")
                            or ""
                        ),
                        mode="planned",
                        analysis_type=(
                            task_payload.get("analysisType")
                            or ("modeling" if item.get("capability") == "modeling.propose" else "query")
                        ),
                        status="pending",
                        depends_on_json=list(item.get("dependsOn") or []),
                        intent_json={
                            "planStepId": item.get("id"),
                            "capability": item.get("capability"),
                            "purpose": item.get("purpose"),
                        },
                    )
                )
            append_event(
                session,
                run,
                "plan.created",
                {
                    "goal": plan.get("goal"),
                    "revision": update.get("plan_revision", 1),
                    "steps": plan.get("steps") or [],
                    "budget": plan.get("budget") or {},
                    "degraded": bool(update.get("planner_degraded")),
                    "reason": update.get("planning_reason"),
                },
            )
            if update.get("planner_degraded"):
                append_event(
                    session,
                    run,
                    "planner.degraded",
                    {
                        "reason": update.get("planning_reason") or "模型计划不可用，已切换确定性计划",
                        "revision": update.get("plan_revision", 1),
                    },
                )

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

        observation_content = (update.get("observations") or [None])[-1]
        if node in {"record_business_observation", "record_delegated_observation"} and observation_content:
            observation_content = dict(observation_content)
            # Planner 路径不经过 advance_task，因此必须在观察落库时收口
            # AnalysisTask 状态。否则 Run 已成功、产物也已交付，但侧边栏
            # 仍会永久显示“运行中”。
            if task_id:
                task_row = session.query(AnalysisTask).filter_by(id=task_id).first()
                if task_row:
                    observation_status = str(observation_content.get("status") or "failed")
                    task_row.status = "succeeded" if observation_status == "succeeded" else "failed"
                    task_row.finished_at = datetime.now()
            artifact_query = session.query(AnalysisArtifact.id).filter_by(run_id=run.id)
            if task_id:
                artifact_query = artifact_query.filter_by(task_id=task_id)
            evidence_refs = [
                row[0]
                for row in artifact_query.filter(
                    AnalysisArtifact.type.in_([
                        "context", "memory-context", "analysis-skill", "sql", "validation",
                        "result", "conclusion", "modeling-proposal", "capability-result",
                    ])
                ).order_by(AnalysisArtifact.created_at).all()
            ]
            observation_content["artifactRefs"] = list(dict.fromkeys([
                *(observation_content.get("artifactRefs") or []), *evidence_refs,
            ]))

        artifact_map = {
            "build_business_plan": ("agent-plan", {
                "plan": update.get("plan"),
                "revision": update.get("plan_revision", 1),
                "degraded": update.get("planner_degraded", False),
                "reason": update.get("planning_reason"),
            }),
            "validate_business_plan": ("plan-validation", update.get("plan_validation")),
            "invoke_business_capability": ("capability-result", update.get("capability_result")),
            "record_business_observation": ("capability-observation", observation_content),
            "record_delegated_observation": ("capability-observation", observation_content),
            "revise_business_plan": ("agent-plan-revision", {
                "plan": update.get("plan"),
                "revision": update.get("plan_revision"),
                "replanCount": update.get("replan_count"),
            }),
            "finalize_business_plan": ("delivery-ledger", update.get("delivery")),
            "retrieve_memory": ("memory-context", update.get("memory_context")),
            "retrieve_skill": ("analysis-skill", update.get("skill_context")),
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
        if node == "retrieve_context" and (update.get("task") or {}).get("queryBinding"):
            _artifact(session, run, task_id, "query-binding", update["task"]["queryBinding"])
        if node in {"explain_result", "reuse_presentation"} and update.get("interpretation"):
            _artifact(session, run, task_id, "interpretation", update.get("interpretation"))
        if node == "retrieve_skill":
            from app.skills.service import record_usage

            record_usage(run.id, task_id, update.get("skill_context") or {}, db_session=session)
            step.output_refs_json = [artifact_id]

        event_type = {
            "succeeded": (
                "modeling.proposed"
                if node == "propose_modeling"
                else {
                    "validate_business_plan": "plan.validated",
                    "invoke_business_capability": "capability.completed",
                    "record_business_observation": "observation.recorded",
                    "record_delegated_observation": "observation.recorded",
                    "revise_business_plan": "plan.revised",
                    "finalize_business_plan": "planner.completed",
                }.get(node, "step.completed")
            ),
            "failed": "capability.failed" if node == "invoke_business_capability" else "step.failed",
            "blocked": "step.blocked",
            "waiting_user": "clarification.requested",
        }[status]
        event_payload = {
            "status": status,
            "label": label,
            "attempt": attempt,
            "error": update.get("error"),
        }
        if node == "invoke_business_capability":
            event_payload.update({
                "capability": (state.get("active_plan_step") or {}).get("capability"),
                "planStepId": state.get("active_plan_step_id"),
                "summary": capability_result.get("summary"),
                "reasonCode": capability_result.get("reasonCode"),
            })
        elif node in {"record_business_observation", "record_delegated_observation"}:
            event_payload.update({
                "capability": (observation_content or {}).get("capability"),
                "planStepId": (observation_content or {}).get("stepId"),
                "summary": (observation_content or {}).get("summary"),
                "reasonCode": (observation_content or {}).get("reasonCode"),
                "artifactRefs": (observation_content or {}).get("artifactRefs") or [],
            })
        append_event(
            session,
            run,
            event_type,
            event_payload,
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
                kind=payload.get("kind") or "clarification",
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
        interrupt_event = "approval.requested" if payload.get("kind") == "approval" else "clarification.requested"
        append_event(
            session,
            run,
            interrupt_event,
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
            if proposal.get("recommendedAction") == "reuse":
                reusable = proposal.get("reusableModel") or {}
                content = (
                    f"已找到可复用的已发布模型：{reusable.get('name')} v{reusable.get('version')}，"
                    f"用于预测「{reusable.get('targetLabel') or reusable.get('targetField')}」。"
                    "本次不会重复训练；需要批量推理时将继续沿用该生产版本，并保留调用记录。"
                )
            else:
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
            from app.agent.request_context import successful_snapshot
            analysis_snapshot = successful_snapshot(state)
            understanding = " · ".join((state.get("agent_intent") or {}).get("understanding") or [])
            content = "\n\n".join(
                part for part in (state.get("direct_response", ""), understanding, analysis_content) if part
            ) or "分析完成"
            session.add(
                AnalysisMessage(
                    id=new_id("msg"),
                    session_id=run.session_id,
                    run_id=run.id,
                    role="assistant",
                    content=content,
                    sequence_no=sequence_no,
                    metadata_json={"analysisContext": analysis_snapshot} if analysis_snapshot else {},
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
        from app.skills.service import finish_run_usage

        finish_run_usage(run_id, run.status, db_session=session)
        if run.memory_enabled:
            from app.memory.service import enqueue_run_capture

            enqueue_run_capture(run_id, db_session=session)
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


def execute_inline_conversation(
    run_id: str,
    direct_response: str | None = None,
    *,
    intent_kind: str = "conversation",
    step_node: str = "route_request",
) -> dict:
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
            "model_profile_id": ((run.budget_snapshot_json or {}).get("llm") or {}).get("profileId"),
            "skill_enhancement_enabled": bool(run.skill_enhancement_enabled),
            "skill_context": {},
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
        "intent_kind": intent_kind,
        "analysis_question": "",
        "direct_response": response,
        "tasks": [],
        "blocked": False,
        "error": None,
    }
    state.update(update)
    _persist_update(run_id, step_node, update, state)
    _finish_run(run_id, state)
    return {"runId": run_id, "status": "succeeded"}


def execute_inline_report_clarification(run_id: str) -> dict:
    """Pause a chat report request until the user chooses a report template."""

    prompt = {
        "kind": "clarification",
        "question": "你想生成哪一种周报？请选择报告类型；选择后我会在报告中心创建并开始生成。",
        "options": list(CHAT_REPORT_TYPE_OPTIONS),
        "allowCustom": False,
    }
    with PlatformSession() as session:
        run = session.query(AnalysisRun).filter_by(id=run_id).with_for_update().one()
        if run.status in TERMINAL_STATUSES:
            return {"runId": run.id, "status": run.status}
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


def _chat_report_period(question: str) -> tuple[datetime, datetime]:
    now = datetime.now(CHAT_REPORT_TIMEZONE).replace(tzinfo=None)
    today = now.date()
    if "上周" in question:
        current_monday = today - timedelta(days=today.weekday())
        start = current_monday - timedelta(days=7)
        end = current_monday - timedelta(days=1)
        return datetime.combine(start, datetime.min.time()), datetime.combine(end, datetime.max.time())
    if "本周" in question:
        start = today - timedelta(days=today.weekday())
        return datetime.combine(start, datetime.min.time()), now.replace(
            hour=23, minute=59, second=59, microsecond=999999
        )
    if "本月" in question:
        start = today.replace(day=1)
        return datetime.combine(start, datetime.min.time()), now.replace(
            hour=23, minute=59, second=59, microsecond=999999
        )
    match = re.search(r"最近\s*(\d+)\s*(?:天|日)", question)
    days = int(match.group(1)) if match else 7
    days = min(max(days, 1), 366)
    start = today - timedelta(days=days)
    end = now.replace(hour=23, minute=59, second=59, microsecond=999999)
    return datetime.combine(start, datetime.min.time()), end


def execute_inline_report_request(run_id: str, template_id: str | None = None) -> dict:
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
            "model_profile_id": ((run.budget_snapshot_json or {}).get("llm") or {}).get("profileId"),
            "user_question": message.content,
            "conversation_context": [],
            "retry_count": 0,
            "attempted_sqls": [],
            "expected_lease_owner": "inline-conversation",
        }
        user_id = business_session.user_id
        session.commit()

    from app.reports import service as report_service
    from app.reports.contracts import ReportCreate

    period_start, period_end = _chat_report_period(state["user_question"])
    template_id = template_id or _chat_report_template(state["user_question"])
    try:
        report = report_service.create_report(
            ReportCreate(
                templateId=template_id,
                sourceId=state["source_id"],
                periodStart=period_start,
                periodEnd=period_end,
                ownerId=user_id,
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
            "model_profile_id": ((run.budget_snapshot_json or {}).get("llm") or {}).get("profileId"),
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
        labels, responses, specs = _model_route_decision(
            _tracked_generate(state, "intent.route", system, user),
            unknown,
        )
    except Exception:  # noqa: BLE001 - let the worker retry ambiguous routing later
        labels, responses, specs = {}, {}, {}

    routed = build_request_route(
        state["user_question"],
        labels,
        context,
    )

    # 多轮追问继承：极简的视角切换/下钻（“拆开来”“按产线看”“按周”“再看下排名”这类承前省略），
    # 在上一轮已有分析结果时，应作为新一轮查询继续分析——由 worker 里的 _rewrite_followup_question
    # 继承上一轮指标/时间并补全维度，而不是给一句“我这就去查”的对话承诺就收尾。
    # 仅当确实承前省略、且当前句无独立业务指标时才触发，避免覆盖真正的闲聊。
    if (
        _has_previous_result(context)
        and _is_turn_switch(state["user_question"])
        and not _has_business_analysis_signal(state["user_question"])
        and _requested_chart(state["user_question"]) is None
    ):
        with PlatformSession() as session:
            run = session.query(AnalysisRun).filter_by(id=run_id).with_for_update().one()
            if run.status in TERMINAL_STATUSES or run.lease_owner != "inline-conversation":
                return {"runId": run.id, "status": run.status}
            message = session.query(AnalysisMessage).filter_by(id=run.trigger_message_id).one()
            metadata = dict(message.metadata_json or {})
            metadata["routeLabels"] = {str(seg.get("index")): "analysis" for seg in routed["segments"]}
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
                {"status": "queued", "kind": "analysis", "reason": "followup_refinement"},
                step_key="intent.route",
            )
            session.commit()
        return {"runId": run_id, "status": "queued"}

    # 安全网：模型/规则把「明显非业务问句」判成分析时，直接按对话收尾，避免跑空 SQL。
    # 例如“5月有多少天”“比较苹果安卓哪个好”“世界上有多少个国家”——无任何真实业务实体，
    # 也不该进入数据检索。判定遵循「LLM 决定语义、确定性只守安全边界」。
    previous_business = any(
        _has_business_analysis_signal(str(item.get("content") or "")) or _has_analysis_verb(str(item.get("content") or ""))
        for item in context
        if item.get("role") == "user"
    )
    if routed["kind"] == "analysis" and should_downgrade_to_conversation(
        state["user_question"], "analysis", previous_business=previous_business
    ):
        return execute_inline_conversation(run_id, None, intent_kind="conversation", step_node="route_request")
    # The router may recognize a report request from its meaning rather than
    # from fixed report keywords (for example, “把最近一周的经营情况整理成
    # 一份总结”).  Keep this on the same clarification path as explicit report
    # requests and do not send it through SQL retrieval first.
    if routed.get("reportSegments") and not routed.get("analysisQuestion"):
        return execute_inline_report_clarification(run_id)
    # 模型路由失败（labels 为空）或把整个问句判为 mixed/analysis 时，若它其实不含任何
    # 业务实体（如纯闲聊/常识），也按对话收尾，避免入队后跑空 SQL 失败。
    if (not labels or routed["kind"] in {"analysis", "mixed"}) and should_downgrade_to_conversation(
        state["user_question"], "analysis", previous_business=previous_business
    ):
        return execute_inline_conversation(run_id, None, intent_kind="conversation", step_node="route_request")
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

    # 模型路由若只判了 kind 没给自然回复，就用一次对话 LLM 生成真实回应，
    # 避免落到模板兜底（“你好，我是 ForgeSight…”），让闲聊更像人话。
    if routed["kind"] == "conversation" and not responses:
        return execute_inline_conversation(run_id, None, intent_kind="conversation", step_node="route_request")
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
    active_started_at = time.monotonic()
    _ACTIVE_RUNTIME.set((run_id, active_started_at, 0.0))
    graph = build_analysis_graph(dependencies or default_dependencies(), checkpointer=checkpointer)
    # 先探查是否已有 checkpoint（恢复场景），避免恢复时重新调用 LLM 意图解析。
    config = {"configurable": {"thread_id": run_id}}
    snapshot = graph.get_state(config)
    has_checkpoint = bool(snapshot.values)
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
        frozen_access = (run.budget_snapshot_json or {}).get("access") or {}
        duration_limit = float(
            (run.budget_snapshot_json or {}).get("max_duration_seconds")
            or settings.agent_max_duration_seconds
        )
        _ACTIVE_RUNTIME.set((run_id, active_started_at, duration_limit))
        previous_task = _extract_previous_task(session, run)
        if previous_task and previous_task.get("analysisContext"):
            conversation_context.append({"role": "system", "content": "上一轮成功分析条件", "analysisContext": previous_task["analysisContext"]})
        initial = {
            "session_id": run.session_id,
            "run_id": run.id,
            "runtime_started_monotonic": active_started_at,
            "runtime_duration_limit": duration_limit,
            "source_id": run.source_id,
            "model_profile_id": ((run.budget_snapshot_json or {}).get("llm") or {}).get("profileId"),
            "tenant_id": frozen_access.get("tenantId") or (business_session.tenant_id if business_session else "tenant-default"),
            "role": frozen_access.get("role") or "business",
            "capability_scopes": list(frozen_access.get("capabilityScopes") or [
                "analysis:read", "scope:read", "task:read",
                "modeling:read", "modeling:draft", "modeling:predict",
                "report:read", "report:draft", "investigation:read",
            ]),
            "user_id": frozen_access.get("userId") or (business_session.user_id if business_session else "local-test-user"),
            "user_question": message.content,
            "conversation_context": conversation_context,
            "memory_enabled": bool(run.memory_enabled),
            "memory_context": {},
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
            "request_requirements": [],
            "task_index": 0,
            "task_results": [],
            "previous_task": previous_task,
            "planner_enabled": bool(
                ((run.budget_snapshot_json or {}).get("planner") or {}).get(
                    "enabled", settings.agent_planner_enabled
                )
            ),
            "planner_model_enabled": bool(
                ((run.budget_snapshot_json or {}).get("planner") or {}).get(
                    "modelPlanningEnabled", settings.agent_planner_model_enabled
                )
            ),
            "execution_path": "fast",
            "planning_reason": "",
            "plan": None,
            "plan_validation": {},
            "plan_revision": 0,
            "active_plan_step_id": None,
            "active_plan_step": None,
            "capability_result": None,
            "capability_calls": [],
            "observations": [],
            "replan_count": 0,
            "capability_budget": {},
            "pending_approval": None,
            "progress_decision": {},
            "finalization_reason": None,
            "planner_degraded": False,
            "blocked": False,
            "waiting_user": False,
            "error": None,
        }
        # 在进入可能较慢的 LLM 意图解析之前先提交：释放对 analysis_run 行的 FOR UPDATE 锁，
        # 避免 worker 在 `idle in transaction` 中阻塞后续认领，即使进程中途被杀也不会留下
        # 僵死事务挡住新 worker（配合 Postgres idle_in_transaction_session_timeout 兜底）。
        session.commit()
        # 系统能力不需要等待意图模型和数据库查询。对复合请求先冻结并
        # 落库这份确定性回答，SSE 收到 artifact.created 后即可立即展示；
        # 后续 LangGraph 复用同一文本，避免跨分钟时即时气泡与最终记录不一致。
        if not is_resume and not has_checkpoint and is_system_utility_request(message.content):
            utility_response = build_system_utility_response(message.content, run.source_id)
            initial["utility_response"] = utility_response
            existing_utility = session.query(AnalysisArtifact.id).filter_by(
                run_id=run.id, type="utility-response"
            ).first()
            if existing_utility is None:
                _artifact(
                    session,
                    run,
                    None,
                    "utility-response",
                    {"kind": "system", "text": utility_response},
                )
                session.commit()
        # 写意图在意图解析之前拦截：删除/更新等写入问法不进 LLM，由 intent_guard 一键阻断。
        from app.agent.intent import has_write_intent
        has_write_terms = has_write_intent(message.content)
        if settings.agent_intent_enabled and not is_resume and not has_checkpoint and not has_write_terms:
            # AgentIntent 驱动：旁路产出 AgentIntent 注入 state，route/planner/编译层消费它。
            # 仅在有开关且非恢复时执行；任何失败都不影响主执行路径。
            try:
                deps = dependencies or default_dependencies()
                intent, producer, intent_error = resolve_intent(
                    message.content,
                    conversation_context,
                    deps.generate_text,
                    run_id=run.id,
                    state=initial,
                )
                initial["agent_intent"] = intent.model_dump(mode="json", by_alias=True)
                from app.agent.delivery import build_requirement_ledger
                initial["request_requirements"] = build_requirement_ledger(intent, message.content)
                append_event(
                    session,
                    run,
                    "intent.agent_resolved",
                    {
                        "driven": True,
                        "producer": producer,
                        "error": intent_error,
                        "intent": initial["agent_intent"],
                    },
                )
            except Exception:  # noqa: BLE001 - shadow 失败不阻断主流程
                pass
        session.commit()

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
        deadline_exceeded = False
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
                    current_run = session.query(AnalysisRun).filter_by(id=run_id).first()
                    current_status = current_run.status if current_run else None
                    elapsed = time.monotonic() - active_started_at
                    if duration_limit > 0 and elapsed >= duration_limit:
                        state["error"] = {
                            "code": "RUN_DEADLINE_EXCEEDED",
                            "category": "budget",
                            "retryable": False,
                            "userSafeTitle": "分析已达到最长执行时间",
                            "userSafeMessage": "本次分析已停止，已完成的步骤和证据仍会保留。请缩小范围后重试。",
                            "usedSeconds": round(elapsed, 3),
                            "limitSeconds": duration_limit,
                        }
                        if current_run is not None:
                            append_event(
                                session,
                                current_run,
                                "run.budget.exceeded",
                                {
                                    "errorCode": "RUN_DEADLINE_EXCEEDED",
                                    "budgetType": "max_duration_seconds",
                                    "used": elapsed,
                                    "limit": duration_limit,
                                },
                            )
                            session.commit()
                        deadline_exceeded = True
                if current_status == "cancelled":
                    return {"runId": run_id, "status": "cancelled", "state": state}
                if deadline_exceeded:
                    break
            if deadline_exceeded:
                break
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
            return execute_inline_report_clarification(run_id)
        _finish_run(run_id, state)
    except Exception as exc:
        _mark_unexpected_failure(run_id, exc)
        raise
    return {"runId": run_id, "status": state.get("error") and "failed" or "succeeded", "state": state}


