"""周期周报的轻量调度与 Agent 编排入口。"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
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
from app.reports.repository import get_or_create_report
from app.reports.repository import publish_version
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
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


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
    return candidate.astimezone(timezone.utc).replace(tzinfo=None)


def initialize_next_run(subscription: ReportSubscription, *, now: datetime | None = None) -> datetime:
    if subscription.cadence != "weekly":
        raise ValueError("当前自动生成仅支持 weekly 订阅")
    return next_weekly_run(
        now or datetime.now(timezone.utc),
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

    now_utc = _utc(now or datetime.now(timezone.utc)).replace(tzinfo=None)
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
            for order, (item, item_plan) in enumerate(zip(template.sections_json or [], plan), start=1):
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
        "checkedAt": datetime.now(timezone.utc).isoformat(),
        "ruleVersion": "weekly-report-validation-v2",
        "rules": structured_validation["rules"] if structured_validation else [],
    }


def compose_management_summary(raw_summary: str, sections: list[ReportSection]) -> dict:
    """把已验证章节压缩为 3–5 条管理发现，不创造新的业务数字。"""

    findings: list[dict] = []
    all_evidence = [
        artifact_id
        for section in sections
        for artifact_id in (section.artifact_ids_json or [])
    ]
    scored = [section.confidence for section in sections if section.confidence]
    if raw_summary.strip() and all_evidence:
        findings.append(
            {
                "level": "confirmed",
                "text": raw_summary.strip(),
                "evidenceArtifactIds": all_evidence,
                "confidence": round(sum(scored) / len(scored)) if scored else 60,
            }
        )
    for section in sections:
        if not section.summary.strip():
            continue
        findings.append(
            {
                "level": "confirmed" if section.confidence >= 80 else "possible",
                "sectionCode": section.code,
                "text": section.summary.strip(),
                "evidenceArtifactIds": list(section.artifact_ids_json or []),
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
            }
        ],
    }


def _structured_metrics(sections: list[ReportSection]) -> tuple[list[dict], list[dict], list[dict]]:
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
        numeric_index = next(
            (
                index
                for index in range(len(columns) - 1, -1, -1)
                if any(
                    index < len(row)
                    and isinstance(row[index], (int, float))
                    and not isinstance(row[index], bool)
                    for row in rows
                )
            ),
            None,
        )
        if numeric_index is None:
            continue
        numeric_rows = [
            row
            for row in rows
            if numeric_index < len(row)
            and isinstance(row[numeric_index], (int, float))
            and not isinstance(row[numeric_index], bool)
        ]
        if not numeric_rows:
            continue
        resolution = content.get("metricResolution") or {}
        key = str(resolution.get("id") or columns[numeric_index] or section.code)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        values = [float(row[numeric_index]) for row in numeric_rows]
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
        name = str(resolution.get("name") or columns[numeric_index])
        unit = str(resolution.get("unit") or "数值")
        evidence = list(section.artifact_ids_json or [])
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
            label_index = next((index for index in range(len(columns)) if index != numeric_index), None)
            trends.append(
                {
                    "metricKey": key,
                    "label": f"{name}趋势",
                    "unit": unit,
                    "points": [
                        {
                            "label": str(row[label_index]) if label_index is not None and label_index < len(row) else str(index + 1),
                            "value": float(row[numeric_index]),
                        }
                        for index, row in enumerate(numeric_rows[-12:])
                    ],
                }
            )
        definition = resolution.get("definition")
        if definition:
            definitions.append({"name": name, "definition": str(definition)})
    return metrics, trends, definitions


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
    metrics, trends, definitions = _structured_metrics(sections)
    return {
        "schemaVersion": "weekly-report/v1",
        "identity": {
            "reportId": report.id,
            "versionId": version.id,
            "title": report.title,
            "reportType": "weekly",
            "periodStart": report.period_start.isoformat(),
            "periodEnd": report.period_end.isoformat(),
            "timezone": timezone_name,
        },
        "managementSummary": compose_management_summary(raw_summary, sections),
        "metrics": metrics,
        "trends": trends,
        "anomalies": [],
        "progress": [],
        "recommendations": [],
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
