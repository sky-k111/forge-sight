"""阶段六报告编排、版本与飞书适配器。"""

import json
from datetime import datetime

import httpx

from app.db.models import (
    AnalysisArtifact,
    AnalysisMessage,
    AnalysisRun,
    AnalysisTask,
    KnowledgeMetric,
    MetricSourceBinding,
    Report,
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
