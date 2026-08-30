"""阶段 5 Session/Run/Event/Usage HTTP API。"""

import asyncio
import json
import time

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.agent import service, settings as planner_settings, usage
from app.agent.contracts import BranchCreate, ClarificationResponse, RunCreate, SessionCreate
from app.auth.deps import effective_user_id, optional_user
from app.db.platform import PlatformSession
from app.tenancy.service import (
    TenantContext,
    ensure_analysis_run_access,
    ensure_analysis_session_access,
    ensure_checkpoint_access,
    ensure_interaction_access,
    tenant_context,
)

router = APIRouter(prefix="/api/analysis", tags=["analysis"])


class PlannerSettingPayload(BaseModel):
    enabled: bool
    rolloutPercent: int = Field(default=100, ge=0, le=100)
    modelPlanningEnabled: bool = True


def _admin_identity(user: dict | None) -> str:
    if user and user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可调整 Agent 规划实验")
    return effective_user_id("local-admin", user)


@router.get("/planner-settings")
def get_planner_settings(user: dict | None = Depends(optional_user)):
    _admin_identity(user)
    return {**planner_settings.snapshot_settings(), "experiment": planner_settings.experiment_summary()}


@router.patch("/planner-settings")
def set_planner_settings(payload: PlannerSettingPayload, user: dict | None = Depends(optional_user)):
    return planner_settings.update_settings(
        payload.enabled,
        rollout_percent=payload.rolloutPercent,
        model_planning_enabled=payload.modelPlanningEnabled,
        updated_by=_admin_identity(user),
    )


def _not_found(exc: Exception) -> HTTPException:
    return HTTPException(status_code=404, detail=str(exc).strip("'"))


def _actor(user: dict | None) -> str:
    """归属校验用的账号标识；与 AnalysisSession.user_id 的写入口径一致。"""

    return user["username"] if user else "local-test-user"


def _guard_session(session_id: str, context: TenantContext, user: dict | None) -> None:
    with PlatformSession() as db:
        ensure_analysis_session_access(db, session_id, context.tenant_id, user_id=_actor(user))


def _guard_run(run_id: str, context: TenantContext, user: dict | None) -> None:
    with PlatformSession() as db:
        ensure_analysis_run_access(db, run_id, context.tenant_id, user_id=_actor(user))


def _guard_checkpoint(checkpoint_id: str, context: TenantContext, user: dict | None) -> None:
    with PlatformSession() as db:
        ensure_checkpoint_access(db, checkpoint_id, context.tenant_id, user_id=_actor(user))


def _guard_interaction(interaction_id: str, context: TenantContext, user: dict | None) -> None:
    with PlatformSession() as db:
        ensure_interaction_access(db, interaction_id, context.tenant_id, user_id=_actor(user))


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
def create_session(
    payload: SessionCreate,
    user: dict | None = Depends(optional_user),
    context: TenantContext = Depends(tenant_context),
):
    try:
        return service.create_session(
            payload.title, payload.sourceId, _actor(user), context.tenant_id
        )
    except service.AnalysisNotFound as exc:
        raise _not_found(exc) from exc


@router.get("/sessions")
def list_sessions(
    userId: str = Query(default="local-test-user", min_length=1, max_length=64),
    user: dict | None = Depends(optional_user),
    context: TenantContext = Depends(tenant_context),
):
    # 归属以服务端解析为准：匿名演示沿用参数（兼容本地联调），登录后仅能查看自己；
    # 登录态携带他人 userId 时返回空列表而非报错，避免确认他人会话是否存在。
    scope_user = _actor(user)
    if user is None:
        scope_user = userId
    elif userId != scope_user:
        scope_user = "\u0000out-of-scope"
    return service.list_sessions(scope_user, context.tenant_id)


@router.get("/sessions/{session_id}")
def get_session(
    session_id: str,
    user: dict | None = Depends(optional_user),
    context: TenantContext = Depends(tenant_context),
):
    _guard_session(session_id, context, user)
    try:
        return service.get_session(session_id, context.tenant_id)
    except service.AnalysisNotFound as exc:
        raise _not_found(exc) from exc


@router.delete("/sessions/{session_id}")
def delete_session(
    session_id: str,
    userId: str = Query(default="local-test-user", min_length=1, max_length=64),
    user: dict | None = Depends(optional_user),
    context: TenantContext = Depends(tenant_context),
):
    try:
        return service.delete_session(session_id, _actor(user), context.tenant_id)
    except service.AnalysisNotFound as exc:
        raise _not_found(exc) from exc


@router.post("/sessions/{session_id}/runs", status_code=status.HTTP_202_ACCEPTED)
def create_run(
    session_id: str,
    payload: RunCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=100),
    user: dict | None = Depends(optional_user),
    context: TenantContext = Depends(tenant_context),
):
    # 只有会话归属人可以在会话内继续提问，同租户其他成员不能插入。
    _guard_session(session_id, context, user)
    try:
        return service.create_run(session_id, payload.question, idempotency_key, context.tenant_id)
    except service.AnalysisNotFound as exc:
        raise _not_found(exc) from exc
    except service.AnalysisConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/runs/{run_id}")
def get_run(
    run_id: str,
    user: dict | None = Depends(optional_user),
    context: TenantContext = Depends(tenant_context),
):
    _guard_run(run_id, context, user)
    try:
        return service.get_run(run_id)
    except service.AnalysisNotFound as exc:
        raise _not_found(exc) from exc


@router.post("/runs/{run_id}/cancel")
def cancel_run(
    run_id: str,
    user: dict | None = Depends(optional_user),
    context: TenantContext = Depends(tenant_context),
):
    _guard_run(run_id, context, user)
    try:
        return service.cancel_run(run_id)
    except service.AnalysisNotFound as exc:
        raise _not_found(exc) from exc


@router.post("/interactions/{interaction_id}/responses", status_code=status.HTTP_202_ACCEPTED)
def answer_clarification(
    interaction_id: str,
    payload: ClarificationResponse,
    user: dict | None = Depends(optional_user),
    context: TenantContext = Depends(tenant_context),
):
    _guard_interaction(interaction_id, context, user)
    try:
        return service.respond_to_interaction(interaction_id, payload.answer)
    except service.AnalysisNotFound as exc:
        raise _not_found(exc) from exc
    except service.AnalysisConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/runs/{run_id}/checkpoints")
def get_checkpoints(
    run_id: str,
    user: dict | None = Depends(optional_user),
    context: TenantContext = Depends(tenant_context),
):
    _guard_run(run_id, context, user)
    try:
        return {"items": service.list_checkpoints(run_id)}
    except service.AnalysisNotFound as exc:
        raise _not_found(exc) from exc


@router.post("/checkpoints/{checkpoint_id}/branches", status_code=status.HTTP_201_CREATED)
def create_branch(
    checkpoint_id: str,
    payload: BranchCreate,
    user: dict | None = Depends(optional_user),
    context: TenantContext = Depends(tenant_context),
):
    _guard_checkpoint(checkpoint_id, context, user)
    try:
        return service.create_branch(
            checkpoint_id,
            _actor(user),
            payload.title,
            instruction=payload.instruction,
            adjustments=payload.adjustments,
            reuse_artifacts=payload.reuseArtifacts,
        )
    except service.AnalysisNotFound as exc:
        raise _not_found(exc) from exc


@router.get("/runs/{run_id}/events")
def get_events(
    run_id: str,
    afterSeq: int = Query(default=0, ge=0),
    limit: int = Query(default=500, ge=1, le=500),
    includeAdmin: bool = False,
    user: dict | None = Depends(optional_user),
    context: TenantContext = Depends(tenant_context),
):
    _guard_run(run_id, context, user)
    try:
        return {"items": service.list_events(run_id, afterSeq, limit, include_admin=includeAdmin)}
    except service.AnalysisNotFound as exc:
        raise _not_found(exc) from exc


@router.get("/runs/{run_id}/stream")
def stream_events(
    run_id: str,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    includeAdmin: bool = False,
    user: dict | None = Depends(optional_user),
    context: TenantContext = Depends(tenant_context),
):
    _guard_run(run_id, context, user)
    try:
        service.get_run_status(run_id)
    except service.AnalysisNotFound as exc:
        raise _not_found(exc) from exc
    try:
        initial_seq = max(0, int(last_event_id or 0))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Last-Event-ID 必须是事件序号") from exc

    async def generate():
        last_seq = initial_seq
        heartbeat_at = time.monotonic()
        terminal = {"succeeded", "failed", "blocked", "cancelled"}
        while True:
            items = service.list_events(run_id, last_seq, 500, include_admin=includeAdmin)
            for event in items:
                last_seq = event["seq"]
                data = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
                yield f"id: {last_seq}\nevent: {event['type']}\ndata: {data}\n\n"
            current_status = service.get_run_status(run_id)
            if current_status in terminal and not items:
                break
            if time.monotonic() - heartbeat_at >= 15:
                heartbeat_at = time.monotonic()
                yield "event: heartbeat\ndata: {}\n\n"
            await asyncio.sleep(0.35)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/runs/{run_id}/usage")
def get_usage(
    run_id: str,
    includeAttempts: bool = False,
    user: dict | None = Depends(optional_user),
    context: TenantContext = Depends(tenant_context),
):
    _guard_run(run_id, context, user)
    try:
        return usage.run_usage(run_id, include_attempts=includeAttempts)
    except KeyError as exc:
        raise _not_found(exc) from exc
