"""报告编排服务：复用阶段五 Agent，并把产物冻结为可复核版本。"""

from __future__ import annotations

import html
from datetime import datetime
from typing import Any

from sqlalchemy import func

from app.agent import service as analysis_service
from app.agent.ids import new_id
from app.config import settings
from app.db.models import (
    AnalysisArtifact,
    AnalysisMessage,
    AnalysisRun,
    AnalysisRunStep,
    AnalysisTask,
    DataSource,
    KnowledgeMetric,
    MetricSourceBinding,
    Report,
    ReportDelivery,
    ReportFile,
    ReportSection,
    ReportSubscription,
    ReportTemplate,
    ReportVersion,
)
from app.db.platform import PlatformSession
from app.reports import feishu
from app.reports.repository import publish_version


class ReportNotFound(KeyError):
    pass


class ReportConflict(RuntimeError):
    pass


RUNNING_STATUS = {
    "queued": "queued",
    "running": "running_sections",
    "retrying": "running_sections",
    "waiting_user": "waiting_user",
}


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _coverage(session, template: ReportTemplate, source_id: int) -> dict:
    required = list(dict.fromkeys(
        metric
        for section in (template.sections_json or [])
        for metric in section.get("metrics", [])
    ))
    if not required:
        return {
            "score": 100,
            "available": 0,
            "required": 0,
            "missingMetrics": [],
            "ready": True,
            "sections": [
                {"code": section["code"], "title": section["title"], "score": 100, "missingMetrics": []}
                for section in (template.sections_json or [])
            ],
        }
    published = {
        row.name: row.id
        for row in session.query(KnowledgeMetric)
        .filter(KnowledgeMetric.name.in_(required), KnowledgeMetric.status == "已发布")
        .all()
    }
    verified_ids = {
        row[0]
        for row in session.query(MetricSourceBinding.metric_id)
        .filter(
            MetricSourceBinding.source_id == source_id,
            MetricSourceBinding.status == "已验证",
            MetricSourceBinding.metric_id.in_(list(published.values()) or ["__none__"]),
        )
        .all()
    }
    available = {name for name, metric_id in published.items() if metric_id in verified_ids}
    sections = []
    for item in template.sections_json or []:
        metrics = item.get("metrics", [])
        missing = [name for name in metrics if name not in available]
        score = round((len(metrics) - len(missing)) / len(metrics) * 100) if metrics else 100
        sections.append({
            "code": item["code"],
            "title": item["title"],
            "score": score,
            "missingMetrics": missing,
        })
    score = round(len(available) / len(required) * 100)
    return {
        "score": score,
        "available": len(available),
        "required": len(required),
        "missingMetrics": [name for name in required if name not in available],
        "ready": score >= template.min_coverage,
        "sections": sections,
    }


def list_templates(source_id: int | None = None, session_factory=PlatformSession) -> list[dict]:
    with session_factory() as session:
        rows = session.query(ReportTemplate).filter_by(enabled=True).order_by(ReportTemplate.id).all()
        return [_template_dict(row, _coverage(session, row, source_id) if source_id else None) for row in rows]


def get_template_coverage(template_id: str, source_id: int, session_factory=PlatformSession) -> dict:
    with session_factory() as session:
        template = session.query(ReportTemplate).filter_by(id=template_id, enabled=True).first()
        if template is None:
            raise ReportNotFound(f"报告模板 {template_id} 不存在")
        source = session.query(DataSource).filter_by(id=source_id).first()
        if source is None:
            raise ReportNotFound(f"数据源 {source_id} 不存在")
        return {
            "template": _template_dict(template),
            "source": {"id": source.id, "name": source.name, "status": source.status},
            "coverage": _coverage(session, template, source_id),
        }


def create_report(payload, session_factory=PlatformSession) -> dict:
    """创建报告身份和草稿版本，再提交一个真实 Agent 多任务 Run。"""
    with session_factory() as session:
        template = session.query(ReportTemplate).filter_by(id=payload.templateId, enabled=True).first()
        source = session.query(DataSource).filter_by(id=payload.sourceId).first()
        if template is None:
            raise ReportNotFound(f"报告模板 {payload.templateId} 不存在")
        if source is None:
            raise ReportNotFound(f"数据源 {payload.sourceId} 不存在")
        coverage = _coverage(session, template, source.id)
        if not coverage["ready"]:
            missing = "、".join(coverage["missingMetrics"])
            raise ReportConflict(
                f"当前数据源只覆盖模板所需指标的 {coverage['score']}%，低于 {template.min_coverage}%：{missing}"
            )
        title = (payload.title or _default_title(template, payload.periodStart, payload.periodEnd)).strip()
        report = Report(
            id=new_id("rpt"),
            title=title,
            template_id=template.id,
            source_id=source.id,
            report_type=template.report_type,
            topic=template.topic,
            status="queued",
            owner_id=payload.ownerId,
            period_start=payload.periodStart,
            period_end=payload.periodEnd,
            scope_json={
                **(payload.scope or {}),
                "prompt": (payload.prompt or "").strip(),
                "templateCoverage": coverage,
            },
        )
        version = ReportVersion(
            id=new_id("rpv"),
            report_id=report.id,
            version=1,
            status="generating",
            content_json={"templateId": template.id, "sections": []},
            data_snapshot_json={"sourceId": source.id, "coverage": coverage},
        )
        report.current_version_id = version.id
        session.add_all([report, version])
        for order, item in enumerate(template.sections_json or [], start=1):
            session.add(
                ReportSection(
                    id=new_id("rps"),
                    report_id=report.id,
                    version_id=version.id,
                    code=item["code"],
                    title=item["title"],
                    section_order=order,
                    status="pending",
                    content_json={"question": _section_question(item, payload.prompt)},
                )
            )
        session.commit()
        report_id = report.id
        question = _build_agent_question(report, template, payload.prompt)

    try:
        analysis_session = analysis_service.create_session(
            f"报告生成 · {title}", payload.sourceId, payload.ownerId
        )
        accepted = analysis_service.create_run(
            analysis_session["id"],
            question,
            idempotency_key=f"report:{report_id}:v1",
        )
        with session_factory() as session:
            report = session.query(Report).filter_by(id=report_id).with_for_update().one()
            report.analysis_session_id = analysis_session["id"]
            report.analysis_run_id = accepted["runId"]
            report.status = "queued"
            session.commit()
    except Exception as exc:
        with session_factory() as session:
            report = session.query(Report).filter_by(id=report_id).with_for_update().one()
            report.status = "failed"
            report.error_json = {
                "code": "REPORT_AGENT_START_FAILED",
                "message": str(exc),
            }
            session.commit()
        raise
    return get_report(report_id, session_factory=session_factory)


def _build_agent_question(report: Report, template: ReportTemplate, prompt: str | None) -> str:
    period_start = report.period_start.strftime("%Y-%m-%d")
    period_end = report.period_end.strftime("%Y-%m-%d")
    questions = []
    for index, section in enumerate(template.sections_json or [], start=1):
        question = _section_question(section, prompt)
        questions.append(
            f"{index}. {question}。统计范围固定为 {period_start} 至 {period_end}，"
            "除非章节明确要求比较，否则不要改变范围；请给出适合该数据形态的图表或结果表。"
        )
    return (
        f"为《{report.title}》完成以下相互独立但范围一致的分析任务。"
        "每一项必须拆成独立任务，所有数值只能来自当前数据源，结论需披露口径、数据不足和临时推导：\n"
        + "\n".join(questions)
    )


def _section_question(section: dict, prompt: str | None) -> str:
    question = section.get("question") or section.get("title") or "完成业务分析"
    return question.replace("{prompt}", (prompt or "").strip())


def _default_title(template: ReportTemplate, start: datetime, end: datetime) -> str:
    period = f"{start:%Y-%m-%d} 至 {end:%Y-%m-%d}"
    return f"{period} {template.name}"


def list_reports(
    *,
    owner_id: str = "local-test-user",
    status: str | None = None,
    source_id: int | None = None,
    session_factory=PlatformSession,
) -> list[dict]:
    with session_factory() as session:
        query = session.query(Report).filter_by(owner_id=owner_id)
        if status:
            query = query.filter_by(status=status)
        if source_id:
            query = query.filter_by(source_id=source_id)
        ids = [row.id for row in query.order_by(Report.updated_at.desc()).all()]
    return [get_report(report_id, session_factory=session_factory, include_sections=False) for report_id in ids]


def get_report(
    report_id: str,
    *,
    session_factory=PlatformSession,
    include_sections: bool = True,
) -> dict:
    sync_report(report_id, session_factory=session_factory)
    with session_factory() as session:
        row = session.query(Report).filter_by(id=report_id).first()
        if row is None:
            raise ReportNotFound(f"报告 {report_id} 不存在")
        source = session.query(DataSource).filter_by(id=row.source_id).first()
        template = session.query(ReportTemplate).filter_by(id=row.template_id).first()
        version = (
            session.query(ReportVersion).filter_by(id=row.current_version_id).first()
            if row.current_version_id
            else None
        )
        sections = (
            session.query(ReportSection)
            .filter_by(report_id=row.id, version_id=row.current_version_id)
            .order_by(ReportSection.section_order)
            .all()
            if include_sections and row.current_version_id
            else []
        )
        deliveries = (
            session.query(ReportDelivery)
            .filter_by(report_id=row.id)
            .order_by(ReportDelivery.created_at.desc())
            .limit(10)
            .all()
            if include_sections
            else []
        )
        files = (
            session.query(ReportFile)
            .filter_by(report_id=row.id, version_id=row.current_version_id)
            .order_by(ReportFile.format)
            .all()
            if include_sections and row.current_version_id
            else []
        )
        result = _report_dict(row, source, template, version)
        if include_sections:
            result["sections"] = [_section_dict(item) for item in sections]
            result["deliveries"] = [_delivery_dict(item) for item in deliveries]
            result["files"] = [_file_dict(item) for item in files]
        return result


def sync_report(report_id: str, session_factory=PlatformSession) -> None:
    """把 Agent 的实时状态与终态产物投影到报告，不修改已发布版本。"""
    with session_factory() as session:
        report = session.query(Report).filter_by(id=report_id).with_for_update().first()
        if report is None:
            raise ReportNotFound(f"报告 {report_id} 不存在")
        if not report.analysis_run_id or report.status == "published":
            return
        run = session.query(AnalysisRun).filter_by(id=report.analysis_run_id).first()
        if run is None:
            return
        if run.status in RUNNING_STATUS:
            report.status = RUNNING_STATUS[run.status]
            _sync_running_sections(session, report, run)
            session.commit()
            return
        if run.status not in {"succeeded", "failed", "blocked", "cancelled"}:
            return
        _finalize_report(session, report, run)
        session.commit()


def sync_report_from_run(run_id: str, session_factory=PlatformSession) -> None:
    with session_factory() as session:
        report_id = session.query(Report.id).filter_by(analysis_run_id=run_id).scalar()
    if report_id:
        sync_report(report_id, session_factory=session_factory)


def _sync_running_sections(session, report: Report, run: AnalysisRun) -> None:
    tasks = session.query(AnalysisTask).filter_by(run_id=run.id).order_by(AnalysisTask.task_order).all()
    sections = (
        session.query(ReportSection)
        .filter_by(report_id=report.id, version_id=report.current_version_id)
        .order_by(ReportSection.section_order)
        .all()
    )
    for section, task in zip(sections, tasks):
        section.analysis_task_id = task.id
        section.status = {
            "pending": "pending",
            "running": "running",
            "waiting": "waiting_user",
            "succeeded": "succeeded",
            "failed": "failed",
        }.get(task.status, task.status)


def _finalize_report(session, report: Report, run: AnalysisRun) -> None:
    version = session.query(ReportVersion).filter_by(id=report.current_version_id).one()
    if version.status in {"awaiting_review", "review_pending", "approved", "published"}:
        return
    tasks = session.query(AnalysisTask).filter_by(run_id=run.id).order_by(AnalysisTask.task_order).all()
    sections = (
        session.query(ReportSection)
        .filter_by(report_id=report.id, version_id=version.id)
        .order_by(ReportSection.section_order)
        .all()
    )
    all_artifacts = session.query(AnalysisArtifact).filter_by(run_id=run.id).order_by(AnalysisArtifact.created_at).all()
    by_task: dict[str, list[AnalysisArtifact]] = {}
    for artifact in all_artifacts:
        if artifact.task_id:
            by_task.setdefault(artifact.task_id, []).append(artifact)

    succeeded = 0
    confidence_scores: list[int] = []
    frozen_sections = []
    for section, task in zip(sections, tasks):
        section.analysis_task_id = task.id
        artifacts = by_task.get(task.id, [])
        artifact_by_type = {item.type: item for item in artifacts}
        conclusion = (artifact_by_type.get("conclusion").content_json or {}) if artifact_by_type.get("conclusion") else {}
        result = (artifact_by_type.get("result").content_json or {}) if artifact_by_type.get("result") else {}
        sql = (artifact_by_type.get("sql").content_json or {}) if artifact_by_type.get("sql") else {}
        validation = (artifact_by_type.get("validation").content_json or {}) if artifact_by_type.get("validation") else {}
        context = (artifact_by_type.get("context").content_json or {}) if artifact_by_type.get("context") else {}
        confidence = conclusion.get("confidence") or {}
        score = int(confidence.get("score") or 0)
        section.status = "succeeded" if task.status == "succeeded" and conclusion else task.status
        section.summary = conclusion.get("text") or ""
        section.confidence = score
        section.artifact_ids_json = [item.id for item in artifacts]
        section.content_json = {
            "question": task.normalized_question,
            "result": result,
            "sql": sql,
            "validation": validation,
            "context": {
                "coverage": context.get("coverage"),
                "metrics": context.get("metrics", []),
                "source": context.get("source"),
            },
            "confidence": confidence,
            "metricResolution": conclusion.get("metricResolution") or sql.get("metricResolution"),
        }
        if section.status == "succeeded":
            succeeded += 1
        if score:
            confidence_scores.append(score)
        frozen_sections.append(_section_dict(section))

    # 若意图层未按章节拆出足够任务，保留明确失败原因而不是伪造章节。
    for section in sections[len(tasks):]:
        section.status = "failed"
        section.error_json = {
            "code": "REPORT_SECTION_NOT_PLANNED",
            "message": "Agent 未生成对应章节任务，可单独重试该章节。",
        }
        frozen_sections.append(_section_dict(section))

    assistant = (
        session.query(AnalysisMessage)
        .filter_by(run_id=run.id, role="assistant")
        .order_by(AnalysisMessage.sequence_no.desc())
        .first()
    )
    summary = assistant.content if assistant else "\n\n".join(item.summary for item in sections if item.summary)
    report_status = "awaiting_review" if run.status == "succeeded" and succeeded == len(sections) else (
        "partial" if succeeded else "failed"
    )
    report.status = report_status
    report.error_json = None if report_status == "awaiting_review" else {
        "code": run.error_code or "REPORT_PARTIAL",
        "message": "部分章节未完成，可查看章节状态和 Agent 证据链。",
    }
    version.status = report_status
    version.summary = summary
    version.generated_at = datetime.now()
    version.content_json = {
        "summary": summary,
        "sections": frozen_sections,
        "confidence": round(sum(confidence_scores) / len(confidence_scores)) if confidence_scores else 0,
    }
    version.data_snapshot_json = {
        **(version.data_snapshot_json or {}),
        "analysisRunId": run.id,
        "sourceId": run.source_id,
        "artifactChecksums": {item.id: item.checksum for item in all_artifacts},
        "generatedAt": datetime.now().isoformat(),
    }
    if report.subscription_id and run.status == "succeeded":
        subscription = session.query(ReportSubscription).filter_by(id=report.subscription_id).first()
        if subscription and subscription.publish_policy == "auto_publish":
            from app.reports.automation import finalize_automatic_report

            finalize_automatic_report(
                session,
                report=report,
                version=version,
                sections=sections,
                raw_summary=summary,
                timezone_name=subscription.timezone,
            )


def submit_review(report_id: str, user_id: str, session_factory=PlatformSession) -> dict:
    return _transition(report_id, {"awaiting_review", "partial"}, "review_pending", user_id, session_factory)


def approve_report(report_id: str, user_id: str, session_factory=PlatformSession) -> dict:
    with session_factory() as session:
        report = session.query(Report).filter_by(id=report_id).with_for_update().first()
        if report is None:
            raise ReportNotFound(f"报告 {report_id} 不存在")
        if report.status != "review_pending":
            raise ReportConflict("只有待复核报告可以通过复核")
        version = session.query(ReportVersion).filter_by(id=report.current_version_id).one()
        report.status = "approved"
        report.reviewer_id = user_id
        version.status = "approved"
        version.approved_by = user_id
        session.commit()
    return get_report(report_id, session_factory=session_factory)


def publish_report(
    report_id: str,
    user_id: str,
    *,
    push_to_feishu: bool = False,
    session_factory=PlatformSession,
) -> dict:
    with session_factory() as session:
        report = session.query(Report).filter_by(id=report_id).with_for_update().first()
        if report is None:
            raise ReportNotFound(f"报告 {report_id} 不存在")
        if report.status not in {"approved", "published"}:
            raise ReportConflict("报告通过复核后才能发布")
        version = session.query(ReportVersion).filter_by(id=report.current_version_id).one()
        if report.report_type == "weekly" and (version.content_json or {}).get("schemaVersion") != "weekly-report/v1":
            from app.reports.automation import build_structured_content, validate_generated_version
            from app.reports.schema import calculate_confidence

            sections = (
                session.query(ReportSection)
                .filter_by(report_id=report.id, version_id=version.id)
                .order_by(ReportSection.section_order)
                .all()
            )
            timezone_name = str((report.scope_json or {}).get("timezone") or "Asia/Shanghai")
            structured = build_structured_content(
                report=report,
                version=version,
                sections=sections,
                raw_summary=version.summary,
                timezone_name=timezone_name,
            )
            validation = validate_generated_version(sections, structured)
            if not validation["passed"]:
                raise ReportConflict("周报结构化校验未通过，不能发布或生成下载文件")
            confidence = calculate_confidence(structured["dataQuality"])
            version.content_json = structured
            version.schema_version = structured["schemaVersion"]
            version.validation_json = validation
            version.confidence_score = confidence["score"]
            version.confidence_algorithm_version = confidence["algorithmVersion"]
            version.data_quality_json = {
                **(version.data_quality_json or {}),
                "confidence": confidence,
            }
        report.reviewer_id = report.reviewer_id or user_id
        publish_version(
            session,
            report_id=report.id,
            version_id=version.id,
            event_id=new_id("evt"),
            idempotency_key=f"report-version-published:{version.id}",
            event_type="report.version.published",
            event_payload={"publishSource": "manual"},
        )
        session.commit()
    if push_to_feishu:
        deliver_to_feishu(report_id, session_factory=session_factory)
    return get_report(report_id, session_factory=session_factory)


def _transition(report_id, allowed, target, user_id, session_factory):
    with session_factory() as session:
        report = session.query(Report).filter_by(id=report_id).with_for_update().first()
        if report is None:
            raise ReportNotFound(f"报告 {report_id} 不存在")
        if report.status not in allowed:
            raise ReportConflict(f"报告当前状态 {report.status} 不能变更为 {target}")
        version = session.query(ReportVersion).filter_by(id=report.current_version_id).one()
        report.status = target
        version.status = target
        if target == "review_pending":
            report.reviewer_id = user_id
        session.commit()
    return get_report(report_id, session_factory=session_factory)


def deliver_to_feishu(
    report_id: str,
    *,
    subscription_id: str | None = None,
    session_factory=PlatformSession,
    client=None,
) -> dict:
    report = get_report(report_id, session_factory=session_factory)
    if report["status"] != "published":
        raise ReportConflict("只有已发布报告可以推送到飞书")
    report["reportUrl"] = f"{settings.public_app_url.rstrip('/')}/workspace/reports/{report_id}"
    with session_factory() as session:
        attempt = (
            session.query(func.count(ReportDelivery.id))
            .filter_by(report_id=report_id, channel="feishu")
            .scalar()
            + 1
        )
        delivery = ReportDelivery(
            id=new_id("dlv"),
            report_id=report_id,
            version_id=report["version"]["id"],
            subscription_id=subscription_id,
            channel="feishu",
            status="sending",
            attempt=attempt,
        )
        session.add(delivery)
        session.commit()
        delivery_id = delivery.id
    outcome = feishu.send_report(report, client=client)
    with session_factory() as session:
        delivery = session.query(ReportDelivery).filter_by(id=delivery_id).with_for_update().one()
        delivery.status = "sent" if outcome.ok else "failed"
        delivery.sent_at = datetime.now() if outcome.ok else None
        delivery.response_id = outcome.response_id
        delivery.error_json = None if outcome.ok else {"message": outcome.error}
        session.commit()
        return _delivery_dict(delivery)


def list_subscriptions(owner_id: str, session_factory=PlatformSession) -> list[dict]:
    with session_factory() as session:
        rows = (
            session.query(ReportSubscription)
            .filter_by(owner_id=owner_id)
            .order_by(ReportSubscription.updated_at.desc())
            .all()
        )
        return [_subscription_dict(item) for item in rows]


def create_subscription(payload, session_factory=PlatformSession) -> dict:
    with session_factory() as session:
        if session.query(ReportTemplate).filter_by(id=payload.templateId, enabled=True).first() is None:
            raise ReportNotFound(f"报告模板 {payload.templateId} 不存在")
        if session.query(DataSource).filter_by(id=payload.sourceId).first() is None:
            raise ReportNotFound(f"数据源 {payload.sourceId} 不存在")
        row = ReportSubscription(
            id=new_id("sub"),
            name=payload.name,
            template_id=payload.templateId,
            source_id=payload.sourceId,
            owner_id=payload.ownerId,
            cadence=payload.cadence,
            weekday=payload.weekday if payload.cadence == "weekly" else None,
            day_of_month=payload.dayOfMonth if payload.cadence == "monthly" else None,
            send_time=payload.sendTime,
            timezone=payload.timezone,
            channel=payload.channel,
            publish_policy=payload.publishPolicy,
            enabled=True,
        )
        if row.cadence == "weekly":
            from app.reports.automation import initialize_next_run

            row.next_run_at = initialize_next_run(row)
        session.add(row)
        session.commit()
        return _subscription_dict(row)


def integration_status() -> dict:
    return {
        "feishu": {
            "configured": feishu.configured(),
            "channel": "feishu",
            "mode": "custom_bot_webhook",
            "supportsSignedWebhook": True,
        }
    }


def export_html(report_id: str, session_factory=PlatformSession) -> str:
    report = get_report(report_id, session_factory=session_factory)
    sections = []
    for section in report.get("sections", []):
        sections.append(
            f"<section><h2>{html.escape(section['title'])}</h2>"
            f"<p class='confidence'>可信度 {section['confidence']}% · {html.escape(section['statusLabel'])}</p>"
            f"<div>{html.escape(section.get('summary') or '本章节暂无结论。').replace(chr(10), '<br>')}</div></section>"
        )
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>{html.escape(report['title'])}</title>
<style>
body{{font-family:"Microsoft YaHei",sans-serif;color:#18222d;max-width:920px;margin:40px auto;line-height:1.7}}
header{{border-bottom:2px solid #18222d;padding-bottom:18px}} .meta,.confidence{{color:#62717e;font-size:13px}}
section{{break-inside:avoid;border-bottom:1px solid #e3e8eb;padding:20px 0}} h1{{font-size:28px}} h2{{font-size:18px}}
@media print{{body{{margin:0;max-width:none}}}}
</style></head><body><header><h1>{html.escape(report['title'])}</h1>
<p class="meta">{html.escape(report['sourceName'])} · {report['periodStart'][:10]} 至 {report['periodEnd'][:10]} · 版本 {report['version']['version']}</p>
</header><section><h2>管理摘要</h2><div>{html.escape(report.get('summary') or '').replace(chr(10), '<br>')}</div></section>
{''.join(sections)}</body></html>"""


def _template_dict(row: ReportTemplate, coverage: dict | None = None) -> dict:
    result = {
        "id": row.id,
        "name": row.name,
        "reportType": row.report_type,
        "topic": row.topic,
        "audience": row.audience,
        "description": row.description,
        "sections": row.sections_json or [],
        "minCoverage": row.min_coverage,
        "version": row.version,
    }
    if coverage is not None:
        result["coverage"] = coverage
    return result


def _report_dict(row: Report, source: DataSource | None, template: ReportTemplate | None, version: ReportVersion | None) -> dict:
    status_labels = {
        "queued": "等待执行",
        "checking_coverage": "检查数据覆盖",
        "planning": "生成报告大纲",
        "running_sections": "分析章节",
        "waiting_user": "等待补充条件",
        "validating": "校验报告",
        "composing": "生成摘要",
        "awaiting_review": "待提交复核",
        "review_pending": "待复核",
        "approved": "已通过",
        "published": "已发布",
        "partial": "部分完成",
        "failed": "生成失败",
    }
    content = version.content_json if version else {}
    return {
        "id": row.id,
        "title": row.title,
        "templateId": row.template_id,
        "templateName": template.name if template else row.template_id,
        "sourceId": row.source_id,
        "sourceName": source.name if source else f"数据源 {row.source_id}",
        "reportType": row.report_type,
        "topic": row.topic,
        "status": row.status,
        "statusLabel": status_labels.get(row.status, row.status),
        "ownerId": row.owner_id,
        "reviewerId": row.reviewer_id,
        "periodStart": _iso(row.period_start),
        "periodEnd": _iso(row.period_end),
        "scope": row.scope_json or {},
        "analysisSessionId": row.analysis_session_id,
        "analysisRunId": row.analysis_run_id,
        "summary": (version.summary if version else "") or content.get("summary", ""),
        "confidence": content.get("confidence", 0),
        "version": {
            "id": version.id,
            "version": version.version,
            "status": version.status,
            "generatedAt": _iso(version.generated_at),
            "publishedAt": _iso(version.published_at),
            "approvedBy": version.approved_by,
        } if version else None,
        "error": row.error_json,
        "createdAt": _iso(row.created_at),
        "updatedAt": _iso(row.updated_at),
    }


def _section_dict(row: ReportSection) -> dict:
    labels = {
        "pending": "等待分析",
        "running": "分析中",
        "waiting_user": "等待补充条件",
        "succeeded": "已完成",
        "failed": "失败",
        "blocked": "已拦截",
        "cancelled": "已取消",
    }
    return {
        "id": row.id,
        "code": row.code,
        "title": row.title,
        "order": row.section_order,
        "status": row.status,
        "statusLabel": labels.get(row.status, row.status),
        "summary": row.summary,
        "content": row.content_json or {},
        "confidence": row.confidence,
        "analysisTaskId": row.analysis_task_id,
        "artifactIds": row.artifact_ids_json or [],
        "error": row.error_json,
    }


def _file_dict(row: ReportFile) -> dict:
    return {
        "id": row.id,
        "format": row.format,
        "status": row.status,
        "mimeType": row.mime_type,
        "fileSize": row.file_size,
        "checksum": row.checksum,
        "generatedAt": _iso(row.generated_at),
        "error": row.error_json,
        "downloadUrl": f"/api/v1/report-files/{row.id}/download",
    }


def _subscription_dict(row: ReportSubscription) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "templateId": row.template_id,
        "sourceId": row.source_id,
        "ownerId": row.owner_id,
        "cadence": row.cadence,
        "weekday": row.weekday,
        "dayOfMonth": row.day_of_month,
        "sendTime": row.send_time,
        "timezone": row.timezone,
        "channel": row.channel,
        "publishPolicy": row.publish_policy,
        "enabled": row.enabled,
        "nextRunAt": _iso(row.next_run_at),
        "updatedAt": _iso(row.updated_at),
    }


def _delivery_dict(row: ReportDelivery) -> dict:
    return {
        "id": row.id,
        "reportId": row.report_id,
        "versionId": row.version_id,
        "subscriptionId": row.subscription_id,
        "channel": row.channel,
        "status": row.status,
        "attempt": row.attempt,
        "responseId": row.response_id,
        "error": row.error_json,
        "createdAt": _iso(row.created_at),
        "sentAt": _iso(row.sent_at),
    }

