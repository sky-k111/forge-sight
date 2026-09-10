"""Phase B/C：意图驱动 planning + 校验 +编译层的回归测试。"""

from __future__ import annotations

import pytest

from app.agent.intent_adapter import intent_to_tasks
from app.agent.intent_contract import AgentIntent
from app.agent.planner import build_fallback_plan, should_use_planner
from app.agent.query_compiler import compile_intent
from app.agent.validators import validate_intent
from app.agent.dialogue import (
    inline_conversation_reply,
    should_treat_as_conversation,
    _looks_referential,
)


def _comparison_intent() -> AgentIntent:
    return AgentIntent.model_validate(
        {
            "kind": "analysis",
            "question": "对比5月与6月各产线良率，找出差异最大的两个工序再下钻",
            "metrics": [{"semantic": "良率", "aggregation": "avg"}],
            "dimensions": [{"semantic": "产线"}, {"semantic": "工序"}],
            "time": {
                "mode": "comparison",
                "grain": "month",
                "label": "5月与6月",
                "comparisons": [
                    {"mode": "absolute", "label": "5月"},
                    {"mode": "absolute", "label": "6月"},
                ],
            },
            "comparison": {"basis": "对比5月与6月各产线良率", "topDiffK": 2, "by": {"semantic": "工序"}},
            "chart": {"type": "bar"},
            "confidence": 0.9,
            "reasons": [],
        }
    )


def _simple_intent() -> AgentIntent:
    return AgentIntent.model_validate(
        {
            "kind": "analysis",
            "question": "输出2026年总产量",
            "metrics": [{"semantic": "产量", "aggregation": "sum"}],
            "dimensions": [],
            "chart": {"type": "bar"},
            "confidence": 0.9,
            "reasons": [],
        }
    )


def test_should_use_planner_intent_paths():
    assert should_use_planner("x", [], enabled=True, agent_intent=_comparison_intent()) == (True, "INTENT_MULTI_TASK")
    assert should_use_planner("x", [], enabled=True, agent_intent=_simple_intent()) == (False, "INTENT_SIMPLE_FAST_PATH")


def test_intent_to_tasks_splits_comparison_and_keeps_process():
    tasks = intent_to_tasks(_comparison_intent())
    assert len(tasks) == 1
    for t in tasks:
        assert {"产线", "工序"} <= set(t["groupBy"])
        assert {p["label"] for p in t["timeConstraint"]["periods"]} == {"5月", "6月"}
    assert tasks[0]["timeConstraint"]["kind"] == "comparison"


def test_build_fallback_plan_uses_intent():
    plan = build_fallback_plan(
        "对比5月与6月各产线良率，找出差异最大的两个工序再下钻",
        [],
        agent_intent=_comparison_intent(),
    )
    query_steps = [s for s in plan.steps if s.capability == "analysis.query_data"]
    assert len(query_steps) == 1
    for s in query_steps:
        task = s.arguments.get("task", {})
        assert set(task.get("groupBy") or []) == {"产线", "工序", "月份"}


def test_validate_intent_resolves_dims_and_metric():
    ctx = {
        "tables": [
            {
                "objectType": "fact",
                "name": "mx_process_output",
                "fields": [
                    {"name": "line_id", "semanticName": "产线"},
                    {"name": "process_id", "semanticName": "工序"},
                ],
            }
        ],
        "source": {"type": "mysql"},
        "metrics": [{"id": "m1", "name": "良率", "physicalFormula": "SUM(a)/SUM(b)", "physicalTimeField": "stat_date", "semanticFormula": "良率"}],
    }
    vr = validate_intent(_comparison_intent(), ctx, question=_comparison_intent().question)
    assert vr.valid and not vr.ask_clarify
    codes = {d["semantic"]: d["code"] for d in vr.resolved["dimensions"]}
    assert codes == {"产线": "line_id", "工序": "process_id"}


def test_validate_intent_clarify_on_unknown_dimension():
    intent = AgentIntent.model_validate(
        {"kind": "analysis", "question": "统计某不存在维度", "dimensions": [{"semantic": "未知维度"}]}
    )
    vr = validate_intent(
        intent,
        {"tables": [{"objectType": "fact", "name": "t", "fields": [{"name": "x", "semanticName": "产线"}]}]},
    )
    assert not vr.valid
    assert vr.ask_clarify


def test_compile_intent_renders_dimension_sql():
    ctx = {
        "tables": [
            {
                "objectType": "fact",
                "name": "mx_process_output",
                "fields": [
                    {"name": "line_id", "semanticName": "产线"},
                    {"name": "process_id", "semanticName": "工序"},
                ],
            }
        ],
        "source": {"type": "mysql"},
        "metrics": [{"id": "m1", "name": "良率", "physicalFormula": "SUM(a)/SUM(b)", "physicalTimeField": "stat_date", "semanticFormula": "良率"}],
    }
    result = compile_intent(_comparison_intent(), ctx, dialect="mysql")
    assert result is not None
    sql, meta = result
    assert "line_id" in sql and "process_id" in sql
    assert meta["groupBy"] == ["产线", "工序", "月份"]


def test_classify_request_kind_promotes_data_signal_when_intent_enabled():
    from app.agent.intent import classify_request_kind, is_deterministic_conversation

    # 数据问句即便句式像常识问答，开启意图时也送往 LLM 意图，而不是被闲聊路径吞掉。
    assert classify_request_kind("你知道最近趋势吗", [], intent_enabled=True) == "analysis"
    assert is_deterministic_conversation("你知道最近趋势吗", intent_enabled=True) is False
    # 纯闲聊/范围外仍走固定路径，零 LLM。
    assert classify_request_kind("今天天气怎么样", [], intent_enabled=True) == "conversation"
    assert classify_request_kind("你是谁？", [], intent_enabled=True) == "conversation"


def test_fallback_intent_is_not_trusted_as_llm_semantics():
    from app.agent.planner import _is_llm_intent, build_fallback_plan

    assert _is_llm_intent({"kind": "analysis", "producer": "fallback"}) is False
    assert _is_llm_intent({"kind": "analysis", "producer": "llm"}) is True
    # producer=fallback 的意图不得覆盖确定性任务结构，避免建模/复核被误判成普通查询。
    plan = build_fallback_plan(
        "识别高不良风险的生产记录",
        [],
        agent_intent={"kind": "analysis", "producer": "fallback"},
    )
    query_steps = [s for s in plan.steps if s.capability == "analysis.query_data"]
    assert query_steps


def test_registry_registers_delivery_and_monitoring_capabilities():
    from app.agent.capabilities import build_default_registry

    registry = build_default_registry()
    names = registry.names()
    assert "delivery.push" in names
    assert "monitoring.scan" in names
    assert "monitoring.list_rules" in names
    business = registry.names_for(
        "business",
        frozenset({"delivery:send", "monitoring:read", "monitoring:scan"}),
    )
    assert "delivery.push" in business
    assert "monitoring.scan" in business


def test_delivery_push_requires_explicit_approval():
    from app.agent.capabilities import build_default_registry
    from app.agent.capability_executor import CapabilityExecutor
    from app.agent.capability_registry import CapabilityContext
    from app.agent.plan_schema import PlanBudget

    registry = build_default_registry()
    executor = CapabilityExecutor(registry)
    context = CapabilityContext(
        run_id="run-delivery", session_id="ses-delivery", tenant_id="tenant-a",
        user_id="user-a", role="business", source_ids=(1,),
        capability_scopes=frozenset({"delivery:send"}), plan_revision=1,
        step_id="pst-1", idempotency_key="key-1", state={},
    )
    budget = PlanBudget(maxCapabilityCalls=8, maxDatabaseExecutions=3, maxReplans=1, maxAsyncJobs=1)
    decision = executor.authorize(context, "delivery.push", {}, budget, [])
    assert decision.allowed is True
    assert decision.requires_approval is True


def test_dialogue_routes_confirm_followup_but_not_business_followup():
    """引用上一轮结果的确认 → 对话；业务追问（继续…它…）→ 仍走分析。"""
    assert _looks_referential("你这个是所有产线的吗") is True
    assert _looks_referential("继续看看它最近怎么样") is False
    assert should_treat_as_conversation("你这个是所有产线的吗", True, "analysis") is True
    assert should_treat_as_conversation("继续看看它最近怎么样", True, "analysis") is False
    assert should_treat_as_conversation("5月份不良率是多少", True, "analysis") is False
    assert should_treat_as_conversation("你是谁", False, "utility") is False


def test_inline_conversation_reply_keeps_role_greeting_canned_but_chat_to_llm():
    role = inline_conversation_reply("你有什么能力，应该怎么使用？")
    assert role and "ForgeSight" in role and "只读" in role
    assert inline_conversation_reply("你好") == inline_conversation_reply("你好")
    # 真实闲聊返回 None，交给 LLM 对话器；不返回 canned 模板
    assert inline_conversation_reply("你可以陪我聊天吗") is None
    assert inline_conversation_reply("你知道java是什么吗") is None
