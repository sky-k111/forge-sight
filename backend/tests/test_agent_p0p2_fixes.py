"""P0-P2 修复回归：复合指标拆分、指标提示、超范围年份、写意图守卫终态。"""
from app.agent.intent import _time_constraint, split_questions, split_metric_compound
from app.agent.query_compiler import _pick_metric
from app.agent.time_resolution import resolve_time_constraint


def metric(name, formula, tf):
    return {"name": name, "physicalFormula": formula, "physicalTimeField": tf}


_METRICS = [
    metric("工序良率", "SUM(good_qty)/SUM(input_qty)", "stat_date"),
    metric("抽检不良率", "SUM(defect_qty)/SUM(sample_qty)", "inspection_date"),
    metric("工序不良率", "SUM(defect_qty)/SUM(input_qty)", "stat_date"),
    metric("产量", "SUM(good_qty + defect_qty)", "stat_date"),
    metric("合格数量", "SUM(good_qty)", "stat_date"),
]


def _name(m):
    return m["name"] if m else None


def test_split_metric_compound_detects_parallel_metrics():
    assert split_metric_compound("6月各周的良率和不良率") == ["6月各周的良率", "不良率"]
    assert split_metric_compound("6月份各产线的产量以及良率分别是多少") == ["6月份各产线的产量", "良率分别是多少"]
    assert split_metric_compound("6月份各产线的产量和合格数量") == ["6月份各产线的产量", "合格数量"]
    # 同一指标的补充条件 / 并列维度不应被误拆
    assert split_metric_compound("分析良率并与标准比较") == ["分析良率并与标准比较"]
    assert split_metric_compound("按产线和工序看良率") == ["按产线和工序看良率"]


def test_pick_metric_respects_hint_core():
    # 良率 hint 只能命中「良率」类指标，不能因「不良率」含子串而误命中
    assert _name(_pick_metric(_METRICS, "6月各周的良率和不良率", hint="良率")) == "工序良率"
    assert _name(_pick_metric(_METRICS, "6月各周的良率和不良率", hint="不良率")) in {"抽检不良率", "工序不良率"}
    assert _name(_pick_metric(_METRICS, "6月份各产线的产量以及良率分别是多少", hint="产量")) == "产量"
    assert _name(_pick_metric(_METRICS, "6月份各产线的产量以及良率分别是多少", hint="良率")) == "工序良率"


def test_time_constraint_detects_bare_year():
    tc = _time_constraint("2020年的总产量是多少")
    assert tc and tc["kind"] == "specific_year" and tc["year"] == 2020
    tc2 = _time_constraint("2026年各月产量")
    assert tc2 and tc2["kind"] == "specific_year" and tc2["year"] == 2026
    # 裸月份仍走 specific_month，不被年份误伤
    tc3 = _time_constraint("6月各周良率")
    assert tc3 and tc3["kind"] == "specific_month" and tc3["month"] == 6


def test_resolve_specific_year_creates_period():
    resolved = resolve_time_constraint(
        {"kind": "specific_year", "year": 2020, "label": "2020年", "timeGrain": "year", "reference": "data_horizon"},
        horizon="2026-07-15",
    )
    assert resolved and resolved["periods"] == [{
        "key": "year",
        "label": "2020",
        "startInclusive": "2020-01-01",
        "endExclusive": "2021-01-01",
    }]


def test_compound_question_splits_into_independent_tasks():
    plan = split_questions("6月各周的良率和不良率")
    assert len(plan) == 2
    assert plan[1] == "不良率"