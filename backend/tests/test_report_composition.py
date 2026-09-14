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
