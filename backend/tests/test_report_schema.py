"""V01-V04: structured weekly reports, validation, confidence, and freezing."""

from datetime import datetime

import pytest
from pydantic import ValidationError

from app.reports.automation import build_structured_content
from app.reports.schema import (
    StructuredWeeklyReport,
    calculate_confidence,
    content_checksum,
    validate_for_publish,
)


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
            "comparisonPeriodStart": "2026-07-27T00:00:00",
            "comparisonPeriodEnd": "2026-08-03T00:00:00",
            "timezone": "Asia/Shanghai",
        },
        "managementSummary": {
            "findings": [
                {
                    "level": "confirmed",
                    "text": "本周产量保持稳定。",
                    "evidenceArtifactIds": ["artifact-1"],
                    "evidenceStatus": "verified",
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
        "audience": {
            "scopeHash": "scope-a",
            "dataSourceIds": [1],
            "sensitiveFieldsRedacted": True,
        },
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
    assert {
        "V-HARD-002",
        "V-HARD-004",
    } <= {item["code"] for item in result["rules"] if item["level"] == "error"}


def test_publish_validation_rejects_unsupported_or_untraceable_chart_specs():
    unsupported = _content()
    unsupported["charts"] = [
        {
            "key": "output",
            "type": "radar",
            "title": "产量",
            "description": "不在受控图表白名单内。",
            "unit": "件",
            "categoryLabel": "产线",
            "valueLabel": "产量",
            "points": [{"label": "一线", "value": 1}, {"label": "二线", "value": 2}],
            "evidenceArtifactIds": ["artifact-1"],
            "colorRole": "production",
        }
    ]
    untraceable = _content()
    untraceable["charts"] = [
        {
            **unsupported["charts"][0],
            "type": "bar",
            "evidenceArtifactIds": [],
        }
    ]

    assert "V-HARD-001" in {item["code"] for item in validate_for_publish(unsupported)["rules"]}
    assert "V-HARD-004" in {item["code"] for item in validate_for_publish(untraceable)["rules"]}


def test_normal_report_without_structured_metrics_is_blocked():
    content = _content(metrics=[], trends=[])

    result = validate_for_publish(content)

    assert result["passed"] is False
    assert "V-HARD-005" in {item["code"] for item in result["rules"]}


def test_publish_validation_blocks_inconsistent_claims_periods_evidence_and_secrets():
    content = _content()
    content["managementSummary"]["findings"][0].update(
        {"text": "由于产能提升，本周增长 99%。", "evidenceStatus": "inferred"}
    )
    content["identity"]["comparisonPeriodStart"] = "2026-08-01T00:00:00"
    content["audience"]["sensitiveFieldsRedacted"] = False

    result = validate_for_publish(content)

    codes = {item["code"] for item in result["rules"] if item["level"] == "error"}
    assert {"V-HARD-006", "V-HARD-007", "V-HARD-008", "V-HARD-009"} <= codes


def test_publish_validation_blocks_secret_labels_inside_narrative_text():
    content = _content()
    content["managementSummary"]["findings"][0]["text"] = "SMTP 授权码：must-never-ship"

    result = validate_for_publish(content)

    assert result["passed"] is False
    assert "V-HARD-009" in {item["code"] for item in result["rules"]}


def test_publish_validation_recomputes_change_rate_and_requires_adjacent_comparison():
    content = _content()
    content["metrics"][0]["changeRate"] = 0.99
    content["identity"]["comparisonPeriodEnd"] = "2026-08-01T00:00:00"
    content["identity"]["comparisonPeriodStart"] = "2026-07-25T00:00:00"

    result = validate_for_publish(content)

    codes = {item["code"] for item in result["rules"] if item["level"] == "error"}
    assert {"V-HARD-006", "V-HARD-010"} <= codes


def test_publish_validation_accepts_equal_calendar_weeks_with_inclusive_end():
    content = _content()
    content["identity"]["periodEnd"] = "2026-08-09T23:59:59"

    result = validate_for_publish(content)

    assert "V-HARD-006" not in {item["code"] for item in result["rules"]}


def test_publish_validation_requires_previous_value_for_change_rate():
    content = _content()
    content["metrics"][0]["previousValue"] = None

    result = validate_for_publish(content)

    assert "V-HARD-010" in {item["code"] for item in result["rules"]}


def test_zero_previous_value_allows_explicit_uncomputable_change_rate():
    content = _content()
    content["metrics"][0]["previousValue"] = 0
    content["metrics"][0]["changeRate"] = None
    content["managementSummary"]["findings"][0]["text"] = "本期指标已完成核验。"

    result = validate_for_publish(content)

    assert result["passed"] is True


@pytest.mark.parametrize(
    "text",
    ["API_TOKEN=leak", "SMTP_PASSWORD=leak", "密码：leak", "密钥：leak"],
)
def test_publish_validation_normalizes_common_sensitive_labels(text):
    content = _content()
    content["managementSummary"]["findings"][0]["text"] = text

    result = validate_for_publish(content)

    assert "V-HARD-009" in {item["code"] for item in result["rules"]}


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
        scope_json={},
        audience_scope_hash="scope-a",
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


def test_grouped_category_rows_create_a_distribution_without_period_comparison():
    from types import SimpleNamespace

    report = SimpleNamespace(
        id="report-1",
        title="生产经营周报",
        source_id=1,
        period_start=datetime(2026, 8, 3),
        period_end=datetime(2026, 8, 10),
        scope_json={},
        audience_scope_hash="scope-a",
    )
    version = SimpleNamespace(id="version-1", data_quality_json={"coverageScore": 42})
    section = SimpleNamespace(
        code="output",
        title="产线表现",
        status="succeeded",
        summary="二线出现异常波动，需继续复核。",
        confidence=90,
        artifact_ids_json=["artifact-result"],
        content_json={
            "result": {
                "columns": ["production_line", "output_qty"],
                "rows": [["一线", 12100], ["二线", 12840]],
            },
            "metricResolution": {"id": "output_qty", "name": "总产量", "unit": "件"},
            "anomalies": [
                {
                    "title": "产线表现",
                    "description": "二线出现异常波动，需继续复核。",
                    "severity": "medium",
                }
            ],
            "recommendations": [
                {
                    "title": "复核二线",
                    "rationale": "依据已冻结异常证据进行复核。",
                    "priority": "medium",
                }
            ],
        },
    )

    content = build_structured_content(
        report=report,
        version=version,
        sections=[section],
        raw_summary="二线出现异常波动，需继续复核。",
        timezone_name="Asia/Shanghai",
    )

    assert [metric["value"] for metric in content["metrics"]] == [12100, 12840]
    assert all(metric["previousValue"] is None for metric in content["metrics"])
    assert all(metric["changeRate"] is None for metric in content["metrics"])
    assert content["trends"] == [
        {
            "metricKey": "output_qty:一线",
            "label": "总产量分布",
            "unit": "件",
            "points": [
                {"label": "一线", "value": 12100.0},
                {"label": "二线", "value": 12840.0},
            ],
        }
    ]
    assert content["charts"] == [
        {
            "key": "output",
            "type": "bar",
            "title": "产线表现",
            "description": "按产线对总产量进行降序比较。",
            "unit": "件",
            "categoryLabel": "production_line",
            "valueLabel": "总产量",
            "points": [
                {"label": "二线", "value": 12840.0},
                {"label": "一线", "value": 12100.0},
            ],
            "evidenceArtifactIds": ["artifact-result"],
            "colorRole": "production",
        }
    ]
    assert content["anomalies"][0]["title"] == "产线表现"
    assert content["recommendations"][0]["evidenceStatus"] == "verified"


def test_agent_selects_pareto_chart_and_calculates_cumulative_share():
    from types import SimpleNamespace

    report = SimpleNamespace(
        id="report-1",
        title="质量周报",
        source_id=1,
        period_start=datetime(2026, 8, 3),
        period_end=datetime(2026, 8, 10),
        scope_json={},
        audience_scope_hash="scope-a",
    )
    version = SimpleNamespace(id="version-1", data_quality_json={"coverageScore": 100})
    section = SimpleNamespace(
        code="quality",
        title="缺陷 Pareto",
        status="succeeded",
        summary="前三类缺陷贡献集中。",
        confidence=96,
        artifact_ids_json=["artifact-quality"],
        content_json={
            "result": {
                "columns": ["缺陷类型", "不良数量"],
                "rows": [["划伤", 60], ["脏污", 30], ["尺寸", 10]],
            },
            "metricResolution": {
                "metricId": "defect_count",
                "name": "不良数量",
                "unit": "件",
                "semantics": {"compositionAllowed": True},
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

    assert content["charts"][0]["type"] == "pareto"
    assert content["charts"][0]["compositionAllowed"] is True
    assert content["charts"][0]["colorRole"] == "quality"
    assert content["charts"][0]["points"] == [
        {"label": "划伤", "value": 60.0, "secondaryValue": 60.0},
        {"label": "脏污", "value": 30.0, "secondaryValue": 90.0},
        {"label": "尺寸", "value": 10.0, "secondaryValue": 100.0},
    ]


def test_agent_selects_pareto_from_a_concentrated_defect_distribution_without_a_keyword():
    from types import SimpleNamespace

    report = SimpleNamespace(
        id="report-1", title="质量周报", source_id=1,
        period_start=datetime(2026, 8, 3), period_end=datetime(2026, 8, 10),
        scope_json={}, audience_scope_hash="scope-a",
    )
    version = SimpleNamespace(id="version-1", data_quality_json={"coverageScore": 100})
    section = SimpleNamespace(
        code="quality", title="缺陷类型分布", status="succeeded", summary="缺陷集中。",
        confidence=95, artifact_ids_json=["artifact-quality"],
        content_json={
            "result": {
                "columns": ["缺陷类型", "不良数量"],
                "rows": [["划伤", 70], ["脏污", 15], ["尺寸", 8], ["色差", 4], ["其他", 3]],
            },
            "metricResolution": {"metricId": "defect_count", "name": "不良数量", "unit": "件", "semantics": {"compositionAllowed": True}},
        },
    )

    content = build_structured_content(
        report=report, version=version, sections=[section],
        raw_summary=section.summary, timezone_name="Asia/Shanghai",
    )

    assert content["charts"][0]["type"] == "pareto"
    assert content["charts"][0]["points"][-1]["secondaryValue"] == 100.0


def test_agent_selects_scatter_for_a_two_measure_relationship():
    from types import SimpleNamespace

    report = SimpleNamespace(
        id="report-1", title="设备周报", source_id=1,
        period_start=datetime(2026, 8, 3), period_end=datetime(2026, 8, 10),
        scope_json={}, audience_scope_hash="scope-a",
    )
    version = SimpleNamespace(id="version-1", data_quality_json={"coverageScore": 100})
    section = SimpleNamespace(
        code="equipment", title="运行时长与停机时长的相关性", status="succeeded", summary="存在关系。",
        confidence=93, artifact_ids_json=["artifact-equipment"],
        content_json={
            "result": {
                "columns": ["设备", "运行时长", "停机时长"],
                "rows": [["A01", 120, 8], ["A02", 150, 4], ["A03", 90, 12]],
            },
            "metricResolution": {"metricId": "downtime", "name": "停机时长", "unit": "分钟"},
        },
    )

    content = build_structured_content(
        report=report, version=version, sections=[section],
        raw_summary=section.summary, timezone_name="Asia/Shanghai",
    )

    chart = content["charts"][0]
    assert chart["type"] == "scatter"
    assert chart["valueLabel"] == "运行时长"
    assert chart["secondaryValueLabel"] == "停机时长"
    assert chart["points"][0] == {"label": "A01", "value": 120.0, "secondaryValue": 8.0}


def test_agent_selects_combo_for_time_series_with_count_and_rate():
    from types import SimpleNamespace

    report = SimpleNamespace(
        id="report-1", title="生产周报", source_id=1,
        period_start=datetime(2026, 8, 3), period_end=datetime(2026, 8, 10),
        scope_json={}, audience_scope_hash="scope-a",
    )
    version = SimpleNamespace(id="version-1", data_quality_json={"coverageScore": 100})
    section = SimpleNamespace(
        code="production", title="日产量与不良率", status="succeeded", summary="联合观察。",
        confidence=95, artifact_ids_json=["artifact-production"],
        content_json={
            "result": {
                "columns": ["日期", "产量", "不良率"],
                "rows": [["2026-08-08", 100, 2.1], ["2026-08-09", 120, 1.7]],
            },
            "metricResolution": {"metricId": "output_qty", "name": "产量", "unit": "件"},
        },
    )

    content = build_structured_content(
        report=report, version=version, sections=[section],
        raw_summary=section.summary, timezone_name="Asia/Shanghai",
    )

    chart = content["charts"][0]
    assert chart["type"] == "combo"
    assert chart["valueLabel"] == "产量"
    assert chart["secondaryValueLabel"] == "不良率"
    assert chart["secondaryUnit"] == "%"
    assert chart["points"][1]["secondaryValue"] == 1.7


def test_agent_does_not_treat_a_numeric_identifier_as_a_second_metric():
    from types import SimpleNamespace

    report = SimpleNamespace(
        id="report-1", title="生产周报", source_id=1,
        period_start=datetime(2026, 8, 3), period_end=datetime(2026, 8, 10),
        scope_json={}, audience_scope_hash="scope-a",
    )
    version = SimpleNamespace(id="version-1", data_quality_json={"coverageScore": 100})
    section = SimpleNamespace(
        code="production", title="一线日产量趋势", status="succeeded", summary="趋势稳定。",
        confidence=95, artifact_ids_json=["artifact-production"],
        content_json={
            "result": {
                "columns": ["日期", "line_id", "产量"],
                "rows": [["2026-08-08", 1, 100], ["2026-08-09", 1, 120]],
            },
            "metricResolution": {"metricId": "output_qty", "name": "产量", "unit": "件"},
        },
    )

    content = build_structured_content(
        report=report, version=version, sections=[section],
        raw_summary=section.summary, timezone_name="Asia/Shanghai",
    )

    assert content["charts"][0]["type"] == "area"
    assert "secondaryValue" not in content["charts"][0]["points"][0]


def test_agent_does_not_treat_year_as_a_second_metric():
    from types import SimpleNamespace

    report = SimpleNamespace(
        id="report-1", title="生产周报", source_id=1,
        period_start=datetime(2026, 8, 3), period_end=datetime(2026, 8, 10),
        scope_json={}, audience_scope_hash="scope-a",
    )
    version = SimpleNamespace(id="version-1", data_quality_json={"coverageScore": 100})
    section = SimpleNamespace(
        code="production", title="日产量趋势", status="succeeded", summary="趋势稳定。",
        confidence=95, artifact_ids_json=["artifact-production"],
        content_json={
            "result": {
                "columns": ["日期", "年份", "产量"],
                "rows": [["2026-08-08", 2026, 100], ["2026-08-09", 2026, 120]],
            },
            "metricResolution": {"metricId": "output_qty", "name": "产量", "unit": "件"},
        },
    )

    content = build_structured_content(
        report=report, version=version, sections=[section],
        raw_summary=section.summary, timezone_name="Asia/Shanghai",
    )

    assert content["charts"][0]["type"] == "area"
    assert "secondaryValue" not in content["charts"][0]["points"][0]


def test_agent_does_not_treat_numeric_shift_as_a_second_metric():
    from types import SimpleNamespace

    report = SimpleNamespace(
        id="report-1", title="生产周报", source_id=1,
        period_start=datetime(2026, 8, 3), period_end=datetime(2026, 8, 10),
        scope_json={}, audience_scope_hash="scope-a",
    )
    version = SimpleNamespace(id="version-1", data_quality_json={"coverageScore": 100})
    section = SimpleNamespace(
        code="production", title="日产量趋势", status="succeeded", summary="趋势稳定。",
        confidence=95, artifact_ids_json=["artifact-production"],
        content_json={
            "result": {
                "columns": ["日期", "班次", "产量"],
                "rows": [["2026-08-08", 1, 100], ["2026-08-09", 2, 120]],
            },
            "metricResolution": {"metricId": "output_qty", "name": "产量", "unit": "件"},
        },
    )

    content = build_structured_content(
        report=report, version=version, sections=[section],
        raw_summary=section.summary, timezone_name="Asia/Shanghai",
    )

    assert content["charts"][0]["type"] == "area"
    assert "secondaryValue" not in content["charts"][0]["points"][0]


def test_agent_requires_a_repeated_cross_dimension_before_using_heatmap():
    from types import SimpleNamespace

    report = SimpleNamespace(
        id="report-1", title="生产周报", source_id=1,
        period_start=datetime(2026, 8, 3), period_end=datetime(2026, 8, 10),
        scope_json={}, audience_scope_hash="scope-a",
    )
    version = SimpleNamespace(id="version-1", data_quality_json={"coverageScore": 100})
    section = SimpleNamespace(
        code="production", title="各产线不良数量", status="succeeded", summary="完成统计。",
        confidence=95, artifact_ids_json=["artifact-production"],
        content_json={
            "result": {
                "columns": ["产线编码", "产线名称", "不良数量"],
                "rows": [
                    ["L01", "一线", 8], ["L02", "二线", 6],
                    ["L03", "三线", 4], ["L04", "四线", 2],
                ],
            },
            "metricResolution": {"metricId": "defect_count", "name": "不良数量", "unit": "件", "semantics": {"compositionAllowed": True}},
        },
    )

    content = build_structured_content(
        report=report, version=version, sections=[section],
        raw_summary=section.summary, timezone_name="Asia/Shanghai",
    )

    assert content["charts"][0]["type"] == "bar"


def test_pareto_keeps_full_population_in_the_cumulative_share():
    from types import SimpleNamespace

    report = SimpleNamespace(
        id="report-1", title="质量周报", source_id=1,
        period_start=datetime(2026, 8, 3), period_end=datetime(2026, 8, 10),
        scope_json={}, audience_scope_hash="scope-a",
    )
    version = SimpleNamespace(id="version-1", data_quality_json={"coverageScore": 100})
    rows = [[f"缺陷-{index:02d}", 100 if index == 1 else 5] for index in range(1, 21)]
    section = SimpleNamespace(
        code="quality", title="缺陷 Pareto", status="succeeded", summary="贡献集中。",
        confidence=95, artifact_ids_json=["artifact-quality"],
        content_json={
            "result": {"columns": ["缺陷类型", "不良数量"], "rows": rows},
            "metricResolution": {
                "metricId": "defect_count",
                "name": "不良数量",
                "unit": "件",
                "semantics": {"compositionAllowed": True},
            },
        },
    )

    content = build_structured_content(
        report=report, version=version, sections=[section],
        raw_summary=section.summary, timezone_name="Asia/Shanghai",
    )

    points = content["charts"][0]["points"]
    assert len(points) == 12
    assert points[-1]["label"] == "其他"
    assert points[-1]["value"] == 45.0
    assert points[-1]["secondaryValue"] == 100.0


def test_agent_selects_temporal_chart_without_mixing_section_series():
    from types import SimpleNamespace

    report = SimpleNamespace(
        id="report-1",
        title="生产周报",
        source_id=1,
        period_start=datetime(2026, 8, 3),
        period_end=datetime(2026, 8, 10),
        scope_json={},
        audience_scope_hash="scope-a",
    )
    version = SimpleNamespace(id="version-1", data_quality_json={"coverageScore": 100})
    section = SimpleNamespace(
        code="production",
        title="日产量趋势",
        status="succeeded",
        summary="日产量稳步增长。",
        confidence=95,
        artifact_ids_json=["artifact-output"],
        content_json={
            "result": {
                "columns": ["日期", "产量"],
                "rows": [["2026-08-08", 100], ["2026-08-09", 120]],
            },
            "metricResolution": {"metricId": "daily_output", "name": "产量", "unit": "件"},
        },
    )

    content = build_structured_content(
        report=report,
        version=version,
        sections=[section],
        raw_summary=section.summary,
        timezone_name="Asia/Shanghai",
    )

    assert content["charts"] == [
        {
            "key": "production",
            "type": "area",
            "title": "日产量趋势",
            "description": "展示产量随日期的变化趋势。",
            "unit": "件",
            "categoryLabel": "日期",
            "valueLabel": "产量",
            "points": [
                {"label": "2026-08-08", "value": 100.0},
                {"label": "2026-08-09", "value": 120.0},
            ],
            "evidenceArtifactIds": ["artifact-output"],
            "colorRole": "production",
        }
    ]


def test_agent_category_result_accepts_metric_id_and_numeric_strings():
    from types import SimpleNamespace

    report = SimpleNamespace(
        id="report-1",
        title="生产经营周报",
        source_id=1,
        period_start=datetime(2026, 7, 6),
        period_end=datetime(2026, 7, 13),
        scope_json={},
        audience_scope_hash="scope-a",
    )
    version = SimpleNamespace(id="version-1", data_quality_json={"coverageScore": 100})
    section = SimpleNamespace(
        code="production",
        title="生产达成与趋势",
        status="succeeded",
        summary=(
            "一线首末日产量增长 25.61%，二线变化率为 -38.71%；"
            "重点偏差阈值为 20%，两条产线合计占比约 40%。"
        ),
        confidence=98,
        artifact_ids_json=["artifact-result"],
        content_json={
            "result": {
                "columns": ["产线", "产量", "首末日变化率_pct"],
                "rows": [
                    ["一线", "159046", "25.61"],
                    ["二线", "153872", "-38.71"],
                ],
            },
            "metricResolution": {
                "metricId": "M-001",
                "name": "产量",
                "definition": "统计周期内良品与不良品数量之和。",
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
    validation = validate_for_publish(content)

    assert content["trends"][0]["label"] == "产量分布"
    assert content["trends"][0]["points"] == [
        {"label": "一线", "value": 159046.0},
        {"label": "二线", "value": 153872.0},
    ]
    assert {metric["value"] for metric in content["metrics"] if metric["unit"] == "%"} == {
        25.61,
        -38.71,
    }
    assert content["managementSummary"]["findings"][0]["text"] == (
        "生产达成与趋势：一线·产量为 159046件，详细分布与证据见正文。"
    )
    assert "V-HARD-007" not in {rule["code"] for rule in validation["rules"]}


def test_temporal_rows_are_sorted_before_current_and_previous_values_are_selected():
    from types import SimpleNamespace

    report = SimpleNamespace(
        id="report-1",
        title="生产经营周报",
        source_id=1,
        period_start=datetime(2026, 8, 3),
        period_end=datetime(2026, 8, 10),
        scope_json={},
        audience_scope_hash="scope-a",
    )
    version = SimpleNamespace(id="version-1", data_quality_json={"coverageScore": 100})
    section = SimpleNamespace(
        code="output",
        title="生产情况",
        status="succeeded",
        summary="产量已汇总。",
        confidence=90,
        artifact_ids_json=["artifact-result"],
        content_json={
            "result": {
                "columns": ["week", "output_qty"],
                "rows": [["W32", 12840], ["W31", 12100]],
            },
            "metricResolution": {"id": "output_qty", "name": "总产量", "unit": "件"},
        },
    )

    content = build_structured_content(
        report=report,
        version=version,
        sections=[section],
        raw_summary="产量已汇总。",
        timezone_name="Asia/Shanghai",
    )

    assert content["metrics"][0]["previousValue"] == 12100
    assert content["metrics"][0]["value"] == 12840
    assert [point["label"] for point in content["trends"][0]["points"]] == ["W31", "W32"]


def test_narrative_negations_never_create_operational_state_without_structured_artifacts():
    from types import SimpleNamespace

    report = SimpleNamespace(
        id="report-1",
        title="生产经营周报",
        source_id=1,
        period_start=datetime(2026, 8, 3),
        period_end=datetime(2026, 8, 10),
        scope_json={},
        audience_scope_hash="scope-a",
    )
    version = SimpleNamespace(id="version-1", data_quality_json={"coverageScore": 42})
    section = SimpleNamespace(
        code="risk",
        title="风险与整改",
        status="succeeded",
        summary="本期未发现异常，风险已解除，但整改尚未完成。",
        confidence=90,
        artifact_ids_json=["artifact-result"],
        content_json={"result": {}},
    )

    content = build_structured_content(
        report=report,
        version=version,
        sections=[section],
        raw_summary=section.summary,
        timezone_name="Asia/Shanghai",
    )

    assert content["anomalies"] == []
    assert content["progress"] == []
    assert content["recommendations"] == []


def test_week_labels_are_ordered_across_iso_year_boundary():
    from types import SimpleNamespace

    report = SimpleNamespace(
        id="report-1",
        title="跨年生产周报",
        source_id=1,
        period_start=datetime(2025, 12, 29),
        period_end=datetime(2026, 1, 5),
        scope_json={},
        audience_scope_hash="scope-a",
    )
    version = SimpleNamespace(id="version-1", data_quality_json={"coverageScore": 100})
    section = SimpleNamespace(
        code="output",
        title="生产情况",
        status="succeeded",
        summary="跨年产量已汇总。",
        confidence=90,
        artifact_ids_json=["artifact-result"],
        content_json={
            "result": {
                "columns": ["week", "output_qty"],
                "rows": [["W1", 12840], ["W52", 12100]],
            },
            "metricResolution": {"id": "output_qty", "name": "总产量", "unit": "件"},
        },
    )

    content = build_structured_content(
        report=report,
        version=version,
        sections=[section],
        raw_summary="跨年产量已汇总。",
        timezone_name="Asia/Shanghai",
    )

    assert content["metrics"][0]["previousValue"] == 12100
    assert content["metrics"][0]["value"] == 12840
    assert [point["label"] for point in content["trends"][0]["points"]] == ["W52", "W1"]
