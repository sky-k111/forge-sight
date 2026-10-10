"""阶段六报告编排、版本与飞书适配器。"""

import json
from datetime import datetime

import httpx
import pytest

from app.db.models import (
    AnalysisArtifact,
    AnalysisMessage,
    AnalysisRun,
    AnalysisTask,
    KnowledgeMetric,
    Message,
    MessageRecipient,
    MetricSourceBinding,
    Report,
    ReportFile,
    ReportSection,
    ReportVersion,
)
from app.reports import feishu, service
from app.reports.contracts import ReportCreate, SubscriptionCreate
from app.reports.templates import seed_report_templates


def _enable_metric(session_factory, metric_id: str, name: str):
    with session_factory() as session:
        session.add(
            KnowledgeMetric(
                id=metric_id,
                name=name,
                topic="质量分析",
                formula="SUM(x)",
                status="已发布",
            )
        )
        session.add(
            MetricSourceBinding(
                metric_id=metric_id,
                source_id=1,
                physical_formula="SUM(x)",
                status="已验证",
            )
        )
        session.commit()


def test_report_template_coverage_is_source_scoped(analysis_session):
    seed_report_templates(analysis_session)
    _enable_metric(analysis_session, "M-001", "产量")

    result = service.get_template_coverage(
        "tpl-production-weekly",
        1,
        session_factory=analysis_session,
    )
    assert result["coverage"]["ready"] is True
    assert result["coverage"]["score"] == 100


def test_create_and_finalize_topic_report_reuses_agent_artifacts(analysis_session, monkeypatch):
    seed_report_templates(analysis_session)
    monkeypatch.setattr(
        service.analysis_service,
        "create_session",
        lambda title, source_id, user_id: {"id": "ses-report", "title": title},
    )
    monkeypatch.setattr(
        service.analysis_service,
        "create_run",
        lambda session_id, question, idempotency_key: {
            "runId": "run-report",
            "sessionId": session_id,
            "status": "queued",
        },
    )
    report = service.create_report(
        ReportCreate(
            templateId="tpl-topic-report",
            sourceId=1,
            periodStart=datetime(2026, 7, 1),
            periodEnd=datetime(2026, 7, 8),
            prompt="分析工序良率变化",
        ),
        session_factory=analysis_session,
    )
    with analysis_session() as session:
        created = session.get(Report, report["id"])
        assert created.scope_json["emailEnabled"] is True
        version = session.get(ReportVersion, created.current_version_id)
        assert version.data_quality_json["coverageScore"] == created.scope_json["templateCoverage"]["score"]
    with analysis_session() as session:
        run = AnalysisRun(
            id="run-report",
            session_id="ses-report",
            trigger_message_id="msg-user",
            source_id=1,
            status="succeeded",
        )
        task = AnalysisTask(
            id="task-report",
            run_id=run.id,
            task_order=1,
            title="专题分析",
            normalized_question="分析工序良率变化",
            status="succeeded",
        )
        session.add_all([run, task])
        session.add(
            AnalysisMessage(
                id="msg-result",
                session_id="ses-report",
                run_id=run.id,
                role="assistant",
                content="本周期工序良率整体稳定。",
                sequence_no=2,
            )
        )
        session.add(
            AnalysisArtifact(
                id="art-conclusion",
                run_id=run.id,
                task_id=task.id,
                type="conclusion",
                content_json={
                    "text": "本周期工序良率整体稳定。",
                    "confidence": {"score": 91, "level": "high"},
                },
                checksum="a" * 64,
            )
        )
        session.commit()

    service.sync_report(report["id"], session_factory=analysis_session)
    completed = service.get_report(report["id"], session_factory=analysis_session)
    assert completed["status"] == "awaiting_review"
    assert completed["confidence"] == 91
    assert completed["sections"][0]["artifactIds"] == ["art-conclusion"]
    assert completed["content"]

    service.submit_review(report["id"], "reviewer", session_factory=analysis_session)
    service.approve_report(report["id"], "reviewer", session_factory=analysis_session)
    service.publish_report(report["id"], "reviewer", session_factory=analysis_session)
    with analysis_session() as session:
        assert session.query(ReportFile).count() == 0


def test_manual_weekly_publish_converts_legacy_content_before_queuing_exports(analysis_session):
    with analysis_session() as session:
        report = Report(
            id="manual-weekly",
            title="手工生产周报",
            template_id="tpl-weekly",
            source_id=1,
            report_type="weekly",
            status="approved",
            owner_id="owner-1",
            period_start=datetime(2026, 8, 3),
            period_end=datetime(2026, 8, 10),
            current_version_id="manual-version",
            scope_json={"timezone": "Asia/Shanghai"},
        )
        version = ReportVersion(
            id="manual-version",
            report_id=report.id,
            version=1,
            status="approved",
            summary="本周总产量为 12840件。",
            content_json={"summary": "本周总产量为 12840件。", "sections": []},
            data_quality_json={"coverageScore": 100},
        )
        section = ReportSection(
            id="manual-section",
            report_id=report.id,
            version_id=version.id,
            code="output",
            title="生产情况",
            section_order=1,
            status="succeeded",
            summary="本周总产量为 12840件。",
            confidence=92,
            artifact_ids_json=["artifact-output"],
            content_json={
                "result": {
                    "columns": ["week", "output_qty"],
                    "rows": [["W31", 12100], ["W32", 12840]],
                },
                "metricResolution": {"id": "output_qty", "name": "总产量", "unit": "件"},
            },
        )
        session.add_all([report, version, section])
        session.commit()

    service.publish_report("manual-weekly", "reviewer", session_factory=analysis_session)

    with analysis_session() as session:
        version = session.get(ReportVersion, "manual-version")
        assert version.content_json["schemaVersion"] == "weekly-report/v1"
        assert version.validation_json["passed"] is True
        assert {row.format for row in session.query(ReportFile).all()} == {"pdf", "xlsx"}
        frozen_validation = version.validation_json

    service.publish_report("manual-weekly", "reviewer", session_factory=analysis_session)
    with analysis_session() as session:
        assert session.get(ReportVersion, "manual-version").validation_json == frozen_validation
        assert session.query(ReportFile).count() == 2


def test_prestructured_weekly_is_revalidated_on_every_publish(analysis_session):
    with analysis_session() as session:
        report = Report(
            id="invalid-structured-weekly",
            title="待校验周报",
            template_id="tpl-weekly",
            source_id=1,
            report_type="weekly",
            status="approved",
            period_start=datetime(2026, 8, 3),
            period_end=datetime(2026, 8, 10),
            current_version_id="invalid-structured-version",
        )
        version = ReportVersion(
            id="invalid-structured-version",
            report_id=report.id,
            version=1,
            status="approved",
            content_json={"schemaVersion": "weekly-report/v1"},
        )
        session.add_all([report, version])
        session.commit()

    with pytest.raises(service.ReportConflict):
        service.publish_report(
            "invalid-structured-weekly",
            "reviewer",
            session_factory=analysis_session,
        )

    with analysis_session() as session:
        assert session.get(Report, "invalid-structured-weekly").status == "approved"
        assert session.query(ReportFile).count() == 0


def test_feishu_signature_and_success_response(monkeypatch):
    monkeypatch.setattr(feishu.settings, "feishu_webhook_url", "https://example.invalid/hook")
    monkeypatch.setattr(feishu.settings, "feishu_webhook_secret", "secret")
    captured = {}

    def handler(request):
        captured.update(json.loads(request.content.decode("utf-8")))
        return httpx.Response(200, json={"code": 0, "msg": "success", "request_id": "req-1"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    outcome = feishu.send_report(
        {
            "title": "质量周报",
            "status": "published",
            "periodStart": "2026-07-01T00:00:00",
            "periodEnd": "2026-07-08T00:00:00",
            "sourceName": "测试数据源",
            "summary": "质量稳定。",
        },
        client=client,
    )
    client.close()
    assert outcome.ok is True
    assert outcome.response_id == "req-1"
    assert captured["msg_type"] == "interactive"
    assert captured["timestamp"]
    assert captured["sign"]


def test_create_subscription_uses_requested_channel(analysis_session):
    seed_report_templates(analysis_session)

    subscription = service.create_subscription(
        SubscriptionCreate(name="经营周报", templateId="tpl-production-weekly", sourceId=1),
        session_factory=analysis_session,
    )

    assert subscription["channel"] == "email"
    assert subscription["publishPolicy"] == "auto_publish"
    assert subscription["nextRunAt"] is not None


def test_report_list_exposes_structured_overview_without_full_sections(analysis_session):
    with analysis_session() as session:
        report = Report(
            id="weekly-list-card",
            title="生产运营周报",
            template_id="tpl-weekly",
            source_id=1,
            report_type="weekly",
            status="published",
            owner_id="owner-1",
            period_start=datetime(2026, 8, 3),
            period_end=datetime(2026, 8, 10),
            current_version_id="weekly-list-version",
        )
        version = ReportVersion(
            id="weekly-list-version",
            report_id=report.id,
            version=2,
            status="published",
            content_json={
                "schemaVersion": "weekly-report/v1",
                "managementSummary": {"findings": [{"text": "产量保持稳定。"}]},
                "metrics": [{"name": "总产量", "value": 12840, "unit": "件"}],
                "anomalies": [{"title": "焊接良率下降", "severity": "high"}],
                "dataQuality": {"coverage": 97},
            },
        )
        report_file = ReportFile(
            id="weekly-list-pdf",
            report_id=report.id,
            version_id=version.id,
            format="pdf",
            status="ready",
        )
        message = Message(
            id="weekly-list-message",
            message_type="weekly_report",
            source="report-agent",
            title=report.title,
            summary="产量保持稳定。",
            related_type="report",
            related_id=report.id,
            report_version_id=version.id,
            idempotency_key="weekly-list-message",
        )
        recipient = MessageRecipient(
            id="weekly-list-recipient",
            message_id=message.id,
            user_id="owner-1",
            is_read=True,
        )
        session.add_all([report, version, report_file, message, recipient])
        session.commit()

    listed = service.list_reports(owner_id="owner-1", session_factory=analysis_session)

    assert listed[0]["reportOverview"]["managementSummary"]["findings"][0]["text"] == "产量保持稳定。"
    assert listed[0]["reportOverview"]["anomalies"][0]["severity"] == "high"
    assert listed[0]["files"][0]["downloadUrl"] == "/api/v1/report-files/weekly-list-pdf/download"
    assert listed[0]["isRead"] is True


def test_legacy_feishu_delivery_keeps_feishu_channel(analysis_session, monkeypatch):
    with analysis_session() as session:
        report = Report(
            id="report-published",
            title="经营周报",
            template_id="tpl-weekly",
            source_id=1,
            status="published",
            period_start=datetime(2026, 8, 3),
            period_end=datetime(2026, 8, 10),
            current_version_id="version-published",
        )
        session.add_all(
            [
                report,
                ReportVersion(
                    id="version-published",
                    report_id=report.id,
                    version=1,
                    status="published",
                ),
            ]
        )
        session.commit()
    monkeypatch.setattr(
        service.feishu,
        "send_report",
        lambda *_args, **_kwargs: feishu.FeishuResult(ok=True, response_id="req-1"),
    )

    delivery = service.deliver_to_feishu(
        "report-published", session_factory=analysis_session
    )

    assert delivery["channel"] == "feishu"
