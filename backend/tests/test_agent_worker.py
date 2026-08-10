"""阶段 5F：持久队列认领、过期租约恢复与并发互斥。"""

from datetime import datetime, timedelta

from app.agent import service, worker
from app.db.models import AnalysisRun


def test_worker_claim_is_exclusive_and_expired_run_is_recovered(analysis_session, monkeypatch):
    monkeypatch.setattr(worker, "PlatformSession", analysis_session)
    created = service.create_session("Worker 恢复测试", 1, "u-worker")
    run_id = service.create_run(created["id"], "分析工序良率", "worker-idem")["runId"]

    assert worker._claim_run() == run_id
    assert worker._claim_run() is None

    with analysis_session() as session:
        run = session.query(AnalysisRun).filter_by(id=run_id).one()
        run.lease_expires_at = datetime.now() - timedelta(seconds=1)
        session.commit()

    assert worker._claim_run() == run_id


def test_waiting_user_run_is_never_reclaimed(analysis_session, monkeypatch):
    monkeypatch.setattr(worker, "PlatformSession", analysis_session)
    created = service.create_session("Worker 澄清测试", 1, "u-worker")
    run_id = service.create_run(created["id"], "分析良率", "waiting-idem")["runId"]
    with analysis_session() as session:
        run = session.query(AnalysisRun).filter_by(id=run_id).one()
        run.status = "waiting_user"
        run.lease_expires_at = datetime.now() - timedelta(seconds=1)
        session.commit()

    assert worker._claim_run() is None

