"""Feishu chart projection and PNG rendering tests."""

import pytest

from app.integrations.feishu_charts import build_chart_specs, render_chart_png


def _snapshot(requested_chart="line"):
    return {
        "question": "统计最近 7 天产量趋势",
        "tasks": [
            {
                "id": "task-1",
                "title": "各产线产量趋势",
                "analysisType": "trend",
                "intent": {"requestedChart": requested_chart},
            }
        ],
        "artifacts": [
            {
                "type": "result",
                "taskId": "task-1",
                "content": {
                    "ok": True,
                    "columns": ["stat_date", "output_qty"],
                    "rows": [["2026-07-09", 120], ["2026-07-10", 168], ["2026-07-11", 142]],
                    "row_count": 3,
                },
            }
        ],
    }


def test_build_chart_specs_uses_result_artifact_and_requested_type():
    specs = build_chart_specs(_snapshot("line"))
    assert len(specs) == 1
    assert specs[0].type == "line"
    assert specs[0].x_key == "stat_date"
    assert specs[0].value_keys == ("output_qty",)
    assert specs[0].rows[1]["output_qty"] == 168


def test_render_chart_png_returns_real_high_resolution_png():
    payload = render_chart_png(build_chart_specs(_snapshot("bar"))[0])
    assert payload.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(payload) > 10_000


def test_table_request_does_not_create_a_fake_chart():
    assert build_chart_specs(_snapshot("table")) == []


@pytest.mark.parametrize("chart_type", ["line", "area", "bar", "donut", "combo"])
def test_supported_cartesian_and_structure_types_render(chart_type):
    snapshot = _snapshot(chart_type)
    if chart_type == "combo":
        snapshot["artifacts"][0]["content"]["columns"] = ["日期", "产量", "良率"]
        snapshot["artifacts"][0]["content"]["rows"] = [
            ["2026-07-09", 120, 96.2], ["2026-07-10", 168, 97.1], ["2026-07-11", 142, 96.8]
        ]
    payload = render_chart_png(build_chart_specs(snapshot)[0])
    assert payload.startswith(b"\x89PNG")


def test_scatter_uses_two_numeric_axes():
    snapshot = {
        "question": "停机时长和不良率是否相关，使用散点图",
        "tasks": [{"id": "task-1", "title": "相关性分析", "analysisType": "correlation"}],
        "artifacts": [{
            "type": "result",
            "taskId": "task-1",
            "content": {"ok": True, "columns": ["停机时长", "不良率"], "rows": [[12, 1.2], [35, 2.8], [22, 1.7]]},
        }],
    }
    spec = build_chart_specs(snapshot)[0]
    assert spec.type == "scatter"
    assert spec.value_keys == ("停机时长", "不良率")
    assert render_chart_png(spec).startswith(b"\x89PNG")


def test_heatmap_renders_two_categorical_dimensions():
    snapshot = {
        "question": "各产线各缺陷类型热力图",
        "tasks": [{"id": "task-1", "title": "缺陷热力图", "intent": {"requestedChart": "heatmap"}}],
        "artifacts": [{
            "type": "result",
            "taskId": "task-1",
            "content": {
                "ok": True,
                "columns": ["产线", "缺陷类型", "不良数量"],
                "rows": [["A线", "划伤", 12], ["A线", "裂纹", 5], ["B线", "划伤", 8]],
            },
        }],
    }
    spec = build_chart_specs(snapshot)[0]
    assert spec.type == "heatmap"
    assert render_chart_png(spec).startswith(b"\x89PNG")
