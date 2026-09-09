"""Phase A：AgentIntent 契约 + LLM 意图解析(兜底)回归测试。"""

from __future__ import annotations

import json

import pytest

from app.agent.intent_contract import AgentIntent
from app.agent.llm_intent import _extract_json, build_fallback_intent, resolve_intent

SAMPLES = [
    "对比5月与6月各产线良率，找出差异最大的两个工序再下钻",
    "输出2026年总产量",
    "用柱状图展示",
    "分别按产线以及周数分类",
]


def test_fallback_intent_is_valid_model_for_all_samples():
    for q in SAMPLES:
        intent = build_fallback_intent(q)
        assert isinstance(intent, AgentIntent)
        assert intent.producer == "fallback"
        # round-trips through JSON (SQLAlchemy / 事件流都是 JSON 形态)
        again = AgentIntent.model_validate(intent.model_dump(mode="json", by_alias=True))
        assert again.kind == intent.kind


def test_fallback_reveals_time_single_month_gap():
    """基线：只命中第一个「5月」，comparisons 为空。这正是「5月/6月」bug 的对拍样本。"""
    intent = build_fallback_intent(SAMPLES[0])
    assert intent.time is not None
    assert intent.time.comparisons == []
    assert intent.time.label == "5月"


def test_fallback_reveals_missing_process_dimension():
    """基线：工序没进维度（原话无「按/各/每道」前缀）。"""
    intent = build_fallback_intent(SAMPLES[0])
    dims = {d.semantic for d in intent.dimensions}
    assert "产线" in dims
    assert "工序" not in dims


def test_fallback_reveals_week_breakdown_miss():
    """基线：「周数」不在现有分组/时间拆分词表里，导致没有周粒度维度。"""
    intent = build_fallback_intent(SAMPLES[3])
    assert "产线" in {d.semantic for d in intent.dimensions}
    assert not any(d.semantic == "时间" and d.grain == "week" for d in intent.dimensions)


def test_extract_json_robust_to_fences():
    assert _extract_json("```json\n{\"a\": 1}\n```") == {"a": 1}
    assert _extract_json("噪音 { \"b\": 2 } 尾巴") == {"b": 2}
    with pytest.raises(ValueError):
        _extract_json("no json here")


def _fake_valid_llm(state, step_key, system, user):  # noqa: ANN001
    payload = {
        "kind": "analysis",
        "question": SAMPLES[0],
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
    return json.dumps(payload, ensure_ascii=False)


def test_resolve_intent_llm_path_uses_agent_semantics():
    """LLM 意图解析覆盖兜底：得到 5 月/6 月对比 + 工序维度。"""
    intent, producer, err = resolve_intent(SAMPLES[0], generate_text=_fake_valid_llm)
    assert producer == "llm"
    assert err is None
    assert intent.kind.value == "analysis"
    assert "工序" in {d.semantic for d in intent.dimensions}
    assert intent.time is not None and len(intent.time.comparisons) == 2
    assert intent.comparison is not None and intent.comparison.top_diff_k == 2


def _fake_bad_llm(state, step_key, system, user):  # noqa: ANN001
    return "这不是 JSON"


def test_resolve_intent_falls_back_on_bad_output():
    intent, producer, err = resolve_intent(SAMPLES[1], generate_text=_fake_bad_llm)
    assert producer == "fallback"
    assert err is not None
    assert intent.producer == "fallback"

