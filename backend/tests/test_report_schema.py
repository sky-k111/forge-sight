"""V01-V04: structured weekly reports, validation, confidence, and freezing."""

from datetime import datetime

import pytest
from pydantic import ValidationError

from app.reports.schema import (
    StructuredWeeklyReport,
    calculate_confidence,
    content_checksum,
    validate_for_publish,
)
from app.reports.automation import build_structured_content


def _content(**overrides) -> dict:
    content = {
        "schemaVersion": "weekly-report/v1",
        "identity": {
            "reportId": "report-1",
            "versionId": "version-1",
            "title": "生产经营周报",
            "reportType": "weekly",
            "periodStart": "2026-08-03T00:00:00",
            "periodEnd": "2026-08-10T00:00:00",
            "timezone": "Asia/Shanghai",
        },
        "managementSummary": {
            "findings": [
                {
                    "level": "confirmed",
                    "text": "本周产量保持稳定。",
                    "evidenceArtifactIds": ["artifact-1"],
                    "confidence": 92,
                }
            ],
            "actions": [],
        },
        "metrics": [
            {
                "key": "output_qty",
                "name": "总产量",
                "value": 12840,
                "unit": "件",
                "previousValue": 12100,
                "changeRate": 0.0612,
                "trend": "up",
                "evidenceArtifactIds": ["artifact-1"],
                "confidence": 94,
            }
        ],
        "trends": [
            {
                "metricKey": "output_qty",
                "label": "近四周产量",
                "unit": "件",
                "points": [
                    {"label": "W31", "value": 12100},
                    {"label": "W32", "value": 12840},
                ],
            }
        ],
        "anomalies": [],
        "progress": [],
        "recommendations": [],
        "dataQuality": {
            "coverage": 96,
            "delayedSources": [],
            "missingMetrics": [],
            "temporaryMetrics": [],
            "failedSections": [],
        },
        "appendix": {"sources": ["MES"], "definitions": []},
    }
    content.update(overrides)
    return content


def test_structured_weekly_report_preserves_numeric_values_for_all_renderers():
    report = StructuredWeeklyReport.model_validate(_content())

    assert report.metrics[0].value == 12840
    assert isinstance(report.metrics[0].value, float)
    assert report.model_dump(by_alias=True)["schemaVersion"] == "weekly-report/v1"


def test_structured_weekly_report_rejects_formatted_metric_strings():
    content = _content()
    content["metrics"][0]["value"] = "12,840 件"

    with pytest.raises(ValidationError):
        StructuredWeeklyReport.model_validate(content)


def test_publish_validation_reports_machine_readable_hard_rule_failures():
    content = _content()
    content["managementSummary"]["findings"][0]["evidenceArtifactIds"] = []
    content["identity"]["periodEnd"] = "2026-08-02T00:00:00"

    result = validate_for_publish(content)

    assert result["passed"] is False
    assert result["ruleVersion"] == "weekly-report-validation-v2"
    assert {item["code"] for item in result["rules"] if item["level"] == "error"} == {
        "V-HARD-002",
        "V-HARD-004",
    }


def test_normal_report_without_structured_metrics_is_blocked():
    content = _content(metrics=[], trends=[])

    result = validate_for_publish(content)

    assert result["passed"] is False
    assert "V-HARD-005" in {item["code"] for item in result["rules"]}


def test_confidence_is_explainable_and_switches_to_data_anomaly_variant():
    result = calculate_confidence(
        {
            "coverage": 42,
            "delayedSources": ["ERP"],
            "missingMetrics": ["inventory_turnover", "on_time_delivery"],
            "temporaryMetrics": ["estimated_capacity"],
            "failedSections": ["inventory"],
        }
    )

    assert result == {
        "algorithmVersion": "weekly-confidence/v1",
        "score": 24,
        "level": "low",
        "templateVariant": "data_anomaly",
        "deductions": [
            {"code": "LOW_COVERAGE", "points": 29},
            {"code": "DELAYED_SOURCE", "points": 8},
            {"code": "MISSING_METRIC", "points": 20},
            {"code": "TEMPORARY_METRIC", "points": 7},
            {"code": "FAILED_SECTION", "points": 12},
        ],
    }


def test_content_checksum_is_stable_for_equivalent_json_and_changes_with_content():
    first = _content()
    reordered = {key: first[key] for key in reversed(first)}

    assert content_checksum(first) == content_checksum(reordered)
    changed = _content()
    changed["metrics"][0]["value"] = 12841
    assert content_checksum(first) != content_checksum(changed)


def test_structured_content_extracts_metric_trend_and_definition_from_frozen_result():
    from types import SimpleNamespace

    report = SimpleNamespace(
        id="report-1",
        title="生产经营周报",
        source_id=1,
        period_start=datetime(2026, 8, 3),
        period_end=datetime(2026, 8, 10),
    )
    version = SimpleNamespace(
        id="version-1",
        data_quality_json={"coverageScore": 96},
    )
    section = SimpleNamespace(
        code="output",
        status="succeeded",
        summary="产量连续增长。",
        confidence=92,
        artifact_ids_json=["artifact-result"],
        content_json={
            "result": {
                "columns": ["week", "output_qty"],
                "rows": [["W29", 11520], ["W30", 11880], ["W31", 12100], ["W32", 12840]],
            },
            "metricResolution": {
                "id": "output_qty",
                "name": "总产量",
                "definition": "统计周期内合格品与不良品产量之和",
            },
        },
    )

    content = build_structured_content(
        report=report,
        version=version,
        sections=[section],
        raw_summary="本周生产保持稳定。",
        timezone_name="Asia/Shanghai",
    )

    assert content["metrics"][0]["value"] == 12840
    assert content["metrics"][0]["evidenceArtifactIds"] == ["artifact-result"]
    assert content["trends"][0]["points"][-1] == {"label": "W32", "value": 12840.0}
    assert content["appendix"]["definitions"][0]["name"] == "总产量"
