"""PostgreSQL 持久队列 Worker。Run 不依赖 FastAPI BackgroundTasks。"""

import logging
import socket
import threading
from datetime import datetime, timedelta

from sqlalchemy import or_

from app.agent.checkpointer import get_checkpointer
from app.agent.runner import execute_run
from app.config import settings
from app.db.models import AnalysisRun
from app.db.platform import PlatformSession

log = logging.getLogger(__name__)
_stop = threading.Event()
_thread: threading.Thread | None = None
_worker_id = f"{socket.gethostname()}:{id(_stop)}"


def _claim_run() -> str | None:
    now = datetime.now()
    with PlatformSession() as session:
        query = (
            session.query(AnalysisRun)
            .filter(
                or_(
                    AnalysisRun.status == "queued",
                    (AnalysisRun.status == "running")
                    & or_(AnalysisRun.lease_expires_at.is_(None), AnalysisRun.lease_expires_at < now),
                )
            )
            .order_by(AnalysisRun.created_at)
        )
        if session.bind and session.bind.dialect.name == "postgresql":
            query = query.with_for_update(skip_locked=True)
        run = query.first()
        if run is None:
            return None
        # claim 时立即转 running，避免其他实例在本次事务提交后再次认领同一个 queued Run。
        run.status = "running"
        run.lease_owner = _worker_id
        run.lease_expires_at = now + timedelta(
            seconds=max(settings.agent_worker_lease_seconds, settings.agent_max_duration_seconds + 30)
        )
        session.commit()
        return run.id


def _loop() -> None:
    while not _stop.is_set():
        try:
            from app.reports.automation import scan_due_subscriptions

            scan_due_subscriptions()
        except Exception:  # noqa: BLE001 - 单个调度故障不能阻塞既有 Agent 队列
            log.exception("周期周报到期扫描失败")
        run_id = _claim_run()
        if run_id is None:
            _stop.wait(settings.agent_worker_poll_seconds)
            continue
        try:
            execute_run(run_id, checkpointer=get_checkpointer())
        except Exception:  # noqa: BLE001 - runner 已持久化结构化失败，这里记录堆栈
            log.exception("Agent Run 执行失败: %s", run_id)


def start_worker() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="forge-sight-agent-worker", daemon=True)
    _thread.start()


def stop_worker() -> None:
    _stop.set()
    if _thread and _thread.is_alive():
        _thread.join(timeout=5)
