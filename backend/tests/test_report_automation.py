"""R 智能周报：调度、编排、校验和自动发布。"""

from datetime import datetime, timezone

from app.db.models import (
    AnalysisArtifact,
    AnalysisEvent,
    AnalysisMessage,
    AnalysisRun,
    AnalysisTask,
    DomainEventOutbox,
    Report,
    ReportSection,
    ReportSubscription,
    ReportVersion,
)
from app.reports import automation, service
from app.reports.templates import seed_report_templates


UTC = timezone.utc


def _subscription(session_factory, *, next_run_at: datetime) -> str:
    with session_factory() as session:
        row = ReportSubscription(
            id="sub-weekly",
            name="生产周报",
            template_id="tpl-production-weekly",
            source_id=1,
            owner_id="owner-1",
            cadence="weekly",
            weekday=1,
            send_time="09:00",
            timezone="Asia/Shanghai",
            channel="email",
            publish_policy="auto_publish",
            next_run_at=next_run_at,
        )
        session.add(row)
        session.commit()
        return row.id


def test_weekly_schedule_uses_completed_natural_week_in_subscription_timezone():
    due_at = datetime(2026, 8, 10, 1, 0, tzinfo=UTC)  # 上海周一 09:00

    period = automation.weekly_period(due_at, "Asia/Shanghai")
    next_run = automation.next_weekly_run(
        due_at,
        weekday=1,
        send_time="09:00",
        timezone_name="Asia/Shanghai",
    )

    assert period.start == datetime(2026, 8, 3, 0, 0)
    assert period.end == datetime(2026, 8, 10, 0, 0)
    assert period.schedule_key == "weekly:2026-08-03"
    assert next_run == datetime(2026, 8, 17, 1, 0)


def test_due_scan_atomically_creates_one_persistent_agent_run(analysis_session):
    seed_report_templates(analysis_session)
    _subscription(analysis_session, next_run_at=datetime(2026, 8, 10, 1, 0))

    first = automation.scan_due_subscriptions(
        now=datetime(2026, 8, 10, 1, 1),
        session_factory=analysis_session,
    )
    second = automation.scan_due_subscriptions(
        now=datetime(2026, 8, 10, 1, 2),
        session_factory=analysis_session,
    )

    assert first.created == 1
    assert second.created == 0
    with analysis_session() as session:
        report = session.query(Report).one()
        run = session.query(AnalysisRun).one()
        version = session.query(ReportVersion).one()
        sections = session.query(ReportSection).order_by(ReportSection.section_order).all()
        subscription = session.query(ReportSubscription).one()

        assert report.analysis_run_id == run.id
        assert run.status == "queued"
        assert run.idempotency_key == f"report:{report.id}:v1"
        assert session.query(AnalysisEvent).one().event_type == "run.accepted"
        assert report.schedule_key == "weekly:2026-08-03"
        assert report.status == "queued"
        assert len(sections) == 2
        assert version.data_quality_json["kind"] == "data_anomaly"
        assert version.data_snapshot_json["period"]["cutoffAt"] == "2026-08-10T00:00:00"
        assert version.content_json["plan"][0]["code"] == "output"
        assert subscription.next_run_at == datetime(2026, 8, 17, 1, 0)


def _complete_scheduled_run(session_factory, *, with_evidence: bool = True) -> tuple[str, str]:
    with session_factory() as session:
        report = session.query(Report).one()
        run = session.query(AnalysisRun).filter_by(id=report.analysis_run_id).one()
        run.status = "succeeded"
        sections = session.query(ReportSection).order_by(ReportSection.section_order).all()
        for order, section in enumerate(sections, start=1):
            task = AnalysisTask(
                id=f"task-{order}",
                run_id=run.id,
                task_order=order,
                title=section.title,
                normalized_question=section.content_json["question"],
                status="succeeded",
            )
            session.add(task)
            if with_evidence:
                session.add(
                    AnalysisArtifact(
                        id=f"artifact-{order}",
                        run_id=run.id,
                        task_id=task.id,
                        type="conclusion",
                        content_json={
                            "text": f"{section.title}保持稳定。",
                            "confidence": {"score": 88, "level": "high"},
                        },
                        checksum=str(order) * 64,
                    )
                )
        session.add(
            AnalysisMessage(
                id="assistant-summary",
                session_id=run.session_id,
                run_id=run.id,
                role="assistant",
                content="本周生产整体稳定，应继续关注关键产线波动。",
                sequence_no=2,
            )
        )
        session.commit()
        return report.id, run.id


def test_scheduled_agent_report_validates_and_publishes_without_review(analysis_session):
    seed_report_templates(analysis_session)
    _subscription(analysis_session, next_run_at=datetime(2026, 8, 10, 1, 0))
    automation.scan_due_subscriptions(
        now=datetime(2026, 8, 10, 1, 1), session_factory=analysis_session
    )
    report_id, _ = _complete_scheduled_run(analysis_session)

    service.sync_report(report_id, session_factory=analysis_session)
    service.sync_report(report_id, session_factory=analysis_session)  # 重放不重复发布

    with analysis_session() as session:
        report = session.query(Report).one()
        version = session.query(ReportVersion).one()
        event = session.query(DomainEventOutbox).one()
        assert report.status == "published"
        assert report.publish_source == "agent"
        assert version.status == "published"
        assert version.validation_json["passed"] is True
        assert version.workflow_version == automation.WORKFLOW_VERSION
        assert version.approved_by is None
        assert event.event_type == "report.version.published"
        assert event.payload_json["subscriptionId"] == "sub-weekly"
        assert event.payload_json["period"]["start"] == "2026-08-03T00:00:00"
        assert event.payload_json["period"]["timezone"] == "Asia/Shanghai"
        assert event.payload_json["eventId"] == event.id
        assert session.query(DomainEventOutbox).count() == 1


def test_missing_section_evidence_blocks_automatic_publish(analysis_session):
    seed_report_templates(analysis_session)
    _subscription(analysis_session, next_run_at=datetime(2026, 8, 10, 1, 0))
    automation.scan_due_subscriptions(
        now=datetime(2026, 8, 10, 1, 1), session_factory=analysis_session
    )
    report_id, _ = _complete_scheduled_run(analysis_session, with_evidence=False)

    service.sync_report(report_id, session_factory=analysis_session)

    with analysis_session() as session:
        report = session.query(Report).one()
        version = session.query(ReportVersion).one()
        assert report.status == "validation_failed"
        assert version.status == "validation_failed"
        assert version.validation_json["passed"] is False
        assert version.validation_json["hardErrors"]
        assert session.query(DomainEventOutbox).count() == 0


def test_failed_subscription_does_not_block_other_due_subscriptions(analysis_session):
    seed_report_templates(analysis_session)
    with analysis_session() as session:
        session.add(
            ReportSubscription(
                id="sub-broken",
                name="失效订阅",
                template_id="missing-template",
                source_id=1,
                cadence="weekly",
                weekday=1,
                send_time="09:00",
                timezone="Asia/Shanghai",
                next_run_at=datetime(2026, 8, 10, 1, 0),
            )
        )
        session.commit()
    _subscription(analysis_session, next_run_at=datetime(2026, 8, 10, 1, 0))

    result = automation.scan_due_subscriptions(
        now=datetime(2026, 8, 10, 1, 1), session_factory=analysis_session
    )

    assert result.scanned == 2
    assert result.created == 1
    assert result.failures[0]["subscriptionId"] == "sub-broken"
    with analysis_session() as session:
        broken = session.get(ReportSubscription, "sub-broken")
        assert broken.next_run_at == datetime(2026, 8, 10, 1, 0)
