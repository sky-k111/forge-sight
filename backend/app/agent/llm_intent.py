"""LLM 意图解析器 + 确定性兜底。

Phase A（shadow）：旁路产出 AgentIntent，落库/事件，与现有 intent.py 决策对拍。
本文件【不改变】现有执行路径：resolve_intent 只在被显式调用（且 generate_text 可用）时
才调用一次 LLM；失败或未提供 generate_text 时回退到 build_fallback_intent（确定性兜底，
producer=fallback），保证离线/降级也拿到同一 shape 的契约。
"""

from __future__ import annotations

import json
from typing import Any, Callable

from app.agent.intent import build_intent_plan, classify_request_kind, is_report_request
from app.agent.intent_contract import (
    AgentIntent,
    Chart,
    ClarifyField,
    Comparison,
    Dimension,
    Grain,
    IntentKind,
    ModelingSpec,
    ReportSpec,
    StepIntent,
    TimeMode,
    TimeRange,
)
from app.agent.planner import _looks_like_modeling


# ---------------------------------------------------------------------------
# 确定性兜底：把现有 intent.py 的决策投影为 AgentIntent（producer=fallback）。
# 它忠实复刻当前行为，因此是 Phase A 对拍的基线，也暴露「5月/6月」「工序」这类缺口。
# ---------------------------------------------------------------------------
_DIM_SEMANTICS = {
    "产品": "产品",
    "产线": "产线",
    "工序": "工序",
    "车间": "车间",
    "班次": "班次",
    "缺陷类型": "缺陷类型",
    "类型": "缺陷类型",
    "状态": "状态",
}
_TIME_TOKEN_GRAIN: dict[str, Grain] = {
    "月份": "month",
    "月": "month",
    "每月": "month",
    "周": "week",
    "每周": "week",
    "季度": "quarter",
    "每季度": "quarter",
    "日": "day",
    "每天": "day",
    "年": "year",
}


def _extract_json(text: str) -> dict[str, Any]:
    cleaned = str(text or "").strip().replace("```json", "").replace("```", "")
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("意图解析未返回 JSON 对象")
    return json.loads(cleaned[start:end + 1])


def _time_from_constraint(tc: dict[str, Any] | None) -> TimeRange | None:
    if not tc:
        return None
    kind = str(tc.get("kind") or "")
    grain: Grain | None = tc.get("timeGrain") or None
    periods = tc.get("periods") or []
    comparisons = [TimeRange(mode=TimeMode.ABSOLUTE, label=str(p.get("label") or "")) for p in periods]
    reference = "now" if kind == "current_vs_previous_month" else (
        tc.get("reference") or "data_horizon"
    )
    if kind == "current_vs_previous_month":
        return TimeRange(
            mode=TimeMode.COMPARISON,
            grain=grain or "month",
            label=str(tc.get("label") or ""),
            reference="now",
            comparisons=comparisons,
        )
    if kind == "current_month" or kind == "current_week" or kind == "current_quarter" or kind == "current_year":
        return TimeRange(mode=TimeMode.CURRENT, grain=grain or "month", label=str(tc.get("label") or ""), reference="now")
    if kind == "recent":
        return TimeRange(
            mode=TimeMode.RECENT,
            grain=grain or "day",
            amount=int(tc["amount"]) if tc.get("amount") else None,
            unit=str(tc.get("unit") or ""),
            label=str(tc.get("label") or ""),
            reference="data_horizon",
        )
    if kind in {"previous_week", "previous_month", "previous_quarter", "previous_year"}:
        return TimeRange(mode=TimeMode.RELATIVE, grain=grain or "month", label=str(tc.get("label") or ""), reference="data_horizon")
    if kind == "specific_month":
        # 忠实复刻现状：仅锚定 data_horizon 的「N 月」。Phase A 只作基线。
        return TimeRange(mode=TimeMode.ABSOLUTE, grain=grain or "day", label=str(tc.get("label") or ""), reference="data_horizon")
    return TimeRange(mode=TimeMode.ABSOLUTE, grain=grain or "day", label=str(tc.get("label") or ""), reference="data_horizon")


def _dimension_from_token(token: str) -> Dimension:
    tok = str(token or "").strip()
    if tok in _DIM_SEMANTICS:
        return Dimension(semantic=_DIM_SEMANTICS[tok], label=tok)
    if tok in _TIME_TOKEN_GRAIN:
        return Dimension(semantic="时间", grain=_TIME_TOKEN_GRAIN[tok], label=tok)
    # 兼容带前缀的写法：如“产线/工序”整体
    for sem, canon in _DIM_SEMANTICS.items():
        if sem in tok:
            return Dimension(semantic=canon, label=tok)
    return Dimension(semantic=tok, label=tok)


def _chart_from_token(rc: str | None) -> Chart | None:
    if not rc:
        return None
    allowed = {"auto", "bar", "line", "donut", "scatter", "heatmap", "table", "combo"}
    if rc in allowed:
        return Chart(type=rc)  # type: ignore[arg-type]
    return None


def _fallback_kind(question: str, conversation_context: list[dict[str, str]] | None) -> IntentKind:
    if not str(question or "").strip():
        return IntentKind.CONVERSATION
    route = classify_request_kind(question, conversation_context)
    if route == "utility":
        return IntentKind.UTILITY
    if route == "conversation":
        return IntentKind.REPORT if is_report_request(question) else IntentKind.CONVERSATION
    if _looks_like_modeling(question):
        return IntentKind.MODELING
    if is_report_request(question):
        return IntentKind.REPORT
    if any(term in question for term in ("根因", "为什么", "原因", "下钻", "进一步分析")):
        return IntentKind.INVESTIGATION
    return IntentKind.ANALYSIS


def build_fallback_intent(
    question: str,
    conversation_context: list[dict[str, str]] | None = None,
) -> AgentIntent:
    plan = build_intent_plan(question, conversation_context)
    tasks = plan.get("tasks") or []
    clar = plan.get("clarification")
    if clar and not tasks:
        fields = [
            ClarifyField(
                id=str(f.get("id") or ""),
                label=str(f.get("label") or ""),
                question=str(f.get("question") or ""),
                options=[str(o.get("label") or "") for o in (f.get("options") or [])],
                allowCustom=bool(f.get("allowCustom", True)),
            )
            for f in (clar.get("fields") or [])
        ]
        return AgentIntent(
            kind=IntentKind.CLARIFY,
            question=question,
            clarify=fields,
            confidence=0.3,
            producer="fallback",
            reasons=["确定性兜底：需要向用户澄清"],
        )

    kind = _fallback_kind(question, conversation_context)
    merged_dims: list[Dimension] = []
    merged_seen: set[str] = set()
    time: TimeRange | None = None
    chart: Chart | None = None
    comparison: Comparison | None = None
    steps: list[StepIntent] = []
    for task in tasks:
        task_dims: list[Dimension] = []
        for token in (task.get("groupBy") or []):
            dim = _dimension_from_token(token)
            if dim.semantic not in merged_seen:
                merged_seen.add(dim.semantic)
                merged_dims.append(dim)
            if all(d.semantic != dim.semantic for d in task_dims):
                task_dims.append(dim)
        tc = task.get("timeConstraint")
        if tc and time is None:
            time = _time_from_constraint(tc)
        rc = _chart_from_token(task.get("requestedChart"))
        if rc and chart is None:
            chart = rc
        task_time = _time_from_constraint(tc) if tc else None
        steps.append(
            StepIntent(
                kind=kind,
                metrics=[],
                dimensions=task_dims,
                time=task_time,
                chart=_chart_from_token(task.get("requestedChart")) or rc,
            )
        )
        if task.get("analysisType") == "comparison" and comparison is None:
            comparison = Comparison(basis=str(task.get("title") or ""))
    if comparison is None and time is not None and time.mode == TimeMode.COMPARISON:
        comparison = Comparison(basis=time.label, periods=time.comparisons)

    modeling = ModelingSpec(task="prediction") if kind == IntentKind.MODELING else None
    report = ReportSpec(name="report") if kind == IntentKind.REPORT else None
    return AgentIntent(
        kind=kind,
        question=question,
        metrics=[],
        dimensions=merged_dims,
        time=time,
        chart=chart,
        comparison=comparison,
        modeling=modeling,
        report=report,
        confidence=0.4,
        producer="fallback",
        reasons=["确定性兜底：由现有 intent.py 规则投影"],
        stepsHint=steps,
    )


# ---------------------------------------------------------------------------
# LLM 意图解析：唯一语义源（producer=llm）。失败即回退到 build_fallback_intent。
# ---------------------------------------------------------------------------
_INTENT_SYSTEM = (
    "你是 ForgeSight 制造业数据分析 Agent 的唯一意图解析器。把用户的问题（含多轮上下文）"
    "理解成一个 JSON 对象，严格符合给定 schema。只输出 JSON，不要输出任何解释或思维链。\n"
    "字段与取值：\n"
    "- kind: analysis | modeling | report | investigation | utility | conversation | clarify\n"
    "- question: 原始问题文本\n"
    "- metrics: 数组，每项含 semantic(如 产量/良率/不良数/缺陷率/产能利用率) 和 "
    "aggregation(sum | avg | count | max | min | rate | share | rank)\n"
    "- dimensions: 数组，每项含 semantic(产线/产品/工序/车间/班次/缺陷类型/状态/时间) 与可选 "
    "grain(day | week | month | quarter | year，仅时间维度)\n"
    "- time: 对象，含 mode(absolute | relative | current | recent | comparison | none)，grain，"
    "start/end(ISO8601 字符串，可空)，amount/unit(如最近7天)，reference(now | data_horizon | prev_period)，"
    "comparisons(对比周期数组，每项同 time 结构)，label(人类可读)\n"
    "- chart: 对象，含 type(auto | bar | line | donut | scatter | heatmap | table | combo)，"
    "x/y/series(维度或指标名)，orient(auto | vertical | horizontal)，secondary(双轴次指标)\n"
    "- comparison: 对象，含 basis(对比描述)，periods(对比周期数组)，topDiffK(如「差异最大两个工序」=2)，"
    "by(对比后下钻维度)\n"
    "- modeling: 仅 kind=modeling 时给，含 task(回归/二分类/多分类/聚类/异常检测)，target，features，horizon\n"
    "- report: 仅 kind=report 时给，含 name(周报/月报/专题)，period，audience，channel(feishu | dingtalk | email)\n"
    "- clarify: 仅 kind=clarify 时给，数组，每项含 id/label/question/options/allowCustom\n"
    "- confidence: 0~1 理解把握\n"
    "- reasons: 简短数组，说明为什么这样理解（供审计）\n"
    "- stepsHint: 若问题是复合/多步，数组，每项含 kind/metrics/dimensions/time/chart/comparison\n"
    "判定边界（重要）：\n"
    "- 只有当用户明确要查询/对比企业生产、质量、设备、库存等业务数据，且问题里带真实业务指标或维度"
    "（产量/良率/不良/产线/工序/设备/库存/工单/物料/合格/缺陷/停机/报警/产能…）时，kind 才取 analysis。\n"
    "- 纯常识、概念解释、生活/通用问题——即使出现 分析/统计/比较/多少/查询 等词——一律 kind=conversation。"
    "例如“什么是OEE”“什么是工序良率”“5月有多少天”“世界上有多少个国家”“苹果和安卓哪个好”"
    "“今年高考录取率多少”“今天天气怎么样”“你分析一下我今天的运气”。不要臆造业务指标，也不要为这些生成 SQL。\n"
    "- kind=clarify 只用于确实需要用户补全必要维度/时间/指标的业务问题；常识类不需要澄清。\n"
    "注意：若用户表达不清或缺少必要维度/时间，应把 kind 设为 clarify 或降低 confidence；"
    "不要凭空编造指标或维度。\n"
)


def _user_prompt(question: str, conversation_context: list[dict[str, str]] | None) -> str:
    recent = []
    for item in (conversation_context or [])[-6:]:
        role = str(item.get("role") or "")
        content = str(item.get("content") or "")
        if role and content:
            recent.append(f"{role}: {content}")
    return json.dumps(
        {"question": question, "context": recent},
        ensure_ascii=False,
    )


def resolve_intent(
    question: str,
    conversation_context: list[dict[str, str]] | None = None,
    generate_text: Callable[[dict[str, Any], str, str, str], str] | None = None,
    *,
    run_id: str = "intent-resolve",
    state: dict[str, Any] | None = None,
) -> tuple[AgentIntent, str, str | None]:
    """返回 (intent, producer, error)。producer 为 llm 或 fallback。"""
    if generate_text is not None:
        try:
            st = dict(state or {})
            st.setdefault("run_id", run_id)
            raw = generate_text(st, "intent.parse", _INTENT_SYSTEM, _user_prompt(question, conversation_context))
            intent = AgentIntent.model_validate(_extract_json(raw))
            return intent.model_copy(update={"producer": "llm", "question": question}), "llm", None
        except Exception as exc:  # noqa: BLE001 - 非法/失败必须安全降级
            fallback = build_fallback_intent(question, conversation_context)
            return fallback.model_copy(update={"producer": "fallback"}), "fallback", type(exc).__name__
    fallback = build_fallback_intent(question, conversation_context)
    return fallback, "fallback", None
