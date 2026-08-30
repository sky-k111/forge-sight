"""受限业务规划器的安全、预算、降级与现有 SQL 子图委托测试。"""

from langgraph.checkpoint.memory import InMemorySaver

from app.agent.capabilities import build_default_registry
from app.agent.capability_executor import CapabilityExecutor
from app.agent.capability_registry import CapabilityContext
from app.agent.graph import GraphDependencies, build_analysis_graph
from app.agent import service as analysis_service
from app.agent import settings as planner_settings
from app.agent.plan_schema import AgentPlan, PlanBudget
from app.agent.planner import build_plan, should_use_planner, validate_plan
from app.db.models import AnalysisArtifact, AnalysisRun, AnalysisTask, ReportTemplate
from app.gateway.executor import ExecutionResult
from app.gateway.sql_validator import SqlCheckResult
from app.reports.service import compose_report_from_evidence


def _context():
    return {
        "source": {"id": 1, "name": "测试数据源", "type": "mysql"},
        "metrics": [{"id": "M-004", "name": "工序良率", "formula": "SUM(good_qty)/SUM(input_qty)"}],
        "tables": [{"name": "mes_process_output", "fields": [{"name": "good_qty"}, {"name": "input_qty"}]}],
        "relations": [],
        "businessRules": [],
        "executionPolicy": {"allowedTables": ["mes_process_output"]},
        "coverage": {"status": "ready"},
    }


def test_simple_query_keeps_fast_path_and_root_cause_uses_planner():
    simple, simple_reason = should_use_planner(
        "统计本月工序良率", [{"kind": "analysis", "text": "统计本月工序良率"}], enabled=True
    )
    complex_goal, complex_reason = should_use_planner(
        "分析本月工序良率为什么下降", [{"kind": "analysis", "text": "分析本月工序良率为什么下降"}], enabled=True
    )
    assert simple is False
    assert simple_reason == "SIMPLE_FAST_PATH"
    assert complex_goal is True
    assert complex_reason == "ADAPTIVE_ANALYSIS"


def test_planner_rollout_assignment_is_stable_and_can_disable(monkeypatch):
    monkeypatch.setattr(analysis_service.settings, "agent_planner_enabled", True)
    monkeypatch.setattr(analysis_service.settings, "agent_planner_rollout_percent", 100)
    first = analysis_service._planner_assignment("operator-a")
    second = analysis_service._planner_assignment("operator-a")
    assert first == second
    assert first["enabled"] is True
    assert first["variant"] == "bounded-planner-v1"

    monkeypatch.setattr(analysis_service.settings, "agent_planner_rollout_percent", 0)
    baseline = analysis_service._planner_assignment("operator-a")
    assert baseline["enabled"] is False
    assert baseline["variant"] == "governed-baseline-v1"


def test_run_freezes_admin_planner_setting_and_access_scope(analysis_session):
    planner_settings.update_settings(
        False,
        rollout_percent=50,
        model_planning_enabled=False,
        updated_by="admin-test",
    )
    created = analysis_service.create_session("Planner 快照", 1, "operator-snapshot")
    accepted = analysis_service.create_run(created["id"], "分析本月工序良率", "planner-snapshot")
    with analysis_session() as session:
        run = session.query(AnalysisRun).filter_by(id=accepted["runId"]).one()
        assert run.workflow_version == "governed-baseline-v1"
        assert run.budget_snapshot_json["planner"]["enabled"] is False
        assert run.budget_snapshot_json["planner"]["rolloutPercent"] == 50
        assert run.budget_snapshot_json["access"]["sourceId"] == 1
        assert "analysis:read" in run.budget_snapshot_json["access"]["capabilityScopes"]


def test_model_plan_invalid_output_degrades_to_valid_registered_plan():
    registry = build_default_registry()
    state = {
        "run_id": "run-planner-1",
        "user_question": "分析良率为什么下降，并形成报告",
        "analysis_question": "分析良率为什么下降，并形成报告",
        "request_segments": [{"kind": "analysis", "text": "分析良率为什么下降，并形成报告"}],
        "conversation_context": [],
    }
    plan, source, reason = build_plan(
        state,
        registry.names(),
        lambda *_args: "这不是合法的计划 JSON",
        model_enabled=True,
        budget=PlanBudget(maxCapabilityCalls=8, maxDatabaseExecutions=3, maxReplans=1, maxAsyncJobs=1),
    )
    validation = validate_plan(plan, registry.names())
    delegated = [step for step in plan.steps if step.capability in {"analysis.query_data", "modeling.propose"}]
    assert source == "deterministic_fallback"
    assert reason == "ValueError"
    assert validation.valid is True
    assert delegated
    assert all(step.arguments.get("task", {}).get("id") for step in delegated)


def test_registry_exposes_business_capabilities_not_raw_sql_or_shell():
    registry = build_default_registry()
    names = registry.names()
    assert "analysis.query_data" in names
    assert "report.compose_from_evidence" in names
    assert "modeling.find_reusable" in names
    assert not any("shell" in name or "python" in name or "raw_sql" in name for name in names)
    business_names = registry.names_for("business", {"analysis:read", "report:draft"})
    assert "analysis.query_data" in business_names
    assert "report.compose_from_evidence" in business_names
    assert "report.send" not in business_names


def test_capability_authorization_freezes_source_scope_and_requires_approval():
    registry = build_default_registry()
    executor = CapabilityExecutor(registry)
    context = CapabilityContext(
        run_id="run-1", session_id="ses-1", tenant_id="tenant-a", user_id="user-a",
        role="business", source_ids=(1,), capability_scopes=frozenset({"analysis:read", "report:send"}),
        plan_revision=1, step_id="pst-1", idempotency_key="key-1", state={},
    )
    budget = PlanBudget(maxCapabilityCalls=8, maxDatabaseExecutions=3, maxReplans=1, maxAsyncJobs=1)
    denied = executor.authorize(context, "analysis.query_data", {"sourceId": 2}, budget, [])
    approval = executor.authorize(context, "report.send", {}, budget, [])
    assert denied.allowed is False
    assert denied.reason_code == "SOURCE_SCOPE_DENIED"
    assert approval.allowed is True
    assert approval.requires_approval is True


def test_complex_goal_delegates_query_to_existing_governed_subgraph():
    def generate(_state, step, _system, _user):
        if step == "planner.build":
            return "invalid planner output"
        if step == "query.generate":
            return "SELECT SUM(good_qty) AS good_qty FROM mes_process_output"
        return "工序良率下降主要集中在当前统计范围，建议继续按工序下钻。"

    deps = GraphDependencies(
        retrieve_context=lambda _question, _source_id: _context(),
        generate_text=generate,
        validate_sql=lambda _sql, _allowed: SqlCheckResult(ok=True, tables=["mes_process_output"], checks=["只读通过"]),
        execute_sql=lambda _sql, _source_id: ExecutionResult(
            ok=True, columns=["good_qty"], rows=[[90]], row_count=1,
            checks=["只读通过"], tables=["mes_process_output"],
        ),
    )
    graph = build_analysis_graph(deps, checkpointer=InMemorySaver())
    result = graph.invoke(
        {
            "session_id": "ses-planner", "run_id": "run-planner", "source_id": 1,
            "tenant_id": "tenant-a", "user_id": "user-a", "role": "business",
            "capability_scopes": ["analysis:read"],
            "user_question": "分析工序良率为什么下降", "planner_enabled": True,
            "retry_count": 0, "attempted_sqls": [],
        },
        config={"configurable": {"thread_id": "run-planner"}},
    )
    assert result["execution_path"] == "planner"
    assert result["planner_degraded"] is True
    assert any(item["capability"] == "analysis.query_data" and item["status"] == "succeeded" for item in result["observations"])
    assert result["finalization_reason"] == "GOAL_SATISFIED"


def test_plan_contract_rejects_unknown_dependency():
    try:
        AgentPlan.model_validate({
            "goal": "测试", "assumptions": [],
            "steps": [{
                "id": "pst-1", "capability": "analysis.query_data", "purpose": "查询",
                "arguments": {}, "dependsOn": ["missing"], "successCriteria": ["完成"],
                "onFailure": "stop_with_limitations",
            }],
            "stopConditions": ["完成"], "budget": {},
        })
    except ValueError as exc:
        assert "不存在" in str(exc) or "依赖" in str(exc)
    else:
        raise AssertionError("未知依赖必须被拒绝")


def test_report_draft_reuses_existing_artifacts_without_new_analysis_run(analysis_session):
    created = analysis_service.create_session("证据报告测试", 1, "owner-a")
    run_id = analysis_service.create_run(created["id"], "分析工序良率", "evidence-report")["runId"]
    with analysis_session() as session:
        run = session.query(AnalysisRun).filter_by(id=run_id).one()
        run.status = "succeeded"
        task = AnalysisTask(
            id="tsk-evidence-1", run_id=run.id, task_order=1, title="工序良率",
            normalized_question="分析工序良率", mode="single", analysis_type="query", status="succeeded",
        )
        session.add_all([
            ReportTemplate(
                id="tpl-operations-weekly", name="制造运营综合周报", report_type="weekly",
                topic="综合", sections_json=[], enabled=True,
            ),
            task,
            AnalysisArtifact(
                id="art-result-1", run_id=run.id, task_id=task.id, type="result",
                content_json={"columns": ["yield_rate"], "rows": [[97.5]], "row_count": 1},
                summary_json={}, checksum="a" * 64,
            ),
            AnalysisArtifact(
                id="art-conclusion-1", run_id=run.id, task_id=task.id, type="conclusion",
                content_json={"text": "当前工序良率为 97.5%。", "confidence": {"score": 96}},
                summary_json={}, checksum="b" * 64,
            ),
        ])
        session.commit()

    draft = compose_report_from_evidence(
        run_id=run_id, owner_id="owner-a", source_id=1,
        template_id="tpl-operations-weekly", title="工序良率证据草稿",
        session_factory=analysis_session,
    )
    assert draft["status"] == "draft"
    assert draft["analysisRunId"] is None
    assert set(draft["artifactRefs"]) == {"art-result-1", "art-conclusion-1"}
    assert draft["sections"][0]["summary"] == "当前工序良率为 97.5%。"


def test_run_projection_uses_latest_revised_plan(analysis_session):
    created = analysis_service.create_session("计划修订投影", 1, "owner-plan")
    run_id = analysis_service.create_run(created["id"], "分析良率为什么下降", "plan-revision")["runId"]
    original = {
        "goal": "分析良率为什么下降", "assumptions": [], "steps": [],
        "stopConditions": ["完成"], "budget": {},
    }
    revised = {**original, "steps": [{
        "id": "pst-revised", "capability": "analysis.inspect_result_quality",
        "purpose": "披露限制", "arguments": {}, "dependsOn": [],
        "successCriteria": ["完成"], "onFailure": "stop_with_limitations",
    }]}
    with analysis_session() as session:
        session.add_all([
            AnalysisArtifact(
                id="art-plan-base", run_id=run_id, type="agent-plan",
                content_json={"plan": original, "revision": 1, "degraded": True, "reason": "fallback"},
                summary_json={}, checksum="c" * 64,
            ),
            AnalysisArtifact(
                id="art-plan-revised", run_id=run_id, type="agent-plan-revision",
                content_json={"plan": revised, "revision": 2, "replanCount": 1},
                summary_json={}, checksum="d" * 64,
            ),
        ])
        session.commit()
    projected = analysis_service.get_run(run_id)
    assert projected["planner"]["revision"] == 2
    assert projected["planner"]["plan"]["steps"][0]["id"] == "pst-revised"
    assert projected["planner"]["degraded"] is True
