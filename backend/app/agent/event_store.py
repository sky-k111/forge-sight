"""Append-only 领域事件存储。事件先落库，再由 SSE 查询/推送。"""

from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.agent.ids import new_id
from app.db.models import AnalysisEvent, AnalysisRun


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
    """在调用方事务内追加事件，并以数据库 MAX(seq)+1 作为权威 seq。

    usage.start_call / finish_call 会用独立 Session 追加 llm.* 事件；若本函数依赖传入
    run 对象内存中的 last_event_seq，跨会话时会拿到陈旧值并触发 uq_analysis_event_seq
    冲突（或与 execute_run 的 FOR UPDATE 相互死锁）。这里统一锁定 run 行并从库读取最新
    seq，保证跨会话顺序唯一、可重放。
    """
    # 锁定 run 行以序列化并发追加（同一事务内重复加锁是 re-entrant，安全）。
    # flush so a newly created (not yet persisted) run can be locked.
    session.flush()
    session.query(AnalysisRun).filter_by(id=run.id).with_for_update().one()
    seq = (
        session.query(func.coalesce(func.max(AnalysisEvent.seq), 0))
        .filter(AnalysisEvent.run_id == run.id)
        .scalar()
        + 1
    )
    event = AnalysisEvent(
        id=new_id("evt"),
        session_id=run.session_id,
        run_id=run.id,
        task_id=task_id,
        seq=seq,
        event_type=event_type,
        step_key=step_key,
        attempt=attempt,
        visibility=visibility,
        replayable=replayable,
        payload_json=payload or {},
    )
    session.add(event)
    run.last_event_seq = seq
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
