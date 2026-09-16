"""Versioned structured weekly-report content shared by API and file renderers."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any, Literal

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
    comparison_period_start: datetime | None = Field(default=None, alias="comparisonPeriodStart")
    comparison_period_end: datetime | None = Field(default=None, alias="comparisonPeriodEnd")
    timezone: str = Field(min_length=1, max_length=50)


class Finding(ReportContentModel):
    level: Literal["confirmed", "possible", "attention"]
    section_code: str | None = Field(default=None, alias="sectionCode")
    text: str = Field(min_length=1)
    evidence_artifact_ids: list[str] = Field(alias="evidenceArtifactIds")
    evidence_status: Literal["verified", "inferred", "missing"] = Field(alias="evidenceStatus")
    confidence: int = Field(ge=0, le=100)


class Action(ReportContentModel):
    text: str = Field(min_length=1)
    owner: str | None = None
    due_at: datetime | None = Field(default=None, alias="dueAt")
    priority: Literal["high", "medium", "low"] = "medium"
    evidence_artifact_ids: list[str] = Field(
        default_factory=list, alias="evidenceArtifactIds"
    )
    evidence_status: Literal["verified", "inferred", "missing"] = Field(alias="evidenceStatus")


class ManagementSummary(ReportContentModel):
    findings: list[Finding]
    actions: list[Action]


class Metric(ReportContentModel):
    key: str = Field(min_length=1)
    name: str = Field(min_length=1)
    value: float
    unit: str = Field(min_length=1)
    previous_value: float | None = Field(default=None, alias="previousValue")
    previous_label: str | None = Field(default=None, alias="previousLabel")
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


class ChartPoint(ReportContentModel):
    label: str = Field(min_length=1)
    value: float
    group: str | None = None
    secondary_value: float | None = Field(default=None, alias="secondaryValue")
    previous_value: float | None = Field(default=None, alias="previousValue")


class ChartSpec(ReportContentModel):
    key: str = Field(min_length=1)
    type: Literal["line", "area", "column", "bar", "pareto", "donut", "heatmap", "scatter", "combo", "table", "calendar"]
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=240)
    unit: str = Field(min_length=1, max_length=24)
    category_label: str = Field(alias="categoryLabel", min_length=1, max_length=80)
    value_label: str = Field(alias="valueLabel", min_length=1, max_length=80)
    comparison_label: str | None = Field(default=None, alias="comparisonLabel", min_length=1, max_length=40)
    secondary_value_label: str | None = Field(
        default=None, alias="secondaryValueLabel", min_length=1, max_length=80
    )
    secondary_unit: str | None = Field(
        default=None, alias="secondaryUnit", min_length=1, max_length=24
    )
    points: list[ChartPoint] = Field(min_length=2, max_length=60)
    evidence_artifact_ids: list[str] = Field(alias="evidenceArtifactIds")
    color_role: Literal[
        "production", "quality", "equipment", "inventory", "neutral"
    ] = Field(alias="colorRole")
    width: Literal["half", "full"] = "half"
    height: Literal["compact", "standard", "focus"] = "standard"
    accent: Literal["default", "ocean", "teal", "violet", "graphite"] = "default"
    composition_allowed: bool = Field(default=False, alias="compositionAllowed")
    comparison_state: Literal["ready", "empty", "unavailable", "error"] | None = Field(default=None, alias="comparisonState")


class Anomaly(ReportContentModel):
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    severity: Literal["critical", "high", "medium", "low"]
    metric_key: str | None = Field(default=None, alias="metricKey")
    evidence_artifact_ids: list[str] = Field(alias="evidenceArtifactIds")
    evidence_status: Literal["verified", "inferred", "missing"] = Field(alias="evidenceStatus")
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
    evidence_status: Literal["verified", "inferred", "missing"] = Field(alias="evidenceStatus")


class AudienceScope(ReportContentModel):
    scope_hash: str = Field(alias="scopeHash", min_length=1)
    data_source_ids: list[int] = Field(alias="dataSourceIds", min_length=1)
    sensitive_fields_redacted: bool = Field(alias="sensitiveFieldsRedacted")


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
    charts: list[ChartSpec] = Field(default_factory=list)
    anomalies: list[Anomaly]
    progress: list[ProgressItem]
    recommendations: list[Recommendation]
    audience: AudienceScope
    data_quality: DataQuality = Field(alias="dataQuality")
    appendix: Appendix


_SENSITIVE_KEYS = {
    "password",
    "token",
    "secret",
    "authorization",
    "authorizationcode",
    "smtpauthorizationcode",
}


def _contains_sensitive_key(value: Any) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = re.sub(r"[^a-z]", "", str(key).lower())
            if any(marker in normalized for marker in _SENSITIVE_KEYS) or _contains_sensitive_key(child):
                return True
    elif isinstance(value, list):
        return any(_contains_sensitive_key(item) for item in value)
    elif isinstance(value, str):
        return bool(
            re.search(
                r"(?i)(?:api[_ -]?)?(?:password|token|secret|authorization(?:[_ -]?code)?)\s*[:=]"
                r"|(?:smtp[_ -]?)?(?:password|token|secret|authorization(?:[_ -]?code)?)\s*[:=]"
                r"|(?:授权码|密码|密钥|令牌)\s*[:：=]",
                value,
            )
        )
    return False


def _numeric_claim_errors(report: StructuredWeeklyReport) -> list[str]:
    """Find percentage/unit claims that are not backed by a structured metric."""

    percentage_values = {
        round(value, 4)
        for metric in report.metrics
        for value in (
            ([metric.value] if metric.unit == "%" else [])
            + ([metric.change_rate * 100] if metric.change_rate is not None else [])
        )
    }
    unit_values: dict[str, set[float]] = {}
    for metric in report.metrics:
        unit_values.setdefault(metric.unit, set()).update(
            round(value, 4)
            for value in (metric.value, metric.previous_value)
            if value is not None
        )
    texts = [item.text for item in report.management_summary.findings]
    texts.extend(item.text for item in report.management_summary.actions)
    texts.extend(item.description for item in report.anomalies)
    texts.extend(item.rationale for item in report.recommendations)
    errors: list[str] = []
    for text in texts:
        for matched in re.finditer(r"(?<![A-Za-z0-9])(-?\d+(?:\.\d+)?)\s*%", text):
            value = round(float(matched.group(1)), 4)
            if not any(abs(value - allowed) <= 0.01 for allowed in percentage_values):
                errors.append(matched.group(0).strip())
        for unit, allowed_values in unit_values.items():
            if not unit or unit == "%":
                continue
            pattern = rf"(?<![A-Za-z0-9])(-?\d+(?:\.\d+)?)\s*{re.escape(unit)}"
            for matched in re.finditer(pattern, text):
                value = round(float(matched.group(1)), 4)
                if not any(abs(value - allowed) <= 0.01 for allowed in allowed_values):
                    errors.append(matched.group(0).strip())
    return sorted(set(errors))


def validate_for_publish(content: dict) -> dict:
    """Return deterministic, machine-readable rules for automatic publishing."""

    rules: list[dict] = []
    try:
        report = StructuredWeeklyReport.model_validate(content)
    except ValidationError as exc:
        invalid_rules = [
            {
                "code": "V-HARD-001",
                "level": "error",
                "passed": False,
                "message": "结构化周报不符合 weekly-report/v1。",
                "details": exc.errors(include_url=False),
            }
        ]
        if _contains_sensitive_key(content):
            invalid_rules.append(
                {
                    "code": "V-HARD-009",
                    "level": "error",
                    "passed": False,
                    "message": "接收范围无效或结构化周报仍包含敏感字段。",
                }
            )
        return {
            "passed": False,
            "ruleVersion": "weekly-report-validation-v2",
            "rules": invalid_rules,
            "checkedAt": datetime.now(UTC).isoformat(),
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
    evidence_missing.extend(
        f"chart:{item.key}" for item in report.charts if not item.evidence_artifact_ids
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

    comparative_metrics = [
        metric
        for metric in report.metrics
        if metric.previous_value is not None or metric.change_rate is not None
    ]
    comparison_start = report.identity.comparison_period_start
    comparison_end = report.identity.comparison_period_end
    comparison_invalid = bool(comparative_metrics) and (
        comparison_start is None
        or comparison_end is None
        or comparison_end != report.identity.period_start
        or comparison_start >= comparison_end
        or (
            comparison_end - comparison_start
            != report.identity.period_end - report.identity.period_start
        )
    )
    if comparison_invalid:
        rules.append(
            {
                "code": "V-HARD-006",
                "level": "error",
                "passed": False,
                "message": "包含对比指标时必须提供完整且等长的上一比较周期。",
            }
        )

    invalid_change_rates = [
        metric.key
        for metric in comparative_metrics
        if (
            (metric.previous_value is None and metric.change_rate is not None)
            or (metric.previous_value == 0 and metric.change_rate is not None)
            or (
                metric.previous_value not in {None, 0}
                and (
                    metric.change_rate is None
                    or abs(
                        metric.change_rate
                        - (metric.value - metric.previous_value) / abs(metric.previous_value)
                    )
                    > 0.0001
                )
            )
        )
    ]
    if invalid_change_rates:
        rules.append(
            {
                "code": "V-HARD-010",
                "level": "error",
                "passed": False,
                "message": "环比变化率必须与本期值、上期值计算结果一致。",
                "metricKeys": invalid_change_rates,
            }
        )

    inconsistent_claims = _numeric_claim_errors(report)
    if inconsistent_claims:
        rules.append(
            {
                "code": "V-HARD-007",
                "level": "error",
                "passed": False,
                "message": "正文数值必须能回溯到结构化指标。",
                "claims": inconsistent_claims,
            }
        )

    evidence_state_errors: list[str] = []
    for index, item in enumerate(report.management_summary.findings):
        cause_claim = any(word in item.text for word in ("原因", "导致", "由于"))
        if (item.level == "confirmed" or cause_claim) and item.evidence_status != "verified":
            evidence_state_errors.append(f"finding:{index}")
    for index, item in enumerate(report.anomalies):
        if item.evidence_status != "verified":
            evidence_state_errors.append(f"anomaly:{index}")
    if evidence_state_errors:
        rules.append(
            {
                "code": "V-HARD-008",
                "level": "error",
                "passed": False,
                "message": "确认性结论和原因措辞必须使用已验证证据。",
                "items": evidence_state_errors,
            }
        )

    if (
        not report.audience.sensitive_fields_redacted
        or _contains_sensitive_key(content)
    ):
        rules.append(
            {
                "code": "V-HARD-009",
                "level": "error",
                "passed": False,
                "message": "接收范围无效或结构化周报仍包含敏感字段。",
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
        "checkedAt": datetime.now(UTC).isoformat(),
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
