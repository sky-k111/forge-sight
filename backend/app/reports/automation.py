"""周期周报的轻量调度与 Agent 编排入口。"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.agent.budgets import default_budget
from app.agent.event_store import append_event
from app.agent.ids import new_id
from app.db.models import (
    AnalysisMessage,
    AnalysisRun,
    AnalysisSession,
    DataSource,
    Report,
    ReportSection,
    ReportSubscription,
    ReportTemplate,
    ReportVersion,
)
from app.db.platform import PlatformSession
from app.reports.repository import get_or_create_report, publish_version
from app.reports.schema import calculate_confidence, validate_for_publish

WORKFLOW_VERSION = "weekly-agent-v1"
log = logging.getLogger(__name__)


@dataclass(frozen=True)
class WeeklyPeriod:
    start: datetime
    end: datetime
    schedule_key: str


@dataclass(frozen=True)
class ScanResult:
    scanned: int
    created: int
    report_ids: tuple[str, ...]
    failures: tuple[dict, ...] = ()


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def weekly_period(due_at: datetime, timezone_name: str) -> WeeklyPeriod:
    """返回调度时刻之前已经结束的自然周（周一至下周一）。"""

    local_due = _utc(due_at).astimezone(ZoneInfo(timezone_name))
    current_monday = (local_due - timedelta(days=local_due.weekday())).date()
    period_end = datetime.combine(current_monday, time.min)
    period_start = period_end - timedelta(days=7)
    return WeeklyPeriod(
        start=period_start,
        end=period_end,
        schedule_key=f"weekly:{period_start:%Y-%m-%d}",
    )


def next_weekly_run(
    after: datetime,
    *,
    weekday: int,
    send_time: str,
    timezone_name: str,
) -> datetime:
    """计算严格晚于给定时刻的下一次周调度，数据库中使用 UTC 无时区值。"""

    zone = ZoneInfo(timezone_name)
    local_after = _utc(after).astimezone(zone)
    hour, minute = (int(part) for part in send_time.split(":"))
    days = (weekday - 1 - local_after.weekday()) % 7
    candidate = datetime.combine(
        local_after.date() + timedelta(days=days),
        time(hour, minute),
        tzinfo=zone,
    )
    if candidate <= local_after:
        candidate += timedelta(days=7)
    return candidate.astimezone(UTC).replace(tzinfo=None)


def initialize_next_run(subscription: ReportSubscription, *, now: datetime | None = None) -> datetime:
    if subscription.cadence != "weekly":
        raise ValueError("当前自动生成仅支持 weekly 订阅")
    return next_weekly_run(
        now or datetime.now(UTC),
        weekday=subscription.weekday or 1,
        send_time=subscription.send_time,
        timezone_name=subscription.timezone,
    )


def _scope_hash(owner_id: str, source_id: int) -> str:
    value = json.dumps(
        {"sourceIds": [source_id], "userIds": [owner_id]},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _section_plan(section: dict, period: WeeklyPeriod) -> dict:
    question = section.get("question") or section.get("title") or "完成业务分析"
    return {
        "code": section["code"],
        "title": section["title"],
        "objective": question,
        "metrics": section.get("metrics", []),
        "dimensions": section.get("dimensions", []),
        "comparison": {
            "current": {"start": period.start.isoformat(), "end": period.end.isoformat()},
            "previous": {
                "start": (period.start - timedelta(days=7)).isoformat(),
                "end": period.start.isoformat(),
            },
        },
        "anomalyPolicy": "仅基于可追溯证据标记异常；证据不足时明确写待核实",
        "outputSchema": ["summary", "result", "evidence", "confidence"],
    }


def scan_due_subscriptions(
    *,
    now: datetime | None = None,
    session_factory=PlatformSession,
    limit: int = 50,
) -> ScanResult:
    """扫描到期订阅；每个订阅在一个事务内创建 Report、Version 和 Agent Run。"""

    now_utc = _utc(now or datetime.now(UTC)).replace(tzinfo=None)
    with session_factory() as session:
        due_ids = [
            row[0]
            for row in (
                session.query(ReportSubscription.id)
                .filter(
                    ReportSubscription.enabled.is_(True),
                    ReportSubscription.cadence == "weekly",
                    ReportSubscription.next_run_at.is_not(None),
                    ReportSubscription.next_run_at <= now_utc,
                )
                .order_by(ReportSubscription.next_run_at, ReportSubscription.id)
                .limit(limit)
                .all()
            )
        ]

    created_ids: list[str] = []
    failures: list[dict] = []
    for subscription_id in due_ids:
        try:
            report_id = _schedule_subscription(
                subscription_id,
                now=now_utc,
                session_factory=session_factory,
            )
        except Exception as exc:  # noqa: BLE001 - 失败订阅保留原 next_run，等待后续重试
            log.exception("周报订阅调度失败: %s", subscription_id)
            failures.append({"subscriptionId": subscription_id, "error": str(exc)})
            continue
        if report_id:
            created_ids.append(report_id)
    return ScanResult(
        scanned=len(due_ids),
        created=len(created_ids),
        report_ids=tuple(created_ids),
        failures=tuple(failures),
    )


def _schedule_subscription(subscription_id: str, *, now: datetime, session_factory) -> str | None:
    from app.reports.service import _build_agent_question, _coverage, _default_title, _section_question

    with session_factory() as session:
        query = session.query(ReportSubscription).filter_by(id=subscription_id).with_for_update()
        subscription = query.first()
        if (
            subscription is None
            or not subscription.enabled
            or subscription.next_run_at is None
            or subscription.next_run_at > now
        ):
            return None

        due_at = subscription.next_run_at
        period = weekly_period(due_at, subscription.timezone)
        template = session.query(ReportTemplate).filter_by(id=subscription.template_id, enabled=True).first()
        source = session.query(DataSource).filter_by(id=subscription.source_id).first()
        if template is None or source is None:
            raise ValueError(f"订阅 {subscription.id} 的模板或数据源不可用")

        audience_scope_hash = _scope_hash(subscription.owner_id, subscription.source_id)
        report = get_or_create_report(
            session,
            id=new_id("rpt"),
            title=_default_title(template, period.start, period.end),
            template_id=template.id,
            source_id=source.id,
            report_type=template.report_type,
            topic=template.topic,
            status="queued",
            owner_id=subscription.owner_id,
            subscription_id=subscription.id,
            schedule_key=period.schedule_key,
            audience_scope_hash=audience_scope_hash,
            publish_source="agent",
            period_start=period.start,
            period_end=period.end,
            scope_json={
                "audience": {"userIds": [subscription.owner_id]},
                "dataSourceIds": [source.id],
            },
        )
        created = report.current_version_id is None
        if created:
            coverage = _coverage(session, template, source.id)
            plan = [_section_plan(item, period) for item in (template.sections_json or [])]
            version = ReportVersion(
                id=new_id("rpv"),
                report_id=report.id,
                version=1,
                status="generating",
                content_json={"templateId": template.id, "plan": plan, "sections": []},
                data_snapshot_json={
                    "sourceId": source.id,
                    "period": {
                        "start": period.start.isoformat(),
                        "end": period.end.isoformat(),
                        "cutoffAt": period.end.isoformat(),
                        "previousStart": (period.start - timedelta(days=7)).isoformat(),
                    },
                    "coverage": coverage,
                },
                data_quality_json={
                    "kind": "normal" if coverage["ready"] else "data_anomaly",
                    "coverageScore": coverage["score"],
                    "missingMetrics": coverage["missingMetrics"],
                    "delayedSources": [],
                },
                workflow_version=WORKFLOW_VERSION,
                model_snapshot_json={"agent": "stage5", "evidencePolicy": "verified-only"},
            )
            report.current_version_id = version.id
            analysis_session = AnalysisSession(
                id=new_id("ses"),
                title=f"周报生成 · {report.title}",
                user_id=subscription.owner_id,
                source_id=source.id,
            )
            question = _build_agent_question(report, template, None)
            message = AnalysisMessage(
                id=new_id("msg"),
                session_id=analysis_session.id,
                role="user",
                content=question,
                sequence_no=1,
            )
            run = AnalysisRun(
                id=new_id("run"),
                session_id=analysis_session.id,
                trigger_message_id=message.id,
                idempotency_key=f"report:{report.id}:v1",
                source_id=source.id,
                workflow_version=WORKFLOW_VERSION,
                budget_snapshot_json=default_budget(),
            )
            message.run_id = run.id
            report.analysis_session_id = analysis_session.id
            report.analysis_run_id = run.id
            session.add_all([version, analysis_session, message, run])
            append_event(
                session,
                run,
                "run.accepted",
                {
                    "status": "queued",
                    "question": question,
                    "sourceId": source.id,
                    "trigger": "report_subscription",
                    "reportId": report.id,
                },
            )
            for order, (item, item_plan) in enumerate(zip(template.sections_json or [], plan, strict=False), start=1):
                session.add(
                    ReportSection(
                        id=new_id("rps"),
                        report_id=report.id,
                        version_id=version.id,
                        code=item["code"],
                        title=item["title"],
                        section_order=order,
                        status="pending",
                        content_json={
                            "question": _section_question(item, None),
                            "plan": item_plan,
                        },
                    )
                )

        subscription.next_run_at = next_weekly_run(
            due_at,
            weekday=subscription.weekday or 1,
            send_time=subscription.send_time,
            timezone_name=subscription.timezone,
        )
        session.commit()
        return report.id if created else None


def validate_generated_version(
    sections: list[ReportSection], structured_content: dict | None = None
) -> dict:
    """仅做可重复的确定性校验，不让模型自行判定自己是否可发布。"""

    hard_errors: list[dict] = []
    warnings: list[dict] = []
    for section in sections:
        if section.status != "succeeded":
            hard_errors.append(
                {"code": "SECTION_NOT_SUCCEEDED", "sectionCode": section.code}
            )
        if not section.summary.strip():
            hard_errors.append(
                {"code": "SECTION_CONCLUSION_MISSING", "sectionCode": section.code}
            )
        if not section.artifact_ids_json:
            hard_errors.append(
                {"code": "SECTION_EVIDENCE_MISSING", "sectionCode": section.code}
            )
        validation = (section.content_json or {}).get("validation") or {}
        if validation.get("ok") is False:
            hard_errors.append(
                {"code": "QUERY_VALIDATION_FAILED", "sectionCode": section.code}
            )
        if section.confidence < 60:
            warnings.append(
                {
                    "code": "LOW_CONFIDENCE",
                    "sectionCode": section.code,
                    "confidence": section.confidence,
                }
            )
    structured_validation = (
        validate_for_publish(structured_content) if structured_content is not None else None
    )
    if structured_validation is not None:
        hard_errors.extend(
            {
                "code": rule["code"],
                "message": rule["message"],
            }
            for rule in structured_validation["rules"]
            if rule["level"] == "error"
        )
    return {
        "passed": not hard_errors,
        "hardErrors": hard_errors,
        "warnings": warnings,
        "checkedAt": datetime.now(UTC).isoformat(),
        "ruleVersion": "weekly-report-validation-v2",
        "rules": structured_validation["rules"] if structured_validation else [],
    }


def compose_management_summary(
    raw_summary: str,
    sections: list[ReportSection],
    metrics: list[dict],
) -> dict:
    """从结构化指标生成简洁管理发现，不转抄未结构化的数字。"""

    findings: list[dict] = []
    all_evidence = [
        artifact_id
        for section in sections
        for artifact_id in (section.artifact_ids_json or [])
    ]
    for section in sections:
        evidence = list(section.artifact_ids_json or [])
        section_metric = next(
            (
                metric
                for metric in metrics
                if set(metric.get("evidenceArtifactIds") or []).intersection(evidence)
            ),
            None,
        )
        if section_metric is None:
            continue
        value = section_metric["value"]
        value_label = str(int(value)) if float(value).is_integer() else f"{value:g}"
        title = str(getattr(section, "title", None) or section.code)
        findings.append(
            {
                "level": "confirmed" if section.confidence >= 80 else "possible",
                "sectionCode": section.code,
                "text": (
                    f"{title}：{section_metric['name']}为 {value_label}"
                    f"{section_metric['unit']}，详细分布与证据见正文。"
                ),
                "evidenceArtifactIds": evidence,
                "evidenceStatus": "verified",
                "confidence": section.confidence,
            }
        )
        if len(findings) == 5:
            break
    return {
        "findings": findings,
        "actions": [
            {
                "text": "优先复核低可信度或数据覆盖不足的章节，并在下一周期跟踪变化。",
                "priority": "medium",
                "evidenceArtifactIds": all_evidence,
                "evidenceStatus": "verified" if all_evidence else "missing",
            }
        ],
    }


def _is_temporal_label(value: object) -> bool:
    label = str(value).strip()
    return bool(
        re.fullmatch(r"W\d{1,2}", label, re.IGNORECASE)
        or re.fullmatch(r"\d{4}[-/.]\d{1,2}(?:[-/.]\d{1,2})?", label)
        or re.fullmatch(r"第?\d{1,2}周", label)
    )


def _temporal_sort_key(value: object, reference: datetime) -> tuple[int, object]:
    label = str(value).strip()
    week = re.fullmatch(r"(?:W|第?)(\d{1,2})(?:周)?", label, re.IGNORECASE)
    if week:
        candidates = []
        for year in (reference.year - 1, reference.year, reference.year + 1):
            try:
                candidates.append(datetime.fromisocalendar(year, int(week.group(1)), 1))
            except ValueError:
                continue
        if candidates:
            return 0, min(candidates, key=lambda item: abs(item - reference))
    try:
        return 0, datetime.fromisoformat(label.replace("/", "-"))
    except ValueError:
        return 2, label


def _numeric_value(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    normalized = str(value).strip().replace(",", "").removesuffix("%")
    if not re.fullmatch(r"-?\d+(?:\.\d+)?", normalized):
        return None
    return float(normalized)


def _is_percentage_column(column: str) -> bool:
    normalized = column.lower()
    return any(marker in normalized for marker in ("%", "pct", "率", "占比", "贡献"))


def _is_identifier_column(column: str) -> bool:
    normalized = re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", column.lower())
    return (
        normalized == "id"
        or normalized.endswith("id")
        or any(marker in normalized for marker in ("编号", "编码", "序号", "主键"))
    )


def _is_auxiliary_numeric_column(column: str) -> bool:
    """Exclude numeric dimensions/helpers that are not business measures."""

    normalized = re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", column.lower())
    if _is_identifier_column(column):
        return True
    exact_names = {
        "year", "yearnum", "年份", "年度",
        "month", "monthnum", "monthno", "月份", "月度", "月序号",
        "day", "daynum", "dayno", "日期序号",
        "week", "weeknum", "weekno", "周次", "周序号",
        "rank", "ranking", "排名", "名次",
        "rownum", "rownumber", "sortorder", "排序", "顺序",
    }
    return normalized in exact_names


def _is_measure_column(column: str) -> bool:
    """Recognize business measures; numeric dimensions must not become combo series."""

    if _is_auxiliary_numeric_column(column):
        return False
    normalized = column.lower()
    dimension_markers = (
        "班次", "班组", "季度", "等级", "类别", "类型", "状态", "阶段",
        "shift", "quarter", "grade", "category", "type", "status", "stage",
    )
    if any(marker in normalized for marker in dimension_markers):
        return False
    measure_markers = (
        "%", "率", "占比", "贡献", "数量", "产量", "库存", "缺口", "时长", "时间",
        "金额", "收入", "成本", "温度", "压力", "速度", "能耗", "功率", "重量", "面积",
        "qty", "count", "rate", "ratio", "share", "duration", "minutes", "hours",
        "amount", "revenue", "cost", "temperature", "pressure", "speed", "volume",
    )
    return any(marker in normalized for marker in measure_markers)


def _metric_unit(resolution: dict, column: str) -> str:
    if resolution.get("unit"):
        return str(resolution["unit"])
    if _is_percentage_column(column):
        return "%"
    if "分钟" in column or "时长" in column:
        return "分钟"
    if any(marker in column for marker in ("产量", "数量", "库存", "缺口")):
        return "件"
    return "数值"


def _structured_metrics(
    sections: list[ReportSection], *, period_end: datetime
) -> tuple[list[dict], list[dict], list[dict]]:
    metrics: list[dict] = []
    trends: list[dict] = []
    definitions: list[dict] = []
    seen_keys: set[str] = set()
    for section in sections:
        content = section.content_json or {}
        result = content.get("result") or {}
        columns = [str(item) for item in (result.get("columns") or [])]
        rows = [list(item) for item in (result.get("rows") or []) if isinstance(item, (list, tuple))]
        if not columns or not rows:
            continue
        numeric_indices = [
            index
            for index in range(len(columns))
            if any(index < len(row) and _numeric_value(row[index]) is not None for row in rows)
        ]
        resolution = content.get("metricResolution") or {}
        metric_name = str(resolution.get("name") or "").strip()
        numeric_index = next(
            (
                index
                for index in numeric_indices
                if metric_name and metric_name in columns[index]
            ),
            numeric_indices[-1] if numeric_indices else None,
        )
        if numeric_index is None:
            continue
        numeric_rows = [
            row
            for row in rows
            if numeric_index < len(row)
            and _numeric_value(row[numeric_index]) is not None
        ]
        if not numeric_rows:
            continue
        label_index = next(
            (index for index in range(len(columns)) if index not in numeric_indices),
            next((index for index in range(len(columns)) if index != numeric_index), None),
        )
        temporal = bool(
            len(numeric_rows) > 1
            and label_index is not None
            and all(
                label_index < len(row) and _is_temporal_label(row[label_index])
                for row in numeric_rows
            )
        )
        if temporal and label_index is not None:
            numeric_rows.sort(
                key=lambda row: _temporal_sort_key(row[label_index], period_end)
            )
        key = str(
            resolution.get("id")
            or resolution.get("metricId")
            or columns[numeric_index]
            or section.code
        )
        if key in seen_keys:
            continue
        name = str(resolution.get("name") or columns[numeric_index])
        unit = _metric_unit(resolution, columns[numeric_index])
        evidence = list(section.artifact_ids_json or [])
        if not temporal and len(numeric_rows) > 1 and label_index is not None:
            category_indices = [numeric_index]
            category_indices.extend(
                index
                for index in numeric_indices
                if index != numeric_index and _is_percentage_column(columns[index])
            )
            primary_key = ""
            for column_index in category_indices:
                column_unit = "%" if _is_percentage_column(columns[column_index]) else unit
                column_name = name if column_index == numeric_index else columns[column_index]
                column_key = key if column_index == numeric_index else f"{key}:{columns[column_index]}"
                for row in numeric_rows:
                    value = _numeric_value(row[column_index]) if column_index < len(row) else None
                    if value is None or label_index >= len(row):
                        continue
                    label = str(row[label_index])
                    item_key = f"{column_key}:{label}"
                    if item_key in seen_keys:
                        continue
                    seen_keys.add(item_key)
                    primary_key = primary_key or item_key
                    metrics.append(
                        {
                            "key": item_key,
                            "name": f"{label}·{column_name}",
                            "value": value,
                            "unit": column_unit,
                            "previousValue": None,
                            "changeRate": None,
                            "trend": "unknown",
                            "evidenceArtifactIds": evidence,
                            "confidence": section.confidence,
                        }
                    )
            if primary_key:
                trends.append(
                    {
                        "metricKey": primary_key,
                        "label": f"{name}分布",
                        "unit": unit,
                        "points": [
                            {"label": str(row[label_index]), "value": _numeric_value(row[numeric_index])}
                            for row in numeric_rows[-12:]
                            if label_index < len(row) and _numeric_value(row[numeric_index]) is not None
                        ],
                    }
                )
            definition = resolution.get("definition")
            if definition:
                definitions.append({"name": name, "definition": str(definition)})
            continue
        seen_keys.add(key)
        values = [_numeric_value(row[numeric_index]) for row in numeric_rows]
        previous = values[-2] if len(values) >= 2 else None
        change_rate = (
            (values[-1] - previous) / abs(previous)
            if previous not in {None, 0}
            else None
        )
        trend = (
            "unknown"
            if change_rate is None
            else "up"
            if change_rate > 0.001
            else "down"
            if change_rate < -0.001
            else "flat"
        )
        metrics.append(
            {
                "key": key,
                "name": name,
                "value": values[-1],
                "unit": unit,
                "previousValue": previous,
                "changeRate": change_rate,
                "trend": trend,
                "evidenceArtifactIds": evidence,
                "confidence": section.confidence,
            }
        )
        if len(numeric_rows) >= 2:
            trends.append(
                {
                    "metricKey": key,
                    "label": f"{name}趋势",
                    "unit": unit,
                    "points": [
                        {
                            "label": str(row[label_index]) if label_index is not None and label_index < len(row) else str(index + 1),
                            "value": _numeric_value(row[numeric_index]),
                        }
                        for index, row in enumerate(numeric_rows[-12:])
                    ],
                }
            )
        definition = resolution.get("definition")
        if definition:
            definitions.append({"name": name, "definition": str(definition)})
    return metrics, trends, definitions


def _chart_color_role(section: ReportSection) -> str:
    semantic_text = " ".join(
        (
            str(getattr(section, "code", "")),
            str(getattr(section, "title", "")),
            str(
                (getattr(section, "content_json", None) or {})
                .get("metricResolution", {})
                .get("name", "")
            ),
        )
    ).lower()
    if any(
        marker in semantic_text
        for marker in ("质量", "良率", "缺陷", "不良", "quality", "yield", "defect")
    ):
        return "quality"
    if any(
        marker in semantic_text
        for marker in ("设备", "停机", "故障", "equipment", "downtime")
    ):
        return "equipment"
    if any(
        marker in semantic_text
        for marker in ("库存", "缺口", "inventory", "stock")
    ):
        return "inventory"
    if any(
        marker in semantic_text
        for marker in ("生产", "产量", "产线", "output", "production", "line")
    ):
        return "production"
    return "neutral"


def _chart_axis_label(column: str) -> str:
    normalized = column.strip().lower()
    aliases = {
        "production_line": "产线",
        "line": "产线",
        "date": "日期",
        "day": "日期",
        "week": "周次",
        "process": "工序",
        "equipment": "设备",
        "category": "类别",
    }
    return aliases.get(normalized, column.replace("_", " "))


def _structured_charts(
    sections: list[ReportSection], *, period_end: datetime
) -> list[dict]:
    """Convert each frozen section into one safe, evidence-backed chart specification."""

    charts: list[dict] = []
    used_keys: set[str] = set()
    for section_index, section in enumerate(sections):
        content = section.content_json or {}
        result = content.get("result") or {}
        columns = [str(item) for item in (result.get("columns") or [])]
        rows = [
            list(item)
            for item in (result.get("rows") or [])
            if isinstance(item, (list, tuple))
        ]
        evidence = list(section.artifact_ids_json or [])
        if len(rows) < 2 or not columns or not evidence:
            continue
        numeric_indices = [
            index
            for index in range(len(columns))
            if any(
                index < len(row) and _numeric_value(row[index]) is not None
                for row in rows
            )
        ]
        if not numeric_indices:
            continue
        resolution = content.get("metricResolution") or {}
        metric_name = str(resolution.get("name") or "").strip()
        value_index = next(
            (
                index
                for index in numeric_indices
                if metric_name and metric_name in columns[index]
            ),
            numeric_indices[-1],
        )
        category_indices = [
            index for index in range(len(columns)) if index not in numeric_indices
        ]
        if not category_indices:
            category_indices = [index for index in range(len(columns)) if index != value_index]
        if not category_indices:
            continue
        category_index = category_indices[0]
        category_label = _chart_axis_label(columns[category_index])
        usable_rows = [
            row
            for row in rows
            if category_index < len(row)
            and str(row[category_index]).strip()
            and value_index < len(row)
            and _numeric_value(row[value_index]) is not None
        ]
        if len(usable_rows) < 2:
            continue

        title = str(
            getattr(section, "title", "") or metric_name or columns[value_index]
        )
        unit = _metric_unit(resolution, columns[value_index])
        value_label = metric_name or columns[value_index]
        semantic_text = " ".join(
            (title, str(getattr(section, "code", "")), value_label, *columns)
        ).lower()
        temporal = all(_is_temporal_label(row[category_index]) for row in usable_rows)
        secondary_value_label = None
        secondary_unit = None
        if temporal:
            usable_rows.sort(
                key=lambda row: _temporal_sort_key(row[category_index], period_end)
            )
            secondary_candidates = [
                index for index in numeric_indices
                if index != value_index and _is_measure_column(columns[index])
            ]
            secondary_index = next(
                (
                    index for index in secondary_candidates
                    if all(index < len(row) and _numeric_value(row[index]) is not None for row in usable_rows)
                ),
                None,
            )
            if secondary_index is not None:
                chart_type = "combo"
                secondary_value_label = columns[secondary_index]
                secondary_unit = _metric_unit({}, columns[secondary_index])
                points = [
                    {
                        "label": str(row[category_index]),
                        "value": _numeric_value(row[value_index]),
                        "secondaryValue": _numeric_value(row[secondary_index]),
                    }
                    for row in usable_rows[-12:]
                ]
                description = (
                    f"联合观察{value_label}与{secondary_value_label}随{category_label}的变化。"
                )
            else:
                chart_type = "area" if any(
                    marker in semantic_text
                    for marker in ("产量", "数量", "销售额", "收入", "output", "volume")
                ) else "line"
                points = [
                    {
                        "label": str(row[category_index]),
                        "value": _numeric_value(row[value_index]),
                    }
                    for row in usable_rows[-12:]
                ]
                description = f"展示{value_label}随{category_label}的变化趋势。"
        elif len([index for index in numeric_indices if not _is_auxiliary_numeric_column(columns[index])]) >= 2 and any(
            marker in semantic_text for marker in ("相关", "关系", "影响", "correlation")
        ):
            y_index = value_index
            x_index = next(
                index for index in numeric_indices
                if index != y_index and not _is_auxiliary_numeric_column(columns[index])
            )
            relationship_rows = [
                row for row in usable_rows
                if x_index < len(row) and _numeric_value(row[x_index]) is not None
            ][:60]
            if len(relationship_rows) < 2:
                continue
            chart_type = "scatter"
            value_label = columns[x_index]
            unit = _metric_unit({}, columns[x_index])
            secondary_value_label = metric_name or columns[y_index]
            secondary_unit = _metric_unit(resolution, columns[y_index])
            points = [
                {
                    "label": str(row[category_index]),
                    "value": _numeric_value(row[x_index]),
                    "secondaryValue": _numeric_value(row[y_index]),
                }
                for row in relationship_rows
            ]
            description = f"观察{value_label}与{secondary_value_label}之间的分布关系。"
        elif len(category_indices) >= 2:
            group_index = category_indices[1]
            matrix_points = [
                {
                    "label": str(row[category_index]),
                    "group": str(row[group_index]),
                    "value": _numeric_value(row[value_index]),
                }
                for row in usable_rows
                if group_index < len(row) and str(row[group_index]).strip()
            ][:60]
            labels = {item["label"] for item in matrix_points}
            groups = {item["group"] for item in matrix_points}
            unique_pairs = {(item["label"], item["group"]) for item in matrix_points}
            matrix_capacity = len(labels) * len(groups)
            is_matrix = (
                len(matrix_points) >= 4
                and len(labels) >= 2
                and len(groups) >= 2
                and len(matrix_points) > len(labels)
                and len(matrix_points) > len(groups)
                and matrix_capacity > 0
                and len(unique_pairs) / matrix_capacity >= 0.5
            )
            if is_matrix:
                if len(matrix_points) < 2:
                    continue
                points = matrix_points
                chart_type = "heatmap"
                group_label = _chart_axis_label(columns[group_index])
                description = (
                    f"交叉观察{category_label}与{group_label}的{value_label}分布。"
                )
            else:
                ranked = sorted(
                    usable_rows,
                    key=lambda row: _numeric_value(row[value_index]) or 0,
                    reverse=True,
                )[:12]
                points = [
                    {"label": str(row[category_index]), "value": _numeric_value(row[value_index])}
                    for row in ranked
                ]
                chart_type = "bar"
                description = f"按{category_label}对{value_label}进行降序比较。"
        else:
            ranked_all = sorted(
                usable_rows,
                key=lambda row: _numeric_value(row[value_index]) or 0,
                reverse=True,
            )
            ranked = ranked_all[:12]
            points = [
                {
                    "label": str(row[category_index]),
                    "value": _numeric_value(row[value_index]),
                }
                for row in ranked
            ]
            positive_values = [
                max(float(_numeric_value(row[value_index]) or 0), 0)
                for row in ranked_all
            ]
            total = sum(positive_values)
            concentrated_quality_distribution = (
                len(points) >= 4
                and total > 0
                and any(marker in semantic_text for marker in ("缺陷", "不良", "故障", "质量", "defect"))
                and positive_values[0] / total >= 0.5
            )
            if (
                any(marker in semantic_text for marker in ("pareto", "帕累托", "累计贡献"))
                or concentrated_quality_distribution
            ):
                chart_type = "pareto"
                if len(ranked_all) > 12:
                    visible_rows = ranked_all[:11]
                    other_value = sum(
                        max(float(_numeric_value(row[value_index]) or 0), 0)
                        for row in ranked_all[11:]
                    )
                    points = [
                        {
                            "label": str(row[category_index]),
                            "value": _numeric_value(row[value_index]),
                        }
                        for row in visible_rows
                    ]
                    points.append({"label": "其他", "value": other_value})
                cumulative = 0.0
                for item in points:
                    cumulative += max(float(item["value"] or 0), 0)
                    item["secondaryValue"] = round(cumulative / total * 100, 2) if total else 0.0
                description = f"按{category_label}定位{value_label}的主要贡献项。"
            elif (
                any(
                    marker in semantic_text
                    for marker in ("占比", "构成", "份额", "比例", "share", "composition")
                )
                and len(points) <= 6
                and all(float(item["value"] or 0) >= 0 for item in points)
            ):
                chart_type = "donut"
                description = f"展示不同{category_label}的{value_label}构成。"
            else:
                chart_type = "bar"
                description = f"按{category_label}对{value_label}进行降序比较。"

        # A personal composition is a frozen presentation contract.  Automatic
        # inference remains the fallback for legacy templates, but it must not
        # silently replace a compatible chart explicitly chosen by the user.
        presentation = content.get("presentation") or {}
        requested_chart = str(presentation.get("chart") or "")
        requested_kind = str(presentation.get("kind") or "")
        requested_top_n = max(2, min(int(presentation.get("topN") or 12), 40))
        if requested_kind == "trend" and temporal and requested_chart in {"line", "area", "column"}:
            chart_type = requested_chart
            points = [
                {
                    "label": str(row[category_index]),
                    "value": _numeric_value(row[value_index]),
                }
                for row in usable_rows[-min(requested_top_n, 60):]
            ]
            secondary_value_label = None
            secondary_unit = None
            description = f"按{category_label}展示{value_label}，采用用户选定的图表形式。"
        elif requested_kind == "breakdown" and not temporal and requested_chart in {"bar", "donut", "pareto"}:
            ranked_rows = sorted(
                usable_rows,
                key=lambda row: _numeric_value(row[value_index]) or 0,
                reverse=True,
            )[:requested_top_n]
            points = [
                {
                    "label": str(row[category_index]),
                    "value": _numeric_value(row[value_index]),
                }
                for row in ranked_rows
            ]
            chart_type = requested_chart
            secondary_value_label = None
            secondary_unit = None
            if requested_chart == "pareto":
                total = sum(max(float(item["value"] or 0), 0) for item in points)
                cumulative = 0.0
                for item in points:
                    cumulative += max(float(item["value"] or 0), 0)
                    item["secondaryValue"] = round(cumulative / total * 100, 2) if total else 0.0
                description = f"按{category_label}定位{value_label}的主要贡献项。"
            elif requested_chart == "donut":
                description = f"展示不同{category_label}的{value_label}构成。"
            else:
                description = f"按{category_label}对{value_label}进行降序比较。"

        base_key = str(getattr(section, "code", "") or f"chart-{section_index + 1}")
        key = base_key
        suffix = 2
        while key in used_keys:
            key = f"{base_key}-{suffix}"
            suffix += 1
        used_keys.add(key)
        chart = {
                "key": key,
                "type": chart_type,
                "title": title,
                "description": description,
                "unit": unit,
                "categoryLabel": columns[category_index],
                "valueLabel": value_label,
                "points": points,
                "evidenceArtifactIds": evidence,
                "colorRole": _chart_color_role(section),
            }
        if presentation:
            chart.update({
                "width": presentation.get("width", "half"),
                "height": presentation.get("height", "standard"),
                "accent": presentation.get("accent", "default"),
            })
        if secondary_value_label:
            chart["secondaryValueLabel"] = secondary_value_label
        if secondary_unit:
            chart["secondaryUnit"] = secondary_unit
        charts.append(chart)
    return charts


def _structured_operational_items(sections: list[ReportSection]) -> tuple[list[dict], list[dict], list[dict]]:
    """Map only explicit structured artifacts; narrative keywords are not state."""

    anomalies: list[dict] = []
    progress: list[dict] = []
    recommendations: list[dict] = []
    for section in sections:
        content = section.content_json or {}
        result = content.get("result") or {}
        evidence = list(section.artifact_ids_json or [])
        if not evidence:
            continue
        for item in content.get("anomalies") or result.get("anomalies") or []:
            if not isinstance(item, dict) or not item.get("title") or not item.get("description"):
                continue
            anomalies.append(
                {
                    "title": str(item["title"]),
                    "description": str(item["description"]),
                    "severity": item.get("severity") if item.get("severity") in {"critical", "high", "medium", "low"} else "medium",
                    "metricKey": item.get("metricKey"),
                    "evidenceArtifactIds": evidence,
                    "evidenceStatus": "verified",
                    "confidence": int(item.get("confidence") or section.confidence),
                }
            )
        for item in content.get("progress") or result.get("progress") or []:
            if not isinstance(item, dict) or not item.get("title"):
                continue
            status = item.get("status")
            if status not in {"completed", "on_track", "at_risk", "blocked"}:
                continue
            progress.append(
                {
                    "title": str(item["title"]),
                    "status": status,
                    "note": str(item.get("note") or ""),
                    "completionRate": item.get("completionRate"),
                }
            )
        for item in content.get("recommendations") or result.get("recommendations") or []:
            if not isinstance(item, dict) or not item.get("title") or not item.get("rationale"):
                continue
            recommendations.append(
                {
                    "title": str(item["title"]),
                    "rationale": str(item["rationale"]),
                    "priority": item.get("priority") if item.get("priority") in {"high", "medium", "low"} else "medium",
                    "evidenceArtifactIds": evidence,
                    "evidenceStatus": "verified",
                }
            )
    return anomalies, progress, recommendations


def build_structured_content(
    *,
    report: Report,
    version: ReportVersion,
    sections: list[ReportSection],
    raw_summary: str,
    timezone_name: str,
) -> dict:
    """Build the single weekly-report document consumed by every renderer."""

    quality_source = version.data_quality_json or {}
    data_quality = {
        "coverage": int(quality_source.get("coverageScore") or 0),
        "delayedSources": list(quality_source.get("delayedSources") or []),
        "missingMetrics": list(quality_source.get("missingMetrics") or []),
        "temporaryMetrics": list(quality_source.get("temporaryMetrics") or []),
        "failedSections": [section.code for section in sections if section.status != "succeeded"],
    }
    metrics, trends, definitions = _structured_metrics(
        sections, period_end=report.period_end
    )
    charts = _structured_charts(sections, period_end=report.period_end)
    anomalies, progress, recommendations = _structured_operational_items(sections)
    period_duration = report.period_end - report.period_start
    comparison_start = report.period_start - period_duration
    scope = getattr(report, "scope_json", None) or {}
    scope_hash = getattr(report, "audience_scope_hash", None) or hashlib.sha256(
        json.dumps(
            {"sourceId": report.source_id, "scope": scope},
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    return {
        "schemaVersion": "weekly-report/v1",
        "identity": {
            "reportId": report.id,
            "versionId": version.id,
            "title": report.title,
            "reportType": "weekly",
            "periodStart": report.period_start.isoformat(),
            "periodEnd": report.period_end.isoformat(),
            "comparisonPeriodStart": comparison_start.isoformat(),
            "comparisonPeriodEnd": report.period_start.isoformat(),
            "timezone": timezone_name,
        },
        "managementSummary": compose_management_summary(raw_summary, sections, metrics),
        "metrics": metrics,
        "trends": trends,
        "charts": charts,
        "anomalies": anomalies,
        "progress": progress,
        "recommendations": recommendations,
        "audience": {
            "scopeHash": scope_hash,
            "dataSourceIds": [report.source_id],
            "sensitiveFieldsRedacted": True,
        },
        "dataQuality": data_quality,
        "appendix": {
            "sources": [f"data-source:{report.source_id}"],
            "definitions": definitions,
        },
    }


def finalize_automatic_report(
    session,
    *,
    report: Report,
    version: ReportVersion,
    sections: list[ReportSection],
    raw_summary: str,
    timezone_name: str,
) -> bool:
    """校验并原子发布订阅生成的当前版本；返回是否已发布。"""

    structured_content = build_structured_content(
        report=report,
        version=version,
        sections=sections,
        raw_summary=raw_summary,
        timezone_name=timezone_name,
    )
    confidence = calculate_confidence(structured_content["dataQuality"])
    validation = validate_generated_version(sections, structured_content)
    version.validation_json = validation
    version.schema_version = structured_content["schemaVersion"]
    version.confidence_score = confidence["score"]
    version.confidence_algorithm_version = confidence["algorithmVersion"]
    version.data_quality_json = {
        **(version.data_quality_json or {}),
        "confidence": confidence,
    }
    version.workflow_version = WORKFLOW_VERSION
    if not validation["passed"]:
        report.status = "validation_failed"
        version.status = "validation_failed"
        report.error_json = {
            "code": "REPORT_VALIDATION_FAILED",
            "message": "自动发布校验未通过，请根据 hardErrors 修复后重试。",
        }
        return False

    version.content_json = structured_content
    report.status = "composing"
    version.status = "composing"
    report.publish_source = "agent"
    report.error_json = None
    publish_version(
        session,
        report_id=report.id,
        version_id=version.id,
        event_id=new_id("evt"),
        idempotency_key=f"report-version-published:{version.id}",
        event_type="report.version.published",
        event_payload={
            "subscriptionId": report.subscription_id,
            "period": {
                "start": report.period_start.isoformat(),
                "end": report.period_end.isoformat(),
                "timezone": timezone_name,
            },
            "audienceScopeHash": report.audience_scope_hash,
            "publishSource": "agent",
        },
    )
    return True
