"""阶段 5F：复杂指代与合并澄清。"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.agent.intent import build_intent_plan, build_request_route, classify_analysis_type, classify_request_kind
from app.agent.system_utility import build_system_utility_response


def test_obvious_smalltalk_is_routed_away_from_analysis():
    assert classify_request_kind("你好") == "conversation"
    assert classify_request_kind("谢谢你") == "conversation"
    assert classify_request_kind("你能做什么？") == "conversation"
    assert classify_request_kind("你有什么能力，应该怎么使用？") == "conversation"
    assert classify_request_kind("明年下雨吗") == "conversation"
    assert classify_request_kind("分析最近 7 天的工序良率") == "analysis"
    # 术语定义解释不应查库：走对话由 LLM 解释
    assert classify_request_kind("什么是工序良率") == "conversation"


def test_system_utility_is_deterministic_and_does_not_enter_analysis():
    assert classify_request_kind("现在几点？") == "utility"
    assert classify_request_kind("今天星期几？") == "utility"
    assert classify_request_kind("当前使用哪个数据源？") == "utility"
    assert classify_request_kind("我现在问的数据是哪个数据源？") == "utility"
    assert classify_request_kind("数据更新到什么时候？") == "utility"

    response = build_system_utility_response(
        "现在几点？",
        None,
        now=datetime(2026, 8, 28, 9, 35, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    assert "2026年08月28日 09:35" in response
    assert "星期五" in response
    assert "Asia/Shanghai" in response


def test_mixed_utility_and_analysis_preserves_both_routes():
    route = build_request_route("现在几点；分析最近 7 天的产量趋势")

    assert route["kind"] == "mixed"
    assert route["utilityQuestion"] == "现在几点"
    assert route["analysisQuestion"] == "分析最近 7 天的产量趋势"
    assert [item["kind"] for item in route["segments"]] == ["utility", "analysis"]


def test_bare_report_request_asks_for_report_scope_instead_of_entering_data_analysis():
    route = build_request_route("给我生成一份报告")

    assert classify_request_kind("给我生成一份报告") == "conversation"
    assert route["kind"] == "conversation"
    assert "报告类型" in route["conversationResponse"]
    assert "统计周期" in route["conversationResponse"]


def test_report_output_does_not_hide_a_real_business_analysis_request():
    assert classify_request_kind("分析最近 7 天的产量并生成一份报告") == "analysis"


def test_report_generation_variants_with_scope_stay_out_of_no_match_route():
    assert classify_request_kind("请帮我生成一份报告") == "conversation"
    assert classify_request_kind("生成最近 7 天的质量周报") == "conversation"
    assert classify_request_kind("你好，帮我生成一份报告") == "conversation"


def test_report_request_with_implicit_give_me_wording_stays_in_report_route():
    for text in (
        "给我一份周报",
        "我要一份报告",
        "来一份质量周报",
        "我想要一份设备周报",
        "可以帮我做个库存周报吗",
        "请给我准备一份生产周报",
        "帮我出个周报",
    ):
        assert classify_request_kind(text) == "conversation"


def test_report_explanation_is_not_mistaken_for_report_creation():
    # “解释指标”是术语说明，不属于报告创建；也不应触发独立查询
    assert classify_request_kind("请解释报告里的指标") == "conversation"


def test_emotional_conversation_is_not_misclassified_by_a_single_character():
    assert classify_request_kind("我最近表现不好，想和你聊聊") == "conversation"
    assert classify_request_kind("最近压力有点大，想和你聊聊") == "conversation"


def test_ambiguous_business_language_stays_available_to_the_lightweight_router():
    assert classify_request_kind("上周每条线做了几件？") == "unknown"
    history = [{"role": "user", "content": "统计各产线最近 7 天产量"}]
    assert classify_request_kind("二线呢？", history) == "analysis"
    assert classify_request_kind("那昨天呢？", history) == "analysis"


def test_followup_only_inherits_the_most_recent_user_topic():
    history = [
        {"role": "user", "content": "统计最近 7 天各产线产量"},
        {"role": "assistant", "content": "已完成业务分析。"},
        {"role": "user", "content": "今天天气怎么样？"},
        {"role": "assistant", "content": "这不属于企业数据分析范围。"},
    ]

    assert classify_request_kind("那明天呢？", history) == "unknown"


def test_capability_answer_states_forgesight_role_and_readonly_boundary():
    route = build_request_route("你有什么能力，应该怎么使用？")

    assert route["kind"] == "conversation"
    assert "ForgeSight" in route["conversationResponse"]
    assert "只读" in route["conversationResponse"]
    assert "生产" in route["conversationResponse"]


def test_mixed_request_keeps_only_business_segments_for_analysis():
    route = build_request_route("你好；请统计最近 7 天的产量趋势；谢谢你")

    assert route["kind"] == "mixed"
    assert route["analysisQuestion"] == "请统计最近 7 天的产量趋势"
    assert [item["kind"] for item in route["segments"]] == ["conversation", "analysis", "conversation"]
    assert route["conversationResponse"] == "你好。"


def test_dependent_reference_is_bound_to_previous_task():
    plan = build_intent_plan("先找良率最低的工序，再分析它的缺陷类型")

    assert plan["clarification"] is None
    assert len(plan["tasks"]) == 2
    assert plan["tasks"][1]["mode"] == "dependent"
    assert plan["tasks"][1]["dependsOn"] == [plan["tasks"][0]["id"]]
    assert "上一步结果中的对象" in plan["tasks"][1]["normalizedQuestion"]


def test_vague_follow_up_merges_object_and_time_clarification():
    history = [
        {"role": "user", "content": "比较设备停机时长和各工序良率"},
        {"role": "assistant", "content": "分析完成"},
    ]
    plan = build_intent_plan("看看它最近怎么样", history)

    assert plan["tasks"] == []
    assert [field["id"] for field in plan["clarification"]["fields"]] == ["object", "timeRange"]
    object_values = {item["value"] for item in plan["clarification"]["fields"][0]["options"]}
    assert {"设备", "工序"}.issubset(object_values)


def test_merged_clarification_answers_resolve_to_one_executable_task():
    history = [{"role": "user", "content": "比较设备停机时长和各工序良率"}]
    plan = build_intent_plan(
        "看看它最近怎么样",
        history,
        {"object": "设备", "timeRange": "最近 30 天"},
    )

    assert plan["clarification"] is None
    assert len(plan["tasks"]) == 1
    assert "设备" in plan["tasks"][0]["normalizedQuestion"]
    assert "最近 30 天" in plan["tasks"][0]["normalizedQuestion"]


def test_current_month_is_time_not_an_object_reference():
    history = [{"role": "user", "content": "查看不良数量最高的 4 个产品"}]

    plan = build_intent_plan("使用折线图展示这个月的不良品数量", history)

    assert plan["clarification"] is None
    task = plan["tasks"][0]
    assert task["normalizedQuestion"] == "使用折线图展示这个月的不良品数量"
    assert task["analysisType"] == "trend"
    assert task["requestedChart"] == "line"
    assert task["timeConstraint"] == {
        "kind": "current_month",
        "label": "本月至今",
        "timeGrain": "day",
    }
    assert task["groupBy"] == []


def test_current_and_previous_month_resolve_to_two_explicit_periods():
    task = build_intent_plan("这个月和上个月的良率比较")["tasks"][0]
    constraint = task["timeConstraint"]
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    current_start = today.replace(day=1)
    previous_start = (current_start - timedelta(days=1)).replace(day=1)

    assert constraint["kind"] == "current_vs_previous_month"
    assert constraint["referenceDate"] == today.isoformat()
    assert constraint["comparisonBasis"] == "本月至今对比上月整月"
    assert constraint["periods"] == [
        {
            "key": "current",
            "label": f"{current_start:%Y-%m}（本月至今）",
            "startInclusive": current_start.isoformat(),
            "endExclusive": (today + timedelta(days=1)).isoformat(),
        },
        {
            "key": "previous",
            "label": f"{previous_start:%Y-%m}（上月）",
            "startInclusive": previous_start.isoformat(),
            "endExclusive": current_start.isoformat(),
        },
    ]


def test_extracts_combo_and_heatmap_chart_preferences():
    combo = build_intent_plan("用双轴图比较各产线产量和不良率")["tasks"][0]
    heatmap = build_intent_plan("用热力图展示工序和缺陷类型的分布")["tasks"][0]

    assert combo["requestedChart"] == "combo"
    assert combo["analysisType"] == "comparison"
    assert heatmap["requestedChart"] == "heatmap"
    assert heatmap["analysisType"] == "comparison"


def test_modeling_intent_beats_default_query_but_not_trend():
    assert classify_analysis_type("识别高不良风险的生产记录") == "modeling"
    assert classify_analysis_type("判断设备是否合格") == "modeling"
    assert classify_analysis_type("预测设备停机时长") == "modeling"
    assert classify_analysis_type("聚类分析各产线") == "modeling"
    # 趋势/对比类问析优先，不落入建模；含"异常"的常规问析仍归 anomaly
    assert classify_analysis_type("分析各产线产量趋势") == "trend"
    assert classify_analysis_type("对比两条产线良率差异") == "comparison"
    assert classify_analysis_type("识别生产记录的异常点") == "anomaly"
