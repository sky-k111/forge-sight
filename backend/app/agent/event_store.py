"""Append-only 领域事件存储。事件先落库，再由 SSE 查询/推送。"""

from typing import Any

from sqlalchemy.orm import Session

from app.agent.ids import new_id
from app.db.models import AnalysisEvent, AnalysisRun


class RunNotFound(KeyError):
    pass


def append_event(
    session: Session,
    run: AnalysisRun,
    event_type: str,
    payload: dict[str, Any] | None = None,
    *,
    task_id: str | None = None,
    step_key: str | None = None,
    attempt: int | None = None,
    visibility: str = "user",
    replayable: bool = True,
) -> AnalysisEvent:
    """在调用方事务内递增 seq 并追加事件。"""
    # SQLAlchemy 的 Python default 在 INSERT 时生效；新建 Run 尚未 flush 时字段仍可能为 None。
    run.last_event_seq = (run.last_event_seq or 0) + 1
    event = AnalysisEvent(
        id=new_id("evt"),
        session_id=run.session_id,
        run_id=run.id,
        task_id=task_id,
        seq=run.last_event_seq,
        event_type=event_type,
        step_key=step_key,
        attempt=attempt,
        visibility=visibility,
        replayable=replayable,
        payload_json=payload or {},
    )
    session.add(event)
    return event


def serialize_event(event: AnalysisEvent) -> dict[str, Any]:
    return {
        "schemaVersion": "1.0",
        "eventId": event.id,
        "sessionId": event.session_id,
        "runId": event.run_id,
        "taskId": event.task_id,
        "seq": event.seq,
        "type": event.event_type,
        "occurredAt": event.occurred_at.isoformat(),
        "stepKey": event.step_key,
        "attempt": event.attempt,
        "visibility": event.visibility,
        "replayable": event.replayable,
        "payload": event.payload_json or {},
    }
