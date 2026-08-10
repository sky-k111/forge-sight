"""阶段 5 Session/Run/Event/Usage HTTP API。"""

import asyncio
import json
import time

from fastapi import APIRouter, Header, HTTPException, Query, status
from fastapi.responses import StreamingResponse

from app.agent import service, usage
from app.agent.contracts import BranchCreate, ClarificationResponse, RunCreate, SessionCreate

router = APIRouter(prefix="/api/analysis", tags=["analysis"])


def _not_found(exc: Exception) -> HTTPException:
    return HTTPException(status_code=404, detail=str(exc).strip("'"))


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
def create_session(payload: SessionCreate):
    try:
        return service.create_session(payload.title, payload.sourceId, payload.userId)
    except service.AnalysisNotFound as exc:
        raise _not_found(exc) from exc


@router.get("/sessions")
def list_sessions(userId: str = Query(default="local-test-user", min_length=1, max_length=64)):
    return service.list_sessions(userId)


@router.get("/sessions/{session_id}")
def get_session(session_id: str):
    try:
        return service.get_session(session_id)
    except service.AnalysisNotFound as exc:
        raise _not_found(exc) from exc


@router.delete("/sessions/{session_id}")
def delete_session(
    session_id: str,
    userId: str = Query(default="local-test-user", min_length=1, max_length=64),
):
    try:
        return service.delete_session(session_id, userId)
    except service.AnalysisNotFound as exc:
        raise _not_found(exc) from exc


@router.post("/sessions/{session_id}/runs", status_code=status.HTTP_202_ACCEPTED)
def create_run(
    session_id: str,
    payload: RunCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=100),
):
    try:
        return service.create_run(session_id, payload.question, idempotency_key)
    except service.AnalysisNotFound as exc:
        raise _not_found(exc) from exc
    except service.AnalysisConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/runs/{run_id}")
def get_run(run_id: str):
    try:
        return service.get_run(run_id)
    except service.AnalysisNotFound as exc:
        raise _not_found(exc) from exc


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: str):
    try:
        return service.cancel_run(run_id)
    except service.AnalysisNotFound as exc:
        raise _not_found(exc) from exc


@router.post("/interactions/{interaction_id}/responses", status_code=status.HTTP_202_ACCEPTED)
def answer_clarification(interaction_id: str, payload: ClarificationResponse):
    try:
        return service.respond_to_interaction(interaction_id, payload.answer)
    except service.AnalysisNotFound as exc:
        raise _not_found(exc) from exc
    except service.AnalysisConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/runs/{run_id}/checkpoints")
def get_checkpoints(run_id: str):
    try:
        return {"items": service.list_checkpoints(run_id)}
    except service.AnalysisNotFound as exc:
        raise _not_found(exc) from exc


@router.post("/checkpoints/{checkpoint_id}/branches", status_code=status.HTTP_201_CREATED)
def create_branch(checkpoint_id: str, payload: BranchCreate):
    try:
        return service.create_branch(
            checkpoint_id,
            payload.userId,
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
):
    try:
        return {"items": service.list_events(run_id, afterSeq, limit, include_admin=includeAdmin)}
    except service.AnalysisNotFound as exc:
        raise _not_found(exc) from exc


@router.get("/runs/{run_id}/stream")
def stream_events(
    run_id: str,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    includeAdmin: bool = False,
):
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
def get_usage(run_id: str, includeAttempts: bool = False):
    try:
        return usage.run_usage(run_id, include_attempts=includeAttempts)
    except KeyError as exc:
        raise _not_found(exc) from exc
