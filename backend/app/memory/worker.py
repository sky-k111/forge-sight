"""Small durable memory worker; queued jobs survive API restarts."""

from __future__ import annotations

import threading
import time
import uuid

from app.memory.service import claim_next_job, process_job

_thread: threading.Thread | None = None
_stop = threading.Event()
_worker_id = f"memory-{uuid.uuid4().hex[:10]}"


def _loop() -> None:
    while not _stop.wait(1.5):
        job_id = claim_next_job(_worker_id)
        if not job_id:
            continue
        try:
            process_job(job_id)
        except Exception:
            time.sleep(0.5)


def start_worker() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="agent-memory-worker", daemon=True)
    _thread.start()


def stop_worker() -> None:
    _stop.set()
    if _thread:
        _thread.join(timeout=3)
