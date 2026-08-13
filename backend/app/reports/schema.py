"""Versioned structured weekly-report content shared by API and file renderers."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class ReportContentModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class ReportIdentity(ReportContentModel):
    report_id: str = Field(alias="reportId", min_length=1)
    version_id: str = Field(alias="versionId", min_length=1)
    title: str = Field(min_length=1, max_length=200)
    report_type: Literal["weekly"] = Field(alias="reportType")
    period_start: datetime = Field(alias="periodStart")
    period_end: datetime = Field(alias="periodEnd")
    timezone: str = Field(min_length=1, max_length=50)


class Finding(ReportContentModel):
    level: Literal["confirmed", "possible", "attention"]
    section_code: str | None = Field(default=None, alias="sectionCode")
    text: str = Field(min_length=1)
    evidence_artifact_ids: list[str] = Field(alias="evidenceArtifactIds")
    confidence: int = Field(ge=0, le=100)


class Action(ReportContentModel):
    text: str = Field(min_length=1)
    owner: str | None = None
    due_at: datetime | None = Field(default=None, alias="dueAt")
    priority: Literal["high", "medium", "low"] = "medium"
    evidence_artifact_ids: list[str] = Field(
        default_factory=list, alias="evidenceArtifactIds"
    )


class ManagementSummary(ReportContentModel):
    findings: list[Finding]
    actions: list[Action]


class Metric(ReportContentModel):
    key: str = Field(min_length=1)
    name: str = Field(min_length=1)
    value: float
    unit: str = Field(min_length=1)
    previous_value: float | None = Field(default=None, alias="previousValue")
    change_rate: float | None = Field(default=None, alias="changeRate")
    trend: Literal["up", "down", "flat", "unknown"] = "unknown"
    evidence_artifact_ids: list[str] = Field(alias="evidenceArtifactIds")
    confidence: int = Field(ge=0, le=100)


class TrendPoint(ReportContentModel):
    label: str = Field(min_length=1)
    value: float


class TrendSeries(ReportContentModel):
    metric_key: str = Field(alias="metricKey", min_length=1)
    label: str = Field(min_length=1)
    unit: str = Field(min_length=1)
    points: list[TrendPoint]


class Anomaly(ReportContentModel):
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    severity: Literal["critical", "high", "medium", "low"]
    metric_key: str | None = Field(default=None, alias="metricKey")
    evidence_artifact_ids: list[str] = Field(alias="evidenceArtifactIds")
    confidence: int = Field(ge=0, le=100)


class ProgressItem(ReportContentModel):
    title: str = Field(min_length=1)
    status: Literal["completed", "on_track", "at_risk", "blocked"]
    note: str = ""
    completion_rate: float | None = Field(
        default=None, alias="completionRate", ge=0, le=100
    )


class Recommendation(ReportContentModel):
    title: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    priority: Literal["high", "medium", "low"]
    evidence_artifact_ids: list[str] = Field(alias="evidenceArtifactIds")


class DataQuality(ReportContentModel):
    coverage: int = Field(ge=0, le=100)
    delayed_sources: list[str] = Field(alias="delayedSources")
    missing_metrics: list[str] = Field(alias="missingMetrics")
    temporary_metrics: list[str] = Field(alias="temporaryMetrics")
    failed_sections: list[str] = Field(alias="failedSections")


class Definition(ReportContentModel):
    name: str = Field(min_length=1)
    definition: str = Field(min_length=1)


class Appendix(ReportContentModel):
    sources: list[str]
    definitions: list[Definition]


class StructuredWeeklyReport(ReportContentModel):
    schema_version: Literal["weekly-report/v1"] = Field(alias="schemaVersion")
    identity: ReportIdentity
    management_summary: ManagementSummary = Field(alias="managementSummary")
    metrics: list[Metric]
    trends: list[TrendSeries]
    anomalies: list[Anomaly]
    progress: list[ProgressItem]
    recommendations: list[Recommendation]
    data_quality: DataQuality = Field(alias="dataQuality")
    appendix: Appendix


def validate_for_publish(content: dict) -> dict:
    """Return deterministic, machine-readable rules for automatic publishing."""

    rules: list[dict] = []
    try:
        report = StructuredWeeklyReport.model_validate(content)
    except ValidationError as exc:
        return {
            "passed": False,
            "ruleVersion": "weekly-report-validation-v2",
            "rules": [
                {
                    "code": "V-HARD-001",
                    "level": "error",
                    "passed": False,
                    "message": "结构化周报不符合 weekly-report/v1。",
                    "details": exc.errors(include_url=False),
                }
            ],
            "checkedAt": datetime.now(timezone.utc).isoformat(),
        }

    if report.identity.period_end <= report.identity.period_start:
        rules.append(
            {
                "code": "V-HARD-002",
                "level": "error",
                "passed": False,
                "message": "报告结束时间必须晚于开始时间。",
            }
        )

    metric_keys = {metric.key for metric in report.metrics}
    dangling_trends = sorted(
        trend.metric_key for trend in report.trends if trend.metric_key not in metric_keys
    )
    if dangling_trends:
        rules.append(
            {
                "code": "V-HARD-003",
                "level": "error",
                "passed": False,
                "message": "趋势序列引用了不存在的指标。",
                "metricKeys": dangling_trends,
            }
        )

    evidence_missing = [
        f"finding:{index}"
        for index, item in enumerate(report.management_summary.findings)
        if not item.evidence_artifact_ids
    ]
    evidence_missing.extend(
        f"metric:{item.key}" for item in report.metrics if not item.evidence_artifact_ids
    )
    evidence_missing.extend(
        f"anomaly:{index}"
        for index, item in enumerate(report.anomalies)
        if not item.evidence_artifact_ids
    )
    evidence_missing.extend(
        f"recommendation:{index}"
        for index, item in enumerate(report.recommendations)
        if not item.evidence_artifact_ids
    )
    if evidence_missing:
        rules.append(
            {
                "code": "V-HARD-004",
                "level": "error",
                "passed": False,
                "message": "重要结论必须绑定证据。",
                "items": evidence_missing,
            }
        )

    if report.data_quality.coverage >= 60 and not report.metrics:
        rules.append(
            {
                "code": "V-HARD-005",
                "level": "error",
                "passed": False,
                "message": "正常版周报必须包含结构化指标，数据不足时应使用数据异常版。",
            }
        )

    if not rules:
        rules.append(
            {
                "code": "V-HARD-000",
                "level": "info",
                "passed": True,
                "message": "全部自动发布硬规则通过。",
            }
        )
    return {
        "passed": not any(rule["level"] == "error" for rule in rules),
        "ruleVersion": "weekly-report-validation-v2",
        "rules": rules,
        "checkedAt": datetime.now(timezone.utc).isoformat(),
    }


def calculate_confidence(data_quality: dict) -> dict:
    """Calculate a transparent confidence score from data-quality penalties."""

    quality = DataQuality.model_validate(data_quality)
    deductions = [
        {
            "code": "LOW_COVERAGE",
            "points": max(0, round((100 - quality.coverage) / 2)),
        },
        {"code": "DELAYED_SOURCE", "points": min(16, len(quality.delayed_sources) * 8)},
        {"code": "MISSING_METRIC", "points": min(30, len(quality.missing_metrics) * 10)},
        {"code": "TEMPORARY_METRIC", "points": min(14, len(quality.temporary_metrics) * 7)},
        {"code": "FAILED_SECTION", "points": min(36, len(quality.failed_sections) * 12)},
    ]
    deductions = [item for item in deductions if item["points"]]
    score = max(0, 100 - sum(item["points"] for item in deductions))
    level = "high" if score >= 85 else "medium" if score >= 60 else "low"
    return {
        "algorithmVersion": "weekly-confidence/v1",
        "score": score,
        "level": level,
        "templateVariant": "standard" if score >= 60 else "data_anomaly",
        "deductions": deductions,
    }


def content_checksum(content: dict) -> str:
    """Return a stable SHA-256 for semantically equivalent JSON objects."""

    canonical = json.dumps(
        content,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=lambda value: value.isoformat() if isinstance(value, datetime) else str(value),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
