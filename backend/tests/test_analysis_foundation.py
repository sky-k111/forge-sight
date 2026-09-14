"""阶段 5A：Session/Run/Event/Usage/预算地基。"""

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.agent import service, usage
from app.agent.budgets import BudgetExceeded
from app.db.models import AnalysisRun, LlmCallAttempt, LlmModelPrice


def test_active_duration_excludes_clarification_waiting_time():
    started = datetime(2026, 7, 22, 18, 44, 51)
    events = [
        SimpleNamespace(event_type="run.started", occurred_at=started),
        SimpleNamespace(event_type="clarification.requested", occurred_at=started + timedelta(seconds=1)),
        SimpleNamespace(event_type="run.resumed", occurred_at=started + timedelta(minutes=15, seconds=39)),
        SimpleNamespace(event_type="run.completed", occurred_at=started + timedelta(minutes=15, seconds=52)),
    ]

    assert service._active_duration_ms(events) == 14_000


def test_session_run_idempotency_and_event_sequence(analysis_session):
    created = service.create_session("质量分析", 1, "u-1")
    accepted = service.create_run(created["id"], "分析最近 7 天工序良率", "idem-1")
    repeated = service.create_run(created["id"], "不会创建第二个 Run", "idem-1")

    assert repeated["runId"] == accepted["runId"]
    snapshot = service.get_run(accepted["runId"])
    assert snapshot["status"] == "queued"
    assert snapshot["lastSeq"] == 1
    assert snapshot["budget"]["max_llm_calls"] > 0
    events = service.list_events(accepted["runId"])
    assert [item["type"] for item in events] == ["run.accepted"]
    assert events[0]["seq"] == 1


def test_session_list_uses_latest_real_question_as_display_title(analysis_session):
    created = service.create_session("未命名分析", 1, "u-session-list")
    before = service.list_sessions("u-session-list")

    assert before[0]["displayTitle"] == "未命名分析"
    assert before[0]["runCount"] == 0
    assert before[0]["latestRunStatus"] is None

    service.create_run(created["id"], "统计最近 7 天各产线产量趋势", "idem-title")
    after = service.list_sessions("u-session-list")

    assert after[0]["displayTitle"] == "统计最近 7 天各产线产量趋势"
    assert after[0]["runCount"] == 1
    assert after[0]["latestRunStatus"] == "queued"


def test_delete_session_hides_it_without_destroying_audit_history(analysis_session):
    created = service.create_session("待删除对话", 1, "u-delete")
    accepted = service.create_run(created["id"], "分析设备停机时长", "idem-delete")

    result = service.delete_session(created["id"], "u-delete")

    assert result == {
        "sessionId": created["id"],
        "deleted": True,
        "retention": "audit",
    }
    assert service.list_sessions("u-delete") == []
    assert service.get_run(accepted["runId"])["question"] == "分析设备停机时长"
    with pytest.raises(service.AnalysisConflict):
        service.create_run(created["id"], "删除后不能继续提问")


def test_session_title_can_be_renamed_and_stays_stable_across_follow_up_questions(analysis_session):
    created = service.create_session("未命名分析", 1, "u-rename")
    service.create_run(created["id"], "先看一下六月良率", "idem-rename-1")

    renamed = service.rename_session(created["id"], "  六月质量复盘  ", "u-rename")
    service.create_run(created["id"], "再按产线拆开", "idem-rename-2")

    assert renamed["displayTitle"] == "六月质量复盘"
    assert service.list_sessions("u-rename")[0]["displayTitle"] == "六月质量复盘"


def test_bulk_delete_sessions_keeps_audit_runs_but_hides_all_selected_sessions(analysis_session):
    first = service.create_session("会话一", 1, "u-bulk")
    second = service.create_session("会话二", 1, "u-bulk")
    run_id = service.create_run(first["id"], "查询六月产量", "idem-bulk")["runId"]

    result = service.delete_sessions([first["id"], second["id"]], "u-bulk")

    assert result["deletedCount"] == 2
    assert result["deletedIds"] == [first["id"], second["id"]]
    assert service.list_sessions("u-bulk") == []
    assert service.get_run(run_id)["question"] == "查询六月产量"


def test_llm_attempt_usage_price_snapshot_and_retry_totals(analysis_session):
    created = service.create_session("设备分析", 1, "u-1")
    run_id = service.create_run(created["id"], "分析设备停机", "idem-usage")["runId"]
    with analysis_session() as session:
        session.add(
            LlmModelPrice(
                provider="deepseek",
                model="deepseek-chat",
                currency="CNY",
                input_price=2,
                cached_input_price=0.5,
                output_price=8,
                reasoning_price=8,
                unit_tokens=1_000_000,
                version="2026-07-test",
                effective_from=datetime(2026, 1, 1),
            )
        )
        session.commit()

    call_1 = usage.start_call(
        run_id,
        provider="deepseek",
        model="deepseek-chat",
        step_key="query.generate",
    )
    first = usage.finish_call(
        call_1,
        status="failed",
        latency_ms=800,
        input_tokens=1000,
        cached_input_tokens=200,
        output_tokens=100,
        total_tokens=1100,
        error_code="OUTPUT_SCHEMA_INVALID",
    )
    call_2 = usage.start_call(
        run_id,
        provider="deepseek",
        model="deepseek-chat",
        step_key="query.generate",
        retry_of_call_id=call_1,
    )
    usage.finish_call(
        call_2,
        status="succeeded",
        latency_ms=600,
        input_tokens=1200,
        output_tokens=120,
        total_tokens=1320,
    )

    result = usage.run_usage(run_id, include_attempts=True)
    assert first["priceConfigured"] is True
    assert result["callCount"] == 2
    assert result["retryCallCount"] == 1
    assert result["totalTokens"] == 2420
    assert result["retryTokens"] == 1320
    assert result["estimatedCost"] > 0
    assert len(result["attempts"]) == 2
    with analysis_session() as session:
        attempts = session.query(LlmCallAttempt).filter_by(run_id=run_id).all()
        assert attempts[0].price_snapshot_json["version"] == "2026-07-test"


def test_budget_stops_next_model_call(analysis_session):
    created = service.create_session("预算测试", 1, "u-1")
    run_id = service.create_run(created["id"], "简单查询", "idem-budget")["runId"]
    with analysis_session() as session:
        run = session.query(AnalysisRun).filter_by(id=run_id).one()
        budget = dict(run.budget_snapshot_json)
        budget["max_llm_calls"] = 1
        run.budget_snapshot_json = budget
        session.commit()

    call_id = usage.start_call(
        run_id,
        provider="test",
        model="test-model",
        step_key="intent.parse",
    )
    usage.finish_call(call_id, status="succeeded", latency_ms=1, input_tokens=5, output_tokens=2)

    with pytest.raises(BudgetExceeded) as exc_info:
        usage.start_call(
            run_id,
            provider="test",
            model="test-model",
            step_key="result.explain",
        )
    assert exc_info.value.budget_type == "max_llm_calls"
    events = service.list_events(run_id)
    assert events[-1]["type"] == "run.budget.exceeded"
