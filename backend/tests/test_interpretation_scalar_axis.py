# 单值标量聚合的横轴语义：结果没有任何维度列时，横轴不应回退成指标值本身。
from app.agent.interpretation import build_interpretation


def _build(task, metric, columns, rows):
    return build_interpretation(
        task, metric, {"columns": columns, "rows": rows, "row_count": len(rows), "truncated": False}
    )


def test_scalar_with_period_label_uses_semantic_axis_not_value():
    task = {
        "analysisType": "query",
        "requestedChart": None,
        "normalizedQuestion": "5月份良率",
        "originalQuestion": "5月份良率",
        "groupBy": [],
        "intent": {"timeConstraint": {"label": "5月"}},
    }
    metric = {"name": "工序良率", "axis": None, "periods": [{"key": "specific", "label": "2026-05"}], "timeGrain": "day"}
    out = _build(task, metric, ["工序良率"], [[96.8346]])
    chart = out["chart"]
    assert chart["xKey"] == "统计期间"
    assert chart["xAxis"]["kind"] == "category"
    assert chart["xAxis"]["grain"] is None
    assert chart["data"][0]["统计期间"] == "5月"
    assert chart["bars"][0]["key"] == "工序良率"
    assert out["kpis"][0]["caption"] == "5月"


def test_scalar_without_period_falls_back_to_metric_name_axis():
    task = {"analysisType": "query", "normalizedQuestion": "库存总量", "originalQuestion": "库存总量", "groupBy": []}
    metric = {"name": "库存总量", "axis": None, "periods": []}
    out = _build(task, metric, ["库存总量"], [[1234.5]])
    chart = out["chart"]
    assert chart["xKey"] == "统计期间"
    assert chart["data"][0]["统计期间"] == "库存总量"
    assert chart["bars"][0]["key"] == "库存总量"


def test_scalar_axis_is_never_the_numeric_value():
    task = {"analysisType": "query", "normalizedQuestion": "输出5月份良率", "originalQuestion": "输出5月份良率", "groupBy": []}
    metric = {"name": "工序良率", "axis": None, "periods": [{"key": "specific", "label": "2026-05"}]}
    out = _build(task, metric, ["工序良率"], [[96.8346]])
    chart = out["chart"]
    assert "96.8346" not in str(chart.get("xAxis", {}).get("labels", {}))
    assert chart["data"][0]["统计期间"] != 96.8346