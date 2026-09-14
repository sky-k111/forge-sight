"""Personal report compositions preserve the governed query and tenant boundary."""

from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.gateway.executor import ExecutionResult
from app.reports import composition as service
from app.reports.composition_contracts import ReportBlock, ReportComposition
from app.reports.automation import build_structured_content


@pytest.fixture()
def composition_session(knowledge_session, monkeypatch):
    monkeypatch.setattr(service, "PlatformSession", knowledge_session)
    return knowledge_session


def _composition(metric_id="M-001"):
    return ReportComposition(
        name="车间经营周报",
        blocks=[
            ReportBlock(
                id="weekly-output",
                kind="trend",
                title="产量周趋势",
                metricId=metric_id,
                grain="week",
                chart="line",
            )
        ],
    )


def test_contract_rejects_sql_and_incompatible_chart():
    with pytest.raises(ValidationError):
        ReportBlock.model_validate(
            {
                "id": "unsafe",
                "kind": "trend",
                "title": "不安全",
                "metricId": "M-001",
                "chart": "line",
                "sql": "SELECT * FROM secret",
            }
        )
    with pytest.raises(ValidationError):
        ReportBlock(
            id="bad-chart",
            kind="trend",
            title="错误图表",
            metricId="M-001",
            chart="donut",
        )
    assert ReportBlock(id="column", kind="trend", title="柱形趋势", metricId="M-001", chart="column").chart == "column"
    assert ReportBlock(id="table", kind="breakdown", title="缺陷明细", metricId="M-001", dimension="缺陷类型", chart="table").chart == "table"
    assert ReportBlock(id="calendar", kind="trend", title="产量日历", metricId="M-001", grain="day", chart="calendar").chart == "calendar"
    assert ReportBlock(id="trend-compare", kind="trend", title="双周趋势", metricId="M-001", chart="line", compare=True).compare is True
    with pytest.raises(ValidationError):
        ReportBlock(id="bad-compare", kind="breakdown", title="错误对比", metricId="M-001", dimension="产线", chart="bar", compare=True)


def test_preview_trend_compare_queries_the_previous_period(composition_session, monkeypatch):
    windows = []

    def execute(sql, **kwargs):
        windows.append((sql, kwargs))
        return ExecutionResult(
            ok=True,
            columns=["周", "产量"],
            rows=[["2026-23", 120], ["2026-24", 135]],
            row_count=2,
        )

    monkeypatch.setattr(service, "execute_readonly", execute)
    composition = ReportComposition(
        name="对比周报",
        blocks=[ReportBlock(
            id="weekly-output", kind="trend", title="产量周趋势", metricId="M-001",
            grain="week", chart="line", compare=True,
        )],
    )
    result = service.preview(
        composition, 1, "tenant-default", "alice",
        datetime(2026, 6, 1), datetime(2026, 7, 1),
    )
    item = result["items"][0]
    assert item["state"] == "ready"
    assert len(windows) == 2
    assert item["previousRows"] == [["2026-23", 120], ["2026-24", 135]]


def test_catalog_uses_real_relationships_and_governed_metrics(composition_session):
    catalog = service.load_catalog(1, "tenant-default")
    assert catalog["source"]["name"] == "测试制造数据底座"
    assert {item["id"] for item in catalog["metrics"]} >= {"M-001"}
    assert any(item["metricIds"] for item in catalog["tables"])
    assert catalog["relations"][0]["fromTable"] == "mes_process_output"
    assert catalog["starters"][0]["id"] == "starter-blank"


def test_personal_templates_are_isolated_and_optimistically_versioned(composition_session):
    first = service.save_personal(
        1, "tenant-default", "alice", _composition(), version=0
    )
    assert first["version"] == 1
    assert service.list_personal(1, "tenant-default", "bob") == []
    updated = service.save_personal(
        1,
        "tenant-default",
        "alice",
        _composition(),
        template_id=first["id"],
        version=1,
    )
    assert updated["version"] == 2
    with pytest.raises(HTTPException) as conflict:
        service.save_personal(
            1,
            "tenant-default",
            "alice",
            _composition(),
            template_id=first["id"],
            version=1,
        )
    assert conflict.value.status_code == 409


def test_previous_period_rows_shifts_the_window(composition_session, monkeypatch):
    windows = []

    def execute(sql, **kwargs):
        windows.append(sql)
        return ExecutionResult(ok=True, columns=["周", "产量"], rows=[["2026-21", 90]], row_count=1)

    monkeypatch.setattr(service, "execute_readonly", execute)
    rows = service.previous_period_rows(
        1, "tenant-default", "alice",
        {"kind": "trend", "metricId": "M-001", "grain": "week", "compare": True, "topN": 12, "title": "产量趋势"},
        datetime(2026, 6, 1), datetime(2026, 7, 1),
    )
    assert rows == [["2026-21", 90]]
    assert len(windows) == 1
    # 非趋势或未开对比时不查询
    assert service.previous_period_rows(
        1, "tenant-default", "alice",
        {"kind": "kpi", "metricId": "M-001", "compare": True},
        datetime(2026, 6, 1), datetime(2026, 7, 1),
    ) is None


def test_preview_compiles_only_registered_metric_and_dimension(composition_session, monkeypatch):
    calls = []

    def execute(sql, **kwargs):
        calls.append((sql, kwargs))
        return ExecutionResult(
            ok=True,
            columns=["周", "产量"],
            rows=[["2026-23", 120], ["2026-24", 135]],
            row_count=2,
        )

    monkeypatch.setattr(service, "execute_readonly", execute)
    result = service.preview(
        _composition(),
        1,
        "tenant-default",
        "alice",
        datetime(2026, 6, 1),
        datetime(2026, 7, 1),
    )
    assert result["items"][0]["state"] == "ready"
    assert "SUM(good_qty + defect_qty)" in calls[0][0]
    assert calls[0][1]["source"].id == 1

    with pytest.raises(HTTPException):
        service.validate_composition(_composition("missing"), 1, "tenant-default")


def test_frozen_composition_keeps_the_selected_chart_layout_and_accent():
    report = SimpleNamespace(
        id="report-custom",
        title="自定义周报",
        source_id=1,
        period_start=datetime(2026, 6, 1),
        period_end=datetime(2026, 6, 8),
        scope_json={},
        audience_scope_hash="scope-a",
    )
    version = SimpleNamespace(id="version-custom", data_quality_json={"coverageScore": 100})
    section = SimpleNamespace(
        code="weekly-output",
        title="产量周趋势",
        status="succeeded",
        summary="产量按周展示。",
        confidence=95,
        artifact_ids_json=["artifact-output"],
        content_json={
            "result": {"columns": ["周", "产量"], "rows": [["2026-23", 120], ["2026-24", 135]]},
            "metricResolution": {"id": "M-001", "name": "产量", "unit": "件"},
            "presentation": {
                "kind": "trend",
                "chart": "column",
                "width": "full",
                "height": "focus",
                "accent": "violet",
                "topN": 12,
            },
        },
    )

    content = build_structured_content(
        report=report,
        version=version,
        sections=[section],
        raw_summary=section.summary,
        timezone_name="Asia/Shanghai",
    )

    assert content["charts"][0] == {
        "key": "weekly-output",
        "type": "column",
        "title": "产量周趋势",
        "description": "按周展示产量，采用用户选定的图表形式。",
        "unit": "件",
        "categoryLabel": "周",
        "valueLabel": "产量",
        "points": [{"label": "2026-23", "value": 120.0}, {"label": "2026-24", "value": 135.0}],
        "evidenceArtifactIds": ["artifact-output"],
        "colorRole": "production",
        "width": "full",
        "height": "focus",
        "accent": "violet",
    }


def test_frozen_breakdown_table_keeps_ranked_points():
    report = SimpleNamespace(
        id="report-table",
        title="缺陷明细周报",
        source_id=1,
        period_start=datetime(2026, 6, 1),
        period_end=datetime(2026, 6, 8),
        scope_json={},
        audience_scope_hash="scope-a",
    )
    version = SimpleNamespace(id="version-table", data_quality_json={"coverageScore": 100})
    section = SimpleNamespace(
        code="defect-table",
        title="缺陷类型明细",
        status="succeeded",
        summary="缺陷按类型汇总。",
        confidence=95,
        artifact_ids_json=["artifact-defect"],
        content_json={
            "result": {
                "columns": ["缺陷类型", "不良数量"],
                "rows": [["外观", 30], ["尺寸", 70], ["装配", 20]],
            },
            "metricResolution": {"id": "M-002", "name": "不良数量", "unit": "件"},
            "presentation": {
                "kind": "breakdown",
                "chart": "table",
                "dimension": "缺陷类型",
                "width": "full",
                "height": "standard",
                "accent": "default",
                "topN": 12,
            },
        },
    )

    content = build_structured_content(
        report=report,
        version=version,
        sections=[section],
        raw_summary=section.summary,
        timezone_name="Asia/Shanghai",
    )

    chart = content["charts"][0]
    assert chart["type"] == "table"
    assert [item["label"] for item in chart["points"]] == ["尺寸", "外观", "装配"]


def _trend_section(code, *, compare, previous_rows, chart="line", grain="week"):
    return SimpleNamespace(
        code=code, title="产量趋势", status="succeeded", summary="按周展示产量。",
        confidence=95, artifact_ids_json=["artifact-output"],
        content_json={
            "result": {
                "columns": ["周", "产量"],
                "rows": [["2026-23", 120], ["2026-24", 135]],
                **({"previousRows": previous_rows} if previous_rows is not None else {}),
            },
            "metricResolution": {"id": "M-001", "name": "产量", "unit": "件"},
            "presentation": {"kind": "trend", "chart": chart, "grain": grain, "compare": compare,
                             "width": "full", "height": "standard", "accent": "default", "topN": 12},
        },
    )


def _report_and_version(report_id):
    return (
        SimpleNamespace(id=report_id, title="趋势周报", source_id=1,
                        period_start=datetime(2026, 6, 1), period_end=datetime(2026, 6, 15),
                        scope_json={}, audience_scope_hash="scope-a"),
        SimpleNamespace(id=f"version-{report_id}", data_quality_json={"coverageScore": 100}),
    )


def test_frozen_trend_compare_carries_previous_series():
    report, version = _report_and_version("report-compare")
    section = _trend_section("weekly-output", compare=True, previous_rows=[["2026-21", 90], ["2026-22", 100]])
    content = build_structured_content(report=report, version=version, sections=[section],
                                       raw_summary=section.summary, timezone_name="Asia/Shanghai")
    chart = content["charts"][0]
    assert chart["comparisonLabel"] == "上一周期"
    assert chart["points"][0]["previousValue"] == 90.0
    assert chart["points"][1]["previousValue"] == 100.0
    assert "叠加紧邻等长上一周期" in chart["description"]


def test_frozen_trend_without_compare_has_no_previous():
    report, version = _report_and_version("report-nocmp")
    section = _trend_section("weekly-output", compare=False, previous_rows=None)
    content = build_structured_content(report=report, version=version, sections=[section],
                                       raw_summary=section.summary, timezone_name="Asia/Shanghai")
    chart = content["charts"][0]
    assert "comparisonLabel" not in chart
    assert all("previousValue" not in point for point in chart["points"])


def test_frozen_calendar_keeps_day_series():
    report, version = _report_and_version("report-cal")
    section = _trend_section("daily-output", compare=False, previous_rows=None, chart="calendar", grain="day")
    section.content_json["result"]["columns"] = ["日期", "产量"]
    section.content_json["result"]["rows"] = [["2026-06-01", 120], ["2026-06-02", 135], ["2026-06-03", 90]]
    content = build_structured_content(report=report, version=version, sections=[section],
                                       raw_summary=section.summary, timezone_name="Asia/Shanghai")
    chart = content["charts"][0]
    assert chart["type"] == "calendar"
    assert [item["label"] for item in chart["points"]] == ["2026-06-01", "2026-06-02", "2026-06-03"]
