"""阶段 5C 最小真实 LangGraph 与持久化投影。"""

from copy import deepcopy

from langgraph.checkpoint.memory import InMemorySaver

from app.agent import runner, service
from app.agent.graph import (
    GraphDependencies,
    _confidence_assessment,
    _task_constraint_validation,
    build_analysis_graph,
)
from app.agent.intent import build_intent_plan
from app.db.models import AuditLog
from app.gateway.cost_guard import ExplainCheckResult
from app.gateway.executor import ExecutionResult
from app.gateway.sql_validator import SqlCheckResult


def _context():
    return {
        "question": "分析工序良率",
        "source": {"id": 1, "name": "测试数据源", "type": "mysql"},
        "metrics": [{"id": "M-004", "name": "工序良率"}],
        "tables": [{"name": "mes_process_output", "fields": [{"name": "good_qty"}]}],
        "relations": [],
        "businessRules": [],
        "executionPolicy": {"allowedTables": ["mes_process_output"]},
        "coverage": {"status": "ready"},
    }


def _deps(generate_text=None, validate=None, execute=None, explain=None):
    return GraphDependencies(
        retrieve_context=lambda _question, _source_id: _context(),
        generate_text=generate_text or (
            lambda _state, step, _system, _user: (
                "SELECT SUM(good_qty) AS good_qty FROM mes_process_output"
                if step == "query.generate"
                else "工序合格数量已完成统计，结果为 90。"
            )
        ),
        validate_sql=validate or (
            lambda _sql, _allowed: SqlCheckResult(ok=True, tables=["mes_process_output"], checks=["只读通过"])
        ),
        execute_sql=execute or (
            lambda _sql, _source_id: ExecutionResult(
                ok=True,
                columns=["good_qty"],
                rows=[[90]],
                row_count=1,
                checks=["只读通过"],
                tables=["mes_process_output"],
            )
        ),
        explain_sql=explain,
    )


def test_langgraph_full_readonly_path():
    graph = build_analysis_graph(_deps(), checkpointer=InMemorySaver())
    result = graph.invoke(
        {
            "session_id": "ses-1",
            "run_id": "run-1",
            "source_id": 1,
            "user_question": "分析工序良率",
            "retry_count": 0,
            "attempted_sqls": [],
        },
        config={"configurable": {"thread_id": "ses-1", "checkpoint_ns": "run-1"}},
    )
    assert result["blocked"] is False
    assert result["validation"]["ok"] is True
    assert result["execution"]["row_count"] == 1
    assert "90" in result["conclusion"]
    assert result["confidence"]["level"] == "high"
    assert result["metric_resolution"]["source"] == "published"


def test_enabled_governed_skill_is_injected_without_changing_execution_allowlist():
    prompts = []
    base = _deps(generate_text=lambda _state, step, _system, user: (
        prompts.append((step, user)) or
        ("SELECT SUM(good_qty) AS good_qty FROM mes_process_output" if step == "query.generate" else "结果为 90。")
    ))
    deps = GraphDependencies(
        retrieve_context=base.retrieve_context,
        generate_text=base.generate_text,
        validate_sql=base.validate_sql,
        execute_sql=base.execute_sql,
        retrieve_skills=lambda _question, _source_id: {
            "enabled": True,
            "variant": "on",
            "skills": [{
                "id": "skill-1",
                "name": "良率下降归因",
                "version": 1,
                "preconditions": ["核对时间覆盖"],
                "steps": ["按工序定位变化贡献"],
                "constraints": ["不得扩大白名单"],
            }],
        },
    )
    graph = build_analysis_graph(deps, checkpointer=InMemorySaver())
    result = graph.invoke(
        {
            "session_id": "ses-skill",
            "run_id": "run-skill",
            "source_id": 1,
            "user_question": "分析工序良率",
            "skill_enhancement_enabled": True,
            "retry_count": 0,
            "attempted_sqls": [],
        },
        config={"configurable": {"thread_id": "run-skill"}},
    )

    query_prompt = next(user for step, user in prompts if step == "query.generate")
    assert "良率下降归因" in query_prompt
    assert result["semantic_context"]["executionPolicy"]["allowedTables"] == ["mes_process_output"]


def test_unregistered_metric_is_inferred_and_confidence_is_capped():
    context = _context()
    context["metrics"] = []
    context["coverage"] = {
        "status": "no_metric_match",
        "metricCount": 0,
        "tableCount": 1,
    }

    def generate(_state, step, _system, _user):
        if step == "query.generate":
            return (
                '{"sql":"SELECT AVG(good_qty) AS avg_good_qty FROM mes_process_output",'
                '"derivedMetric":{"name":"平均合格数量","definition":"按当前筛选范围计算每条记录的平均合格数量",'
                '"formula":"AVG(good_qty)","assumptions":["每条记录代表同一统计粒度"],'
                '"confidence":0.61,"confidenceReason":"没有已发布指标口径，仅依据字段语义推导"}}'
            )
        return "平均合格数量已完成统计，结果为 90。"

    base = _deps(generate_text=generate)
    deps = GraphDependencies(
        retrieve_context=lambda _question, _source_id: deepcopy(context),
        generate_text=base.generate_text,
        validate_sql=base.validate_sql,
        execute_sql=base.execute_sql,
    )
    graph = build_analysis_graph(deps, checkpointer=InMemorySaver())
    result = graph.invoke(
        {
            "session_id": "ses-inferred",
            "run_id": "run-inferred",
            "source_id": 1,
            "user_question": "分析平均合格数量",
            "retry_count": 0,
            "attempted_sqls": [],
        },
        config={"configurable": {"thread_id": "run-inferred"}},
    )

    assert result["metric_resolution"]["source"] == "agent_inferred"
    assert result["metric_resolution"]["temporary"] is True
    assert result["metric_resolution"]["formula"] == "AVG(good_qty)"
    assert result["confidence"]["score"] <= 78
    assert result["confidence"]["cappedByInferredMetric"] is True
    assert "仅对本次分析有效" in result["conclusion"]


def test_period_comparison_with_one_month_is_low_confidence():
    confidence = _confidence_assessment(
        {
            "task": {
                "analysisType": "comparison",
                "normalizedQuestion": "这个月和上个月的良率比较",
            },
            "metric_resolution": {
                "source": "published",
                "modelConfidence": 0.96,
                "reason": "企业已发布口径",
            },
            "semantic_context": {
                "executionPolicy": {
                    "allowedTables": ["mes_process_output"],
                    "allowedColumns": {"mes_process_output": ["stat_date", "good_qty", "input_qty"]},
                }
            },
            "validation": {"ok": True, "taskComplianceScore": 100},
            "execution": {
                "ok": True,
                "columns": ["month", "yield_rate"],
                "rows": [["2026-06", 97.57]],
                "row_count": 1,
            },
        }
    )

    assert confidence["score"] <= 60
    assert confidence["level"] == "low"
    assert confidence["components"]["resultCompleteness"] == 35
    assert confidence["cappedByIncompleteResult"] is True
    assert "仅覆盖 1 个周期" in confidence["completenessIssues"][0]


def test_period_comparison_with_two_months_keeps_full_completeness():
    confidence = _confidence_assessment(
        {
            "task": {
                "analysisType": "comparison",
                "normalizedQuestion": "这个月和上个月的良率比较",
            },
            "metric_resolution": {
                "source": "published",
                "modelConfidence": 0.96,
                "reason": "企业已发布口径",
            },
            "semantic_context": {
                "executionPolicy": {
                    "allowedTables": ["mes_process_output"],
                    "allowedColumns": {"mes_process_output": ["stat_date", "good_qty", "input_qty"]},
                }
            },
            "validation": {"ok": True, "taskComplianceScore": 100},
            "execution": {
                "ok": True,
                "columns": ["month", "yield_rate"],
                "rows": [["2026-06", 97.57], ["2026-07", 97.54]],
                "row_count": 2,
            },
        }
    )

    assert confidence["components"]["resultCompleteness"] == 100
    assert confidence["level"] == "high"
    assert confidence["cappedByIncompleteResult"] is False


def test_month_comparison_validation_requires_both_explicit_periods():
    task = build_intent_plan("这个月和上个月的良率比较")["tasks"][0]
    periods = task["timeConstraint"]["periods"]
    current = next(item for item in periods if item["key"] == "current")
    previous = next(item for item in periods if item["key"] == "previous")
    context = {
        "tables": [{
            "name": "mes_process_output",
            "fields": [{"name": "stat_date", "semanticName": "统计日期", "reasons": ["指标时间字段"]}],
        }]
    }
    previous_only_sql = (
        "SELECT DATE_FORMAT(stat_date, '%Y-%m') AS month, SUM(good_qty) "
        "FROM mes_process_output "
        f"WHERE stat_date >= '{previous['startInclusive']}' "
        f"AND stat_date < '{current['startInclusive']}' "
        "GROUP BY DATE_FORMAT(stat_date, '%Y-%m')"
    )
    complete_sql = (
        "SELECT CASE "
        f"WHEN stat_date >= '{current['startInclusive']}' AND stat_date < '{current['endExclusive']}' "
        "THEN 'current' ELSE 'previous' END AS period_label, SUM(good_qty) "
        "FROM mes_process_output "
        f"WHERE stat_date >= '{previous['startInclusive']}' "
        f"AND stat_date < '{current['endExclusive']}' "
        "GROUP BY period_label"
    )

    wrong_ok, _, wrong_reason, _ = _task_constraint_validation(previous_only_sql, task, context)
    complete_ok, _, _, complete_score = _task_constraint_validation(complete_sql, task, context)

    assert wrong_ok is False
    assert "未覆盖完整双周期边界" in wrong_reason
    assert complete_ok is True
    assert complete_score == 100


def test_smalltalk_finishes_without_retrieval_sql_or_model_calls():
    calls = []
    base = _deps(generate_text=lambda *_args: calls.append("llm") or "不应调用")
    deps = GraphDependencies(
        retrieve_context=lambda *_args: calls.append("retrieval") or _context(),
        generate_text=base.generate_text,
        validate_sql=lambda *_args: calls.append("validation") or SqlCheckResult(ok=True),
        execute_sql=lambda *_args: calls.append("execution") or ExecutionResult(ok=True),
    )
    graph = build_analysis_graph(deps, checkpointer=InMemorySaver())
    result = graph.invoke(
        {
            "session_id": "ses-smalltalk",
            "run_id": "run-smalltalk",
            "source_id": 1,
            "user_question": "你好",
            "retry_count": 0,
            "attempted_sqls": [],
        },
        config={"configurable": {"thread_id": "run-smalltalk"}},
    )

    assert result["intent_kind"] == "conversation"
    assert "ForgeSight" in result["direct_response"]
    assert result.get("tasks", []) == []
    assert calls == []


def test_runner_persists_smalltalk_as_a_plain_assistant_message(analysis_session):
    calls = []
    deps = _deps(generate_text=lambda *_args: calls.append(True) or "不应调用")
    created = service.create_session("闲聊路由测试", 1, "u-smalltalk")
    run_id = service.create_run(created["id"], "你好", "smalltalk-idem")["runId"]

    runner.execute_run(run_id, checkpointer=InMemorySaver(), dependencies=deps)

    snapshot = service.get_run(run_id)
    assert snapshot["status"] == "succeeded"
    assert snapshot["tasks"] == []
    assert [step["stepKey"] for step in snapshot["steps"]] == ["intent.route"]
    assert "ForgeSight" in snapshot["assistantMessage"]["content"]
    assert calls == []


def test_known_role_question_is_completed_before_the_worker_poll(analysis_session):
    created = service.create_session("角色定位测试", 1, "u-role")

    accepted = service.create_run(created["id"], "你有什么能力，应该怎么使用？", "role-idem")
    snapshot = service.get_run(accepted["runId"])

    assert accepted["status"] == "succeeded"
    assert snapshot["status"] == "succeeded"
    assert snapshot["usage"]["callCount"] == 0
    assert "ForgeSight" in snapshot["assistantMessage"]["content"]
    assert "只读" in snapshot["assistantMessage"]["content"]


def test_clock_utility_completes_inline_without_llm_or_sql(analysis_session):
    created = service.create_session("系统工具测试", 1, "u-utility")

    accepted = service.create_run(created["id"], "现在几点？", "utility-idem")
    snapshot = service.get_run(accepted["runId"])

    assert accepted["status"] == "succeeded"
    assert snapshot["status"] == "succeeded"
    assert snapshot["tasks"] == []
    assert snapshot["usage"]["callCount"] == 0
    assert [step["stepKey"] for step in snapshot["steps"]] == ["system.utility"]
    assert "Asia/Shanghai" in snapshot["assistantMessage"]["content"]


def test_scope_utility_reads_the_session_data_source(analysis_session):
    created = service.create_session("范围工具测试", 1, "u-utility-scope")

    accepted = service.create_run(created["id"], "当前使用哪个数据源？", "utility-scope-idem")
    snapshot = service.get_run(accepted["runId"])

    assert accepted["status"] == "succeeded"
    assert snapshot["usage"]["callCount"] == 0
    assert "阶段5测试数据源" in snapshot["assistantMessage"]["content"]
    assert "phase5_test" in snapshot["assistantMessage"]["content"]


def test_chat_report_request_creates_report_with_session_scope(analysis_session, monkeypatch):
    from app.reports import service as report_service

    captured = {}

    def fake_create_report(payload):
        captured["payload"] = payload
        return {
            "id": "rpt-chat-created",
            "title": "聊天生成的制造运营综合周报",
            "status": "queued",
            "periodStart": "2026-08-09T00:00:00",
            "periodEnd": "2026-08-16T23:59:59",
        }

    monkeypatch.setattr(report_service, "create_report", fake_create_report)
    created = service.create_session("报告创建测试", 1, "u-report-create")

    accepted = service.create_run(created["id"], "给我一份周报", "report-create")
    pending = service.get_run(accepted["runId"])
    resumed = service.respond_to_interaction(
        pending["interaction"]["id"], "tpl-operations-weekly"
    )
    snapshot = service.get_run(accepted["runId"])

    assert accepted["status"] == "waiting_user"
    assert resumed["status"] == "succeeded"
    assert snapshot["status"] == "succeeded"
    assert captured["payload"].templateId == "tpl-operations-weekly"
    assert captured["payload"].sourceId == 1
    assert captured["payload"].ownerId == "u-report-create"
    assert "已创建" in snapshot["assistantMessage"]["content"]
    assert "rpt-chat-created" in snapshot["assistantMessage"]["content"]


def test_chat_report_request_first_asks_for_report_type(analysis_session, monkeypatch):
    from app.reports import service as report_service

    created = []
    monkeypatch.setattr(report_service, "create_report", lambda _payload: created.append(True))
    session = service.create_session("报告类型澄清测试", 1, "u-report-clarify")

    accepted = service.create_run(session["id"], "帮我生成一份报告", "report-clarify")
    snapshot = service.get_run(accepted["runId"])

    assert accepted["status"] == "waiting_user"
    assert snapshot["status"] == "waiting_user"
    assert snapshot["interaction"]["resumeNode"] == "report.create"
    assert "哪一种周报" in snapshot["interaction"]["prompt"]["question"]
    assert {item["value"] for item in snapshot["interaction"]["prompt"]["options"]} >= {
        "tpl-operations-weekly",
        "tpl-production-weekly",
        "tpl-quality-weekly",
    }
    assert created == []


def test_report_type_selection_creates_the_requested_report(analysis_session, monkeypatch):
    from app.reports import service as report_service

    captured = {}

    def fake_create_report(payload):
        captured["payload"] = payload
        return {
            "id": "rpt-quality-chat",
            "title": "质量周报",
            "status": "queued",
        }

    monkeypatch.setattr(report_service, "create_report", fake_create_report)
    session = service.create_session("报告选择测试", 1, "u-report-selection")
    accepted = service.create_run(session["id"], "帮我生成一份报告", "report-selection")
    pending = service.get_run(accepted["runId"])

    resumed = service.respond_to_interaction(
        pending["interaction"]["id"], "tpl-quality-weekly"
    )
    snapshot = service.get_run(accepted["runId"])

    assert resumed["status"] == "succeeded"
    assert snapshot["status"] == "succeeded"
    assert snapshot["interaction"]["status"] == "resolved"
    assert captured["payload"].templateId == "tpl-quality-weekly"
    assert captured["payload"].ownerId == "u-report-selection"
    assert "质量周报" in snapshot["assistantMessage"]["content"]


def test_free_conversation_completes_inline_with_one_small_model_call(analysis_session, monkeypatch):
    from app.llm.provider import LLMResponse, LLMUsage

    model_calls = []

    def fake_completion(system, user, *, temperature, max_tokens):
        model_calls.append((system, user, temperature, max_tokens))
        return LLMResponse(
            content=(
                '{"segments":[{"index":0,"kind":"conversation",'
                '"response":"我在。你可以从最困扰你的那件事开始说。"}]}'
            ),
            model="test-chat",
            provider_request_id="req-chat",
            usage=LLMUsage(input_tokens=20, output_tokens=12, total_tokens=32),
        )

    monkeypatch.setattr(runner.provider, "chat_completion", fake_completion)
    created = service.create_session("正常对话测试", 1, "u-conversation")

    accepted = service.create_run(created["id"], "你对团队协作有什么看法？", "chat-idem")
    snapshot = service.get_run(accepted["runId"])

    assert accepted["status"] == "succeeded"
    assert snapshot["status"] == "succeeded"
    assert snapshot["tasks"] == []
    assert snapshot["usage"]["callCount"] == 1
    assert snapshot["assistantMessage"]["content"].startswith("我在")
    assert model_calls[0][2:] == (0.1, 500)
    assert "ForgeSight" in model_calls[0][0]


def test_unknown_business_request_is_preclassified_once_then_queued(analysis_session, monkeypatch):
    from app.llm.provider import LLMResponse, LLMUsage

    calls = []
    lease_windows = []

    def fake_completion(system, user, *, temperature, max_tokens):
        calls.append((system, user, max_tokens))
        with analysis_session() as session:
            active = session.query(runner.AnalysisRun).filter_by(status="running").one()
            lease_windows.append((active.lease_expires_at - active.started_at).total_seconds())
        return LLMResponse(
            content='{"segments":[{"index":0,"kind":"analysis","response":""}]}',
            model="test-router", provider_request_id="req-route",
            usage=LLMUsage(input_tokens=18, output_tokens=8, total_tokens=26),
        )

    monkeypatch.setattr(runner.provider, "chat_completion", fake_completion)
    created = service.create_session("自然业务问法", 1, "u-natural-business")

    accepted = service.create_run(created["id"], "上周每条线做了几件？", "natural-business")

    assert accepted["status"] == "queued"
    assert len(calls) == 1
    assert calls[0][2] == 500
    assert lease_windows[0] >= 75
    with analysis_session() as session:
        run = session.query(runner.AnalysisRun).filter_by(id=accepted["runId"]).one()
        message = session.query(runner.AnalysisMessage).filter_by(id=run.trigger_message_id).one()
        assert message.metadata_json["routeLabels"] == {"0": "analysis"}


def test_router_understands_natural_report_request_without_report_keyword(analysis_session, monkeypatch):
    from app.llm.provider import LLMResponse, LLMUsage

    def fake_completion(system, user, *, temperature, max_tokens):
        assert "report" in system.lower()
        return LLMResponse(
            content='{"segments":[{"index":0,"kind":"report","response":""}]}',
            model="test-router",
            provider_request_id="req-report-route",
            usage=LLMUsage(input_tokens=18, output_tokens=8, total_tokens=26),
        )

    monkeypatch.setattr(runner.provider, "chat_completion", fake_completion)
    created = service.create_session("自然报告问法", 1, "u-natural-report")

    accepted = service.create_run(
        created["id"],
        "帮我把最近一周的经营情况整理成一份总结",
        "natural-report",
    )
    snapshot = service.get_run(accepted["runId"])

    assert accepted["status"] == "waiting_user"
    assert snapshot["status"] == "waiting_user"
    assert "哪一种周报" in snapshot["interaction"]["prompt"]["question"]


def test_inline_conversation_cannot_complete_twice(analysis_session):
    created = service.create_session("幂等对话测试", 1, "u-chat-idempotent")
    accepted = service.create_run(created["id"], "你是谁？", "chat-once")

    runner.execute_inline_conversation(accepted["runId"], "不应重复写入")

    with analysis_session() as session:
        messages = (
            session.query(runner.AnalysisMessage)
            .filter_by(session_id=created["id"], role="assistant")
            .all()
        )
        assert len(messages) == 1
        assert "不应重复写入" not in messages[0].content


def test_business_followup_still_uses_the_analysis_worker(analysis_session):
    created = service.create_session("业务追问测试", 1, "u-followup")
    service.create_run(created["id"], "统计最近 7 天各产线产量", "analysis-first")

    accepted = service.create_run(created["id"], "继续看看它最近怎么样", "analysis-followup")

    assert accepted["status"] == "queued"


def test_out_of_scope_question_does_not_trigger_metric_clarification():
    calls = []
    graph = build_analysis_graph(
        GraphDependencies(
            retrieve_context=lambda *_args: calls.append("retrieval") or _context(),
            generate_text=lambda *_args: calls.append("llm") or "{}",
            validate_sql=lambda *_args: calls.append("validation") or SqlCheckResult(ok=True),
            execute_sql=lambda *_args: calls.append("execution") or ExecutionResult(ok=True),
        ),
        checkpointer=InMemorySaver(),
    )

    result = graph.invoke(
        {
            "session_id": "ses-weather",
            "run_id": "run-weather",
            "source_id": 1,
            "user_question": "明年下雨吗",
            "retry_count": 0,
            "attempted_sqls": [],
        },
        config={"configurable": {"thread_id": "run-weather"}},
    )

    assert result["intent_kind"] == "conversation"
    assert result["analysis_question"] == ""
    assert "不属于当前企业数据底座" in result["direct_response"]
    assert calls == []


def test_no_match_returns_business_diagnosis_and_checks_product_entity(analysis_session):
    context = _context()
    context["metrics"] = []
    context["tables"] = []
    context["executionPolicy"] = {"allowedTables": [], "allowedColumns": {}}
    context["coverage"] = {"status": "no_match", "metricCount": 0, "tableCount": 0}
    context["diagnostics"] = {
        "requestedCapability": {
            "code": "sales",
            "label": "销售/出库",
            "matchedTerms": ["卖出"],
            "available": False,
        },
        "availableTopics": ["主数据", "生产", "质量", "设备", "库存"],
        "entityLookup": {
            "kind": "product",
            "label": "产品",
            "value": "泰罗奥特曼",
            "table": "dim_product",
            "idField": "product_id",
            "nameField": "product_name",
            "codeField": "product_code",
        },
    }
    entity_queries = []
    created = service.create_session("无匹配业务诊断", 1, "u-no-match")
    run_id = service.create_run(
        created["id"],
        "这个月泰罗奥特曼一共卖出去多少",
        "no-match-diagnosis",
    )["runId"]
    deps = GraphDependencies(
        retrieve_context=lambda _question, _source_id: deepcopy(context),
        generate_text=lambda *_args: (_ for _ in ()).throw(AssertionError("不应调用大模型")),
        validate_sql=lambda sql, allowed: (
            SqlCheckResult(ok=True, tables=["dim_product"], checks=["只读通过"])
            if allowed == {"dim_product"} and "FROM dim_product" in sql
            else SqlCheckResult(ok=False, reason="诊断查询越界")
        ),
        execute_sql=lambda sql, _source_id: entity_queries.append(sql) or ExecutionResult(
            ok=True,
            columns=["product_id", "product_code", "product_name", "match_type"],
            rows=[],
            row_count=0,
            tables=["dim_product"],
            checks=["只读通过"],
        ),
    )

    runner.execute_run(run_id, checkpointer=InMemorySaver(), dependencies=deps)
    snapshot = service.get_run(run_id)

    assert snapshot["status"] == "failed"
    assert snapshot["errorCode"] == "CONTEXT_NO_MATCH"
    assert snapshot["error"]["showCodeToUser"] is False
    assert "未接入销售/出库数据" in snapshot["error"]["userSafeMessage"]
    assert "未找到“泰罗奥特曼”" in snapshot["error"]["userSafeMessage"]
    assert "生产了多少" in snapshot["error"]["suggestion"]
    assert len(entity_queries) == 1
    context_artifact = next(item for item in snapshot["artifacts"] if item["type"] == "context")
    assert context_artifact["content"]["diagnostics"]["entityCheck"]["exists"] is False


def test_mixed_conversation_and_analysis_executes_only_analysis_segment():
    retrieval_questions = []
    base = _deps()
    graph = build_analysis_graph(
        GraphDependencies(
            retrieve_context=lambda question, _source_id: retrieval_questions.append(question) or _context(),
            generate_text=base.generate_text,
            validate_sql=base.validate_sql,
            execute_sql=base.execute_sql,
        ),
        checkpointer=InMemorySaver(),
    )

    result = graph.invoke(
        {
            "session_id": "ses-mixed",
            "run_id": "run-mixed",
            "source_id": 1,
            "user_question": "你好；分析工序良率；谢谢你",
            "retry_count": 0,
            "attempted_sqls": [],
        },
        config={"configurable": {"thread_id": "run-mixed"}},
    )

    assert result["intent_kind"] == "mixed"
    assert result["analysis_question"] == "分析工序良率"
    assert result["direct_response"] == "你好。"
    assert retrieval_questions == ["分析工序良率"]
    assert result["execution"]["row_count"] == 1


def test_duplicate_failed_sql_stops_retry():
    graph = build_analysis_graph(
        _deps(validate=lambda _sql, _allowed: SqlCheckResult(ok=False, reason="字段不存在")),
        checkpointer=InMemorySaver(),
    )
    result = graph.invoke(
        {
            "session_id": "ses-2",
            "run_id": "run-2",
            "source_id": 1,
            "user_question": "分析工序良率",
            "retry_count": 0,
            "attempted_sqls": [],
        },
        config={"configurable": {"thread_id": "ses-2", "checkpoint_ns": "run-2"}},
    )
    assert result["retry_count"] == 1
    assert result["error"]["code"] == "RETRY_NO_PROGRESS"


def test_model_connection_failure_is_not_reported_as_sql_failure():
    def unavailable(*_args):
        raise RuntimeError("Invalid port")

    graph = build_analysis_graph(_deps(generate_text=unavailable), checkpointer=InMemorySaver())
    result = graph.invoke(
        {
            "session_id": "ses-model-down",
            "run_id": "run-model-down",
            "source_id": 1,
            "user_question": "分析工序良率",
            "retry_count": 0,
            "attempted_sqls": [],
        },
        config={"configurable": {"thread_id": "run-model-down"}},
    )

    assert result["error"]["code"] == "MODEL_SERVICE_UNAVAILABLE"
    assert "模型服务连接失败" in result["error"]["userSafeMessage"]


def test_explain_cost_gate_retries_before_execution():
    generated = iter(
        [
            "SELECT SUM(good_qty) AS good_qty FROM mes_process_output",
            "SELECT SUM(good_qty) AS good_qty FROM mes_process_output WHERE good_qty >= 0",
            "SELECT SUM(good_qty) AS good_qty FROM mes_process_output WHERE good_qty > 0",
        ]
    )
    executed = []

    def explain(sql, _source_id):
        allowed = "good_qty > 0" in sql
        return ExplainCheckResult(
            ok=True,
            allowed=allowed,
            dialect="mysql",
            estimated_rows=100 if allowed else 900_000,
            estimated_cost=10 if allowed else 200_000,
            reason="" if allowed else "预计扫描行数超过安全阈值",
            checks=["成本门控通过" if allowed else "成本门控已阻断"],
        )

    graph = build_analysis_graph(
        _deps(
            generate_text=lambda _state, step, _system, _user: (
                next(generated) if step == "query.generate" else "已完成统计。"
            ),
            explain=explain,
            execute=lambda sql, _source_id: executed.append(sql) or ExecutionResult(
                ok=True, columns=["good_qty"], rows=[[90]], row_count=1
            ),
        ),
        checkpointer=InMemorySaver(),
    )
    result = graph.invoke(
        {
            "session_id": "ses-cost",
            "run_id": "run-cost",
            "source_id": 1,
            "user_question": "分析工序良率",
            "retry_count": 0,
            "attempted_sqls": [],
        },
        config={"configurable": {"thread_id": "run-cost"}},
    )

    assert result["retry_count"] == 2
    assert result["validation"]["costGate"]["allowed"] is True
    assert len(executed) == 1


def test_mysql_dialect_mismatch_is_explained_and_repaired_before_explain():
    generated = []
    executed = []

    def generate(_state, step, system, user):
        if step != "query.generate":
            return "已完成统计。"
        generated.append({"system": system, "user": user})
        if len(generated) == 1:
            return (
                "WITH current_month AS (SELECT SUM(good_qty) AS qty FROM mes_process_output), "
                "previous_month AS (SELECT SUM(good_qty) AS qty FROM mes_process_output) "
                "SELECT * FROM current_month FULL OUTER JOIN previous_month ON 1 = 1"
            )
        assert "SQL_DIALECT_MISMATCH" in user
        return "SELECT SUM(good_qty) AS good_qty FROM mes_process_output"

    graph = build_analysis_graph(
        _deps(
            generate_text=generate,
            execute=lambda sql, _source_id: executed.append(sql) or ExecutionResult(
                ok=True,
                columns=["good_qty"],
                rows=[[90]],
                row_count=1,
            ),
        ),
        checkpointer=InMemorySaver(),
    )
    result = graph.invoke(
        {
            "session_id": "ses-dialect-repair",
            "run_id": "run-dialect-repair",
            "source_id": 1,
            "user_question": "分析工序良率",
            "retry_count": 0,
            "attempted_sqls": [],
        },
        config={"configurable": {"thread_id": "run-dialect-repair"}},
    )

    assert len(generated) == 2
    assert "目标数据库为 MySQL" in generated[0]["system"]
    assert result["retry_count"] == 1
    assert result["error"] is None
    assert len(executed) == 1


def test_runner_projects_steps_events_artifacts_and_message(analysis_session):
    created = service.create_session("Agent 集成测试", 1, "u-runner")
    run_id = service.create_run(created["id"], "分析工序良率", "runner-idem")["runId"]
    runner.execute_run(run_id, checkpointer=InMemorySaver(), dependencies=_deps())

    snapshot = service.get_run(run_id)
    assert snapshot["status"] == "succeeded"
    assert len(snapshot["tasks"]) == 1
    assert [step["stepKey"] for step in snapshot["steps"]] == [
        "intent.route",
            "intent.guard",
            "intent.parse",
            "memory.retrieve",
            "skill.retrieve",
            "context.retrieve",
        "query.generate",
        "query.validate",
        "query.execute",
        "result.explain",
    ]
    events = service.list_events(run_id)
    assert events[-1]["type"] == "run.completed"
    session_view = service.get_session(created["id"])
    assert session_view["messages"][-1]["role"] == "assistant"
    assert "90" in session_view["messages"][-1]["content"]


def test_runner_recovers_finished_checkpoint_without_replaying_llm(analysis_session):
    saver = InMemorySaver()
    calls = []

    def generate(_state, step, _system, _user):
        calls.append(step)
        return (
            "SELECT SUM(good_qty) AS good_qty FROM mes_process_output"
            if step == "query.generate"
            else "恢复后的结论。"
        )

    deps = _deps(generate_text=generate)
    created = service.create_session("恢复测试", 1, "u-recover")
    run_id = service.create_run(created["id"], "分析工序良率", "recover-idem")["runId"]
    graph = build_analysis_graph(deps, checkpointer=saver)
    graph.invoke(
        {
            "session_id": created["id"],
            "run_id": run_id,
            "source_id": 1,
            "user_question": "分析工序良率",
            "conversation_context": [],
            "retry_count": 0,
            "attempted_sqls": [],
        },
        config={"configurable": {"thread_id": run_id}},
    )
    calls_before_recovery = list(calls)

    recovered = runner.execute_run(run_id, checkpointer=saver, dependencies=deps)

    assert recovered == {"runId": run_id, "status": "succeeded", "recovered": True}
    assert calls == calls_before_recovery
    assert service.get_run(run_id)["status"] == "succeeded"
    assert "run.recovered" in [event["type"] for event in service.list_events(run_id)]


def test_write_intent_is_blocked_before_llm(analysis_session):
    calls = []
    deps = _deps(generate_text=lambda *_args: calls.append(True) or "SELECT 1")
    created = service.create_session("安全测试", 1, "u-runner")
    run_id = service.create_run(created["id"], "删除异常记录后重新统计", "block-idem")["runId"]
    runner.execute_run(run_id, checkpointer=InMemorySaver(), dependencies=deps)

    assert service.get_run(run_id)["status"] == "blocked"
    assert calls == []
    with analysis_session() as session:
        audit = session.query(AuditLog).filter_by(action="Agent 写操作意图拦截").one()
        assert audit.result == "已拦截"


def test_compound_question_is_split_into_traceable_tasks(analysis_session):
    created = service.create_session("复合问题测试", 1, "u-multi")
    run_id = service.create_run(
        created["id"],
        "统计最近 7 天工序良率趋势；找出停机时间最高的设备",
        "multi-idem",
    )["runId"]
    runner.execute_run(run_id, checkpointer=InMemorySaver(), dependencies=_deps())

    snapshot = service.get_run(run_id)
    assert snapshot["status"] == "succeeded"
    assert len(snapshot["tasks"]) == 2
    assert [item["analysisType"] for item in snapshot["tasks"]] == ["trend", "ranking"]
    assert all(item["status"] == "succeeded" for item in snapshot["tasks"])


def test_long_conversation_history_does_not_overflow_retrieval_question():
    retrieval_questions = []
    base = _deps()
    deps = GraphDependencies(
        retrieve_context=lambda question, _source_id: retrieval_questions.append(question) or _context(),
        generate_text=base.generate_text,
        validate_sql=base.validate_sql,
        execute_sql=base.execute_sql,
    )
    graph = build_analysis_graph(deps, checkpointer=InMemorySaver())
    current_question = "找出最近一个月不良数量最高的产品"
    result = graph.invoke(
        {
            "session_id": "ses-long-history",
            "run_id": "run-long-history",
            "source_id": 1,
            "user_question": current_question,
            "conversation_context": [
                {"role": "user", "content": "统计每条产线最近 7 天的产量趋势"},
                {"role": "assistant", "content": "很长的分析结果" * 800},
                {"role": "system", "content": "分支检查点" * 800},
            ],
            "retry_count": 0,
            "attempted_sqls": [],
        },
        config={"configurable": {"thread_id": "run-long-history"}},
    )

    assert result["error"] is None
    assert len(retrieval_questions) == 1
    assert len(retrieval_questions[0]) <= 1000
    assert retrieval_questions[0] == current_question
    assert "统计每条产线最近 7 天的产量趋势" not in retrieval_questions[0]
    assert "很长的分析结果" not in retrieval_questions[0]
    assert "分支检查点" not in retrieval_questions[0]


def test_real_follow_up_keeps_bounded_user_history_for_retrieval():
    retrieval_questions = []
    base = _deps()
    graph = build_analysis_graph(
        GraphDependencies(
            retrieve_context=lambda question, _source_id: retrieval_questions.append(question) or _context(),
            generate_text=base.generate_text,
            validate_sql=base.validate_sql,
            execute_sql=base.execute_sql,
        ),
        checkpointer=InMemorySaver(),
    )

    result = graph.invoke(
        {
            "session_id": "ses-follow-up",
            "run_id": "run-follow-up",
            "source_id": 1,
            "user_question": "再看它最近 7 天的趋势",
            "conversation_context": [
                {"role": "user", "content": "查看设备停机时长"},
                {"role": "assistant", "content": "已完成分析"},
            ],
            "retry_count": 0,
            "attempted_sqls": [],
        },
        config={"configurable": {"thread_id": "run-follow-up"}},
    )

    assert result["error"] is None
    assert "前序问题：查看设备停机时长" in retrieval_questions[0]
    assert "当前问题：" in retrieval_questions[0]


def test_task_constraint_gate_repairs_missing_month_filter_and_wrong_dimension():
    context = _context()
    context["question"] = "使用折线图展示这个月的不良品数量"
    context["metrics"] = [{"id": "M-DEFECT", "name": "不良品数量"}]
    context["tables"] = [{
        "name": "mes_process_output",
        "fields": [
            {"name": "stat_date", "type": "date", "description": "统计日期"},
            {"name": "defect_qty", "type": "int", "description": "不良数量"},
            {"name": "product_id", "type": "varchar", "description": "产品编号"},
        ],
    }]
    context["executionPolicy"] = {"allowedTables": ["mes_process_output"]}
    generated_sql = []

    def generate(_state, step, _system, user):
        if step != "query.generate":
            return "本月每日不良品数量已完成统计。"
        if not generated_sql:
            sql = (
                "SELECT product_id, DATE_FORMAT(stat_date, '%Y-%m') AS month, "
                "SUM(defect_qty) AS defect_qty FROM mes_process_output "
                "GROUP BY product_id, DATE_FORMAT(stat_date, '%Y-%m')"
            )
        else:
            assert "TASK_CONSTRAINT_MISMATCH" in user
            sql = (
                "SELECT stat_date, SUM(defect_qty) AS defect_qty FROM mes_process_output "
                "WHERE stat_date >= DATE_FORMAT(CURDATE(), '%Y-%m-01') "
                "AND stat_date < DATE_ADD(DATE_FORMAT(CURDATE(), '%Y-%m-01'), INTERVAL 1 MONTH) "
                "GROUP BY stat_date ORDER BY stat_date"
            )
        generated_sql.append(sql)
        return sql

    graph = build_analysis_graph(
        GraphDependencies(
            retrieve_context=lambda _question, _source_id: deepcopy(context),
            generate_text=generate,
            validate_sql=lambda _sql, _allowed: SqlCheckResult(
                ok=True,
                tables=["mes_process_output"],
                checks=["只读通过"],
            ),
            execute_sql=lambda _sql, _source_id: ExecutionResult(
                ok=True,
                columns=["stat_date", "defect_qty"],
                rows=[["2026-07-01", 21], ["2026-07-02", 18]],
                row_count=2,
            ),
        ),
        checkpointer=InMemorySaver(),
    )

    result = graph.invoke(
        {
            "session_id": "ses-task-constraint",
            "run_id": "run-task-constraint",
            "source_id": 1,
            "user_question": "使用折线图展示这个月的不良品数量",
            "conversation_context": [{"role": "user", "content": "查看不良数量最高的 4 个产品"}],
            "retry_count": 0,
            "attempted_sqls": [],
        },
        config={"configurable": {"thread_id": "run-task-constraint"}},
    )

    assert len(generated_sql) == 2
    assert result["retry_count"] == 1
    assert "WHERE stat_date" in result["sql"]
    assert result["validation"]["taskComplianceScore"] == 100
    assert "任务约束校验通过" in result["validation"]["checks"]


def test_clarification_resumes_same_run_and_checkpoint_can_branch(analysis_session):
    saver = InMemorySaver()
    ambiguous = _context()
    ambiguous["metrics"] = [
        {"id": "M-001", "name": "一次合格率"},
        {"id": "M-002", "name": "最终合格率"},
    ]
    ambiguous["coverage"] = {"status": "needs_clarification"}
    deps = _deps()
    deps = GraphDependencies(
        retrieve_context=lambda _question, _source_id: deepcopy(ambiguous),
        generate_text=deps.generate_text,
        validate_sql=deps.validate_sql,
        execute_sql=deps.execute_sql,
    )
    created = service.create_session("澄清测试", 1, "u-clarify")
    run_id = service.create_run(created["id"], "分析良率", "clarify-idem")["runId"]

    first = runner.execute_run(run_id, checkpointer=saver, dependencies=deps)
    assert first["status"] == "waiting_user"
    interaction = service.get_run(run_id)["interaction"]
    service.respond_to_interaction(interaction["id"], "M-001")
    second = runner.execute_run(run_id, checkpointer=saver, dependencies=deps)

    assert second["status"] == "succeeded"
    snapshot = service.get_run(run_id)
    assert snapshot["status"] == "succeeded"
    assert snapshot["interaction"] is None
    checkpoint = next(item for item in reversed(snapshot["checkpoints"]) if item["rollbackable"])
    branch = service.create_branch(checkpoint["id"], "u-clarify", "调整口径")
    assert branch["session"]["parentSessionId"] == created["id"]
    assert branch["session"]["forkCheckpointId"] == checkpoint["id"]
    branch_session = service.get_session(branch["session"]["id"])
    assert branch_session["messages"] == []
    assert branch_session["runs"] == []
    assert branch_session["branchContext"]["sourceRunId"] == run_id

    adjusted = service.create_branch(
        checkpoint["id"],
        "u-clarify",
        "良率方案调整",
        instruction="基于原分析，将指标口径调整为最终合格率，并按产线重新分析。",
        adjustments={"metric": "最终合格率", "groupBy": "产线"},
        reuse_artifacts="refresh",
    )
    assert adjusted["run"]["status"] == "queued"
    adjusted_session = service.get_session(adjusted["session"]["id"])
    assert len(adjusted_session["messages"]) == 1
    assert adjusted_session["messages"][0]["metadata"]["branchAdjustment"]["metric"] == "最终合格率"
    assert adjusted_session["branchContext"]["adjustments"]["groupBy"] == "产线"


# ---- 建模提案链路（analysisType=modeling → propose_modeling 确定性节点） ----

def _modeling_context():
    return {
        "question": "识别高不良风险的生产记录",
        "source": {"id": 1, "name": "测试数据源", "type": "mysql"},
        "metrics": [],
        "tables": [
            {
                "name": "mes_process_output",
                "semanticName": "工序产量事实表",
                "fields": [
                    {"name": "stat_date", "semanticName": "统计日期", "dataType": "date", "isPrimary": True},
                    {"name": "工序", "semanticName": "工序", "dataType": "varchar"},
                    {"name": "产线", "semanticName": "产线", "dataType": "varchar"},
                    {"name": "停机时长", "semanticName": "停机时长", "dataType": "decimal"},
                    {"name": "计划数量", "semanticName": "计划数量", "dataType": "decimal"},
                    {"name": "high_defect_risk", "semanticName": "高风险标记", "dataType": "tinyint"},
                ],
            }
        ],
        "relations": [],
        "businessRules": [],
        "executionPolicy": {"allowedTables": ["mes_process_output"]},
        "coverage": {"status": "ready"},
    }


def _modeling_deps(validate_ok=True):
    def execute(sql, _source_id):
        if "COUNT(*)" in sql.upper():
            return ExecutionResult(ok=True, columns=["n"], rows=[[200]], row_count=1, tables=["mes_process_output"])
        return ExecutionResult(ok=True, columns=["工序"], rows=[["SMT贴片"]], row_count=1, tables=["mes_process_output"])

    def validate(sql, _allowed):
        if not validate_ok:
            return SqlCheckResult(ok=False, reason="SQL 包含未注册表 forbidden_table")
        return SqlCheckResult(ok=True, tables=["mes_process_output"], checks=["只读通过"])

    return GraphDependencies(
        retrieve_context=lambda _q, _s: _modeling_context(),
        generate_text=lambda *_args: (_ for _ in ()).throw(AssertionError("建模提案不应调用 LLM")),
        validate_sql=validate,
        execute_sql=execute,
    )


def _invoke_modeling(graph, question):
    return graph.invoke(
        {
            "session_id": "ses-modeling",
            "run_id": "run-modeling",
            "source_id": 1,
            "user_question": question,
            "retry_count": 0,
            "attempted_sqls": [],
        },
        config={"configurable": {"thread_id": "ses-modeling"}},
    )


def test_modeling_question_proposes_classification_task_without_llm():
    graph = build_analysis_graph(_modeling_deps(), checkpointer=InMemorySaver())
    result = _invoke_modeling(graph, "识别高不良风险的生产记录")

    proposal = result["modeling_proposal"]
    assert result["error"] is None
    assert proposal["taskType"] == "classification"
    assert proposal["taskTypeLabel"] == "二分类"
    assert proposal["target"] == "high_defect_risk"
    assert proposal["targetLabel"] == "高风险标记"
    assert proposal["candidates"] == ["logistic", "tree", "forest"]
    assert proposal["metric"] == "AUC / F1"
    assert proposal["sampleCount"] == 200
    # 特征排除主键/日期列；SQL 由白名单字段拼接并带 LIMIT
    assert proposal["features"] == ["工序", "产线", "停机时长", "计划数量"]
    assert "SELECT `工序`, `产线`, `停机时长`, `计划数量`, `high_defect_risk`" in proposal["datasetSql"]
    assert "LIMIT 10000" in proposal["datasetSql"]
    assert proposal["dataset"] == "工序产量事实表"


def test_regression_modeling_proposal_targets_duration():
    graph = build_analysis_graph(_modeling_deps(), checkpointer=InMemorySaver())
    result = _invoke_modeling(graph, "预测设备停机时长")

    proposal = result["modeling_proposal"]
    assert proposal["taskType"] == "regression"
    assert proposal["target"] == "停机时长"
    assert proposal["candidates"] == ["linear", "tree", "forest"]
    assert proposal["metric"] == "R² / MAE"


def test_modeling_proposal_prefers_reusable_published_model():
    base = _modeling_deps()
    deps = GraphDependencies(
        retrieve_context=base.retrieve_context,
        generate_text=base.generate_text,
        validate_sql=base.validate_sql,
        execute_sql=base.execute_sql,
        find_reusable_models=lambda question, source_id, task_type: [{
            "modelId": "mdl-production",
            "taskId": "mt-existing",
            "name": "高不良风险识别模型",
            "version": 3,
            "algorithm": "forest",
            "taskType": task_type,
            "targetField": "high_defect_risk",
            "targetLabel": "高风险标记",
            "metric": "AUC 0.986",
            "matchScore": 0.82,
            "decisionThreshold": 0.42,
        }] if source_id == 1 and "不良风险" in question else [],
    )
    graph = build_analysis_graph(deps, checkpointer=InMemorySaver())
    proposal = _invoke_modeling(graph, "识别高不良风险的生产记录")["modeling_proposal"]
    assert proposal["recommendedAction"] == "reuse"
    assert proposal["reusableModel"]["modelId"] == "mdl-production"


def test_modeling_proposal_sql_must_pass_whitelist():
    graph = build_analysis_graph(_modeling_deps(validate_ok=False), checkpointer=InMemorySaver())
    result = _invoke_modeling(graph, "识别高不良风险的生产记录")

    assert result.get("modeling_proposal") is None
    assert result["error"]["code"] == "MODELING_SQL_REJECTED"


def test_runner_persists_modeling_proposal_run(analysis_session):
    deps = _modeling_deps()
    created = service.create_session("建模提案测试", 1, "u-modeling")
    run_id = service.create_run(created["id"], "识别高不良风险的生产记录", "modeling-idem")["runId"]

    runner.execute_run(run_id, checkpointer=InMemorySaver(), dependencies=deps)

    snapshot = service.get_run(run_id)
    assert snapshot["status"] == "modeling-proposal"
    assert snapshot["proposal"]["taskType"] == "classification"
    assert snapshot["proposal"]["sampleCount"] == 200
    assert "二分类模型" in snapshot["assistantMessage"]["content"]
    step_keys = [step["stepKey"] for step in snapshot["steps"]]
    assert "modeling.propose" in step_keys
    assert "query.generate" not in step_keys
    # 证据链：modeling-proposal artifact 已落库
    assert any(item["type"] == "modeling-proposal" for item in snapshot["artifacts"])
    # 任务状态同步为提案态（非终态，不出现 succeeded）
    assert snapshot["tasks"][0]["status"] == "modeling-proposal"
