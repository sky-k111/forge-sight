"""Persistence regression for a rejected business plan falling back to fast tasks."""

from app.agent import runner
from app.db.models import AnalysisRun, AnalysisTask


def test_plan_fallback_replaces_provisional_tasks(analysis_session):
    with analysis_session() as session:
        session.add(
            AnalysisRun(
                id="run-fallback",
                session_id="session-fallback",
                trigger_message_id="message-fallback",
                source_id=1,
                status="running",
            )
        )
        session.commit()

    plan_steps = [
        {
            "id": f"step-{order}",
            "capability": "analysis.query_data",
            "purpose": f"计划任务 {order}",
            "arguments": {"task": {"id": f"provisional-{order}"}},
        }
        for order in (1, 2)
    ]
    runner._persist_update(
        "run-fallback",
        "build_business_plan",
        {"plan": {"goal": "生成报告", "steps": plan_steps}},
        {},
    )

    fast_tasks = [
        {
            "id": f"fallback-{order}",
            "order": order,
            "title": f"正式任务 {order}",
            "normalizedQuestion": f"查询指标 {order}",
            "mode": "single",
            "analysisType": "query",
        }
        for order in (1, 2)
    ]
    runner._persist_update(
        "run-fallback", "plan_tasks", {"tasks": fast_tasks}, {}
    )

    with analysis_session() as session:
        tasks = session.query(AnalysisTask).filter_by(run_id="run-fallback").order_by(AnalysisTask.task_order).all()
        assert [(task.id, task.task_order) for task in tasks] == [
            ("fallback-1", 1), ("fallback-2", 2)
        ]
