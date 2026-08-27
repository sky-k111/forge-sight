"""Monitoring domain tests: deterministic evaluation, alert lifecycle and recovery."""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import (
    AlertDelivery,
    Base,
    DataScopePolicy,
    DataSource,
    DataTable,
    FeishuBinding,
    Message,
    MessageRecipient,
    MonitoringAlert,
    MonitoringCursor,
    MonitoringObservation,
    MonitoringRule,
    PlatformUser,
    TableField,
)
from app.monitoring import service
from app.monitoring import worker
from app.monitoring import delivery
from app.monitoring.provider import CaptureAlertProvider


@pytest.fixture()
def monitoring_session():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with factory() as session:
        source = DataSource(
            name="监控测试数据源",
            type="mysql",
            host="127.0.0.1",
            port=3306,
            db_name="monitoring_test",
            username="reader",
            status="连接正常",
            is_default=True,
        )
        session.add(source)
        session.flush()
        session.add(
            DataTable(
                source_id=source.id,
                name="mes_process_output",
                cn_name="工序产量",
                topic="生产",
                registered=True,
            )
        )
        for ordinal, field in enumerate(
            ["stat_date", "process_id", "good_qty", "defect_qty"], start=1
        ):
            session.add(
                TableField(
                    source_id=source.id,
                    table_name="mes_process_output",
                    name=field,
                    cn_name=field,
                    data_type="date" if field == "stat_date" else "decimal",
                    ordinal=ordinal,
                )
            )
        rule = service.create_rule(
            session,
            name="工序良率低于红线",
            code="yield-low",
            source_id=source.id,
            template_code="process-yield-drop",
            metric_name="工序良率",
            entity_type="process",
            grain="day",
            query_plan={
                "kind": "ratio",
                "table": "mes_process_output",
                "timeField": "stat_date",
                "entityField": "process_id",
                "numeratorField": "good_qty",
                "denominatorFields": ["good_qty", "defect_qty"],
            },
            condition={
                "operator": "lt",
                "openThreshold": 0.9,
                "closeThreshold": 0.93,
                "pendingPeriods": 2,
                "recoveryPeriods": 2,
            },
            notification_policy={"userIds": ["usr_quality"]},
            created_by="admin",
        )
        service.publish_rule(session, rule.id, published_by="admin")
        session.commit()
    return factory


def _evaluate(factory, *, period: str, value: float, now: datetime):
    with factory() as session:
        rule = session.query(service.MonitoringRule).one()
        result = service.record_observation(
            session,
            rule=rule,
            logical_period=period,
            entity_id="PR06",
            entity_name="总装检测",
            current_value=value,
            baseline_value=0.95,
            sample_size=100,
            data_cutoff_at=now,
            now=now,
        )
        session.commit()
        return result


def test_alert_requires_pending_periods_and_recovers_automatically(monitoring_session):
    start = datetime(2026, 8, 24, 8)
    first = _evaluate(monitoring_session, period="2026-08-24", value=0.86, now=start)
    assert first.observation.evaluation_state == "pending"
    assert first.alert is None

    second = _evaluate(
        monitoring_session,
        period="2026-08-25",
        value=0.84,
        now=start + timedelta(days=1),
    )
    assert second.observation.evaluation_state == "firing"
    assert second.alert.status == "firing"

    recovering = _evaluate(
        monitoring_session,
        period="2026-08-26",
        value=0.94,
        now=start + timedelta(days=2),
    )
    assert recovering.observation.evaluation_state == "normal"
    assert recovering.alert.status == "recovering"

    resolved = _evaluate(
        monitoring_session,
        period="2026-08-27",
        value=0.95,
        now=start + timedelta(days=3),
    )
    assert resolved.alert.status == "resolved"
    assert resolved.alert.resolved_at is not None

    with monitoring_session() as session:
        assert session.query(MonitoringAlert).count() == 1
        assert session.query(MonitoringObservation).count() == 4
        event_types = {row.event_type for row in service.list_alert_events(session)}
        assert "monitoring.alert.firing" in event_types
        assert "monitoring.alert.resolved" in event_types


def test_same_logical_period_is_idempotent(monitoring_session):
    now = datetime(2026, 8, 24, 8)
    first = _evaluate(monitoring_session, period="2026-08-24", value=0.86, now=now)
    second = _evaluate(monitoring_session, period="2026-08-24", value=0.86, now=now)
    assert second.observation.id == first.observation.id
    with monitoring_session() as session:
        assert session.query(MonitoringObservation).count() == 1


def test_no_data_does_not_create_business_alert(monitoring_session):
    with monitoring_session() as session:
        rule = session.query(service.MonitoringRule).one()
        result = service.record_observation(
            session,
            rule=rule,
            logical_period="2026-08-24",
            entity_id="PR06",
            entity_name="总装检测",
            current_value=None,
            baseline_value=0.95,
            sample_size=0,
            data_cutoff_at=None,
            quality_status="no_data",
            now=datetime(2026, 8, 24, 8),
        )
        session.commit()
        assert result.observation.evaluation_state == "no_data"
        assert result.alert is None
        assert session.query(MonitoringAlert).count() == 0


def test_median_mad_baseline_detects_outlier_without_hardcoded_threshold(
    monitoring_session,
):
    start = datetime(2026, 8, 20, 8)
    with monitoring_session() as session:
        rule = session.query(service.MonitoringRule).one()
        rule.condition_json = {
            "baselineMethod": "median_mad",
            "direction": "lower",
            "sigma": 3,
            "minimumBaselinePeriods": 4,
            "pendingPeriods": 1,
            "recoveryPeriods": 1,
        }
        for offset, value in enumerate([100, 101, 99, 100]):
            service.record_observation(
                session,
                rule=rule,
                logical_period=f"2026-08-{20 + offset}",
                entity_id="PR06",
                current_value=value,
                sample_size=10,
                now=start + timedelta(days=offset),
            )
        result = service.record_observation(
            session,
            rule=rule,
            logical_period="2026-08-24",
            entity_id="PR06",
            entity_name="总装检测",
            current_value=80,
            sample_size=10,
            now=start + timedelta(days=4),
        )
        assert result.observation.baseline_value == 100
        assert result.observation.lower_bound is not None
        assert result.alert is not None
        assert result.alert.status == "firing"


def test_worker_claims_compiles_and_completes_durable_task(
    monitoring_session, monkeypatch
):
    from app.gateway.executor import ExecutionResult

    now = datetime(2026, 8, 25, 8)
    with monitoring_session() as session:
        rule = session.query(service.MonitoringRule).one()
        task = worker.enqueue_rule_task(
            session,
            rule,
            logical_period="2026-08-25",
            window_start=datetime(2026, 8, 25),
            window_end=datetime(2026, 8, 26),
            now=now,
        )
        task_id = task.id
        session.commit()

    monkeypatch.setattr(
        worker,
        "execute_readonly",
        lambda *_args, **_kwargs: ExecutionResult(
            ok=True,
            columns=["entity_id", "current_value", "sample_size", "data_cutoff_at"],
            rows=[["PR06", 0.84, 120, datetime(2026, 8, 25, 23)]],
            row_count=1,
        ),
    )

    assert worker.run_worker_once(
        monitoring_session, worker_id="worker-1", now=now
    )
    with monitoring_session() as session:
        task = session.get(worker.MonitoringTask, task_id)
        assert task.status == "succeeded"
        assert task.lease_owner is None
        assert session.query(MonitoringObservation).count() == 1
        cursor = session.query(MonitoringCursor).one()
        assert cursor.task_id == task_id
        assert cursor.logical_period == "2026-08-25"


def test_compiler_rejects_unregistered_fields(monitoring_session):
    with monitoring_session() as session:
        rule = session.query(service.MonitoringRule).one()
        plan = dict(rule.query_plan_json)
        plan["numeratorField"] = "secret_column"
        rule.query_plan_json = plan
        with pytest.raises(PermissionError, match="未登记字段"):
            worker.compile_query(
                session,
                rule,
                window_start=datetime(2026, 8, 25),
                window_end=datetime(2026, 8, 26),
            )


def test_scheduler_uses_last_complete_day_and_is_idempotent(monitoring_session):
    now = datetime(2026, 8, 27, 10, 35)
    with monitoring_session() as session:
        assert worker.enqueue_due_rules(session, now=now) == 1
        task = session.query(worker.MonitoringTask).one()
        assert task.logical_period == "2026-08-26"
        assert task.window_start == datetime(2026, 8, 26)
        assert task.window_end == datetime(2026, 8, 27)
        session.commit()
    with monitoring_session() as session:
        # Even when scheduling is reconsidered, the same rule/version/period
        # cannot create a duplicate task.
        rule = session.query(service.MonitoringRule).one()
        rule.last_scheduled_at = None
        assert worker.enqueue_due_rules(session, now=now) == 0


def test_publishing_new_version_retires_previous_active_version(monitoring_session):
    with monitoring_session() as session:
        old = session.query(MonitoringRule).one()
        replacement = service.create_rule(
            session,
            name=old.name,
            code=old.code,
            source_id=old.source_id,
            template_code=old.template_code,
            metric_name=old.metric_name,
            entity_type=old.entity_type,
            grain=old.grain,
            query_plan=old.query_plan_json,
            condition=old.condition_json,
            notification_policy=old.notification_policy_json,
            created_by="admin",
        )
        service.publish_rule(session, replacement.id, published_by="admin")
        session.commit()
        assert replacement.status == "published"
        assert session.get(MonitoringRule, old.id).status == "retired"


def test_paused_rule_can_resume_and_reenters_scheduler(monitoring_session):
    with monitoring_session() as session:
        rule = session.query(MonitoringRule).one()
        service.pause_rule(session, rule.id)
        rule.last_scheduled_at = datetime(2026, 8, 27, 8)
        resumed = service.resume_rule(
            session,
            rule.id,
            resumed_by="admin",
            now=datetime(2026, 8, 27, 9),
        )
        assert resumed.status == "published"
        assert resumed.last_scheduled_at is None
        assert worker.enqueue_due_rules(session, now=datetime(2026, 8, 27, 9)) == 1


def test_worker_records_no_data_when_grouped_query_returns_no_rows(
    monitoring_session, monkeypatch
):
    from app.gateway.executor import ExecutionResult

    now = datetime(2026, 8, 27, 8)
    monkeypatch.setattr(
        worker,
        "execute_readonly",
        lambda *_args, **_kwargs: ExecutionResult(
            ok=True,
            columns=["entity_id", "current_value", "sample_size", "data_cutoff_at"],
            rows=[],
            row_count=0,
        ),
    )
    with monitoring_session() as session:
        rule = session.query(MonitoringRule).one()
        task = worker.enqueue_rule_task(
            session,
            rule,
            logical_period="2026-08-26",
            window_start=datetime(2026, 8, 26),
            window_end=datetime(2026, 8, 27),
            now=now,
        )
        task_id = task.id
        session.commit()
    assert worker.run_worker_once(monitoring_session, worker_id="worker-empty", now=now)
    with monitoring_session() as session:
        observation = session.query(MonitoringObservation).one()
        assert observation.entity_id == "source:1"
        assert observation.quality_status == "no_data"
        assert observation.evaluation_state == "no_data"
        assert session.get(worker.MonitoringTask, task_id).result_json == {
            "processedObservations": 1,
            "rowCount": 0,
        }


def test_scheduler_backfills_every_missing_complete_period(monitoring_session):
    with monitoring_session() as session:
        rule = session.query(MonitoringRule).one()
        session.add(
            MonitoringCursor(
                id="cursor-backfill",
                rule_id=rule.id,
                source_id=rule.source_id,
                logical_period="2026-08-23",
                watermark_at=datetime(2026, 8, 24),
            )
        )
        session.flush()
        assert worker.enqueue_due_rules(session, now=datetime(2026, 8, 27, 10)) == 3
        periods = [
            row[0]
            for row in session.query(worker.MonitoringTask.logical_period)
            .order_by(worker.MonitoringTask.logical_period)
            .all()
        ]
        assert periods == ["2026-08-24", "2026-08-25", "2026-08-26"]


def test_cursor_never_regresses_when_backfill_finishes_out_of_order(
    monitoring_session, monkeypatch
):
    from app.gateway.executor import ExecutionResult

    monkeypatch.setattr(
        worker,
        "execute_readonly",
        lambda *_args, **_kwargs: ExecutionResult(
            ok=True,
            columns=["entity_id", "current_value", "sample_size", "data_cutoff_at"],
            rows=[["PR06", 0.95, 10, datetime(2026, 8, 26, 23)]],
            row_count=1,
        ),
    )
    with monitoring_session() as session:
        rule = session.query(MonitoringRule).one()
        cursor = MonitoringCursor(
            id="cursor-monotonic",
            rule_id=rule.id,
            source_id=rule.source_id,
            logical_period="2026-08-26",
            watermark_at=datetime(2026, 8, 27),
        )
        session.add(cursor)
        task = worker.enqueue_rule_task(
            session,
            rule,
            logical_period="2026-08-25",
            window_start=datetime(2026, 8, 25),
            window_end=datetime(2026, 8, 26),
            now=datetime(2026, 8, 27, 8),
        )
        task.status = "running"
        task.lease_owner = "late-worker"
        session.flush()
        worker.execute_task_sync(
            session,
            task,
            worker_id="late-worker",
            now=datetime(2026, 8, 27, 8),
        )
        assert cursor.watermark_at == datetime(2026, 8, 27)
        assert cursor.logical_period == "2026-08-26"


def test_worker_records_no_data_for_previously_seen_missing_entity(
    monitoring_session, monkeypatch
):
    from app.gateway.executor import ExecutionResult

    first_day = datetime(2026, 8, 25, 8)
    with monitoring_session() as session:
        rule = session.query(MonitoringRule).one()
        service.record_observation(
            session,
            rule=rule,
            logical_period="2026-08-24",
            entity_id="PR06",
            entity_name="总装检测",
            current_value=0.95,
            sample_size=100,
            now=first_day,
        )
        task = worker.enqueue_rule_task(
            session,
            rule,
            logical_period="2026-08-25",
            window_start=datetime(2026, 8, 25),
            window_end=datetime(2026, 8, 26),
            now=first_day + timedelta(days=1),
        )
        session.commit()
    monkeypatch.setattr(
        worker,
        "execute_readonly",
        lambda *_args, **_kwargs: ExecutionResult(
            ok=True,
            columns=["entity_id", "current_value", "sample_size", "data_cutoff_at"],
            rows=[["PR07", 0.96, 80, datetime(2026, 8, 25, 23)]],
            row_count=1,
        ),
    )
    assert worker.run_worker_once(
        monitoring_session,
        worker_id="worker-missing-entity",
        now=first_day + timedelta(days=1),
    )
    with monitoring_session() as session:
        missing = session.query(MonitoringObservation).filter_by(
            logical_period="2026-08-25",
            entity_id="PR06",
        ).one()
        assert missing.quality_status == "no_data"
        assert missing.details_json["missingEntity"] is True
        assert session.get(worker.MonitoringTask, task.id).result_json[
            "processedObservations"
        ] == 2



def test_alert_event_expands_to_inbox_and_reliable_feishu_delivery(monitoring_session):
    now = datetime(2026, 8, 25, 8)
    with monitoring_session() as session:
        session.add(
            PlatformUser(
                id="usr_quality",
                username="quality",
                password_hash="test",
                display_name="质量主管",
                role="business",
            )
        )
        session.add(
            DataScopePolicy(
                id="scope-quality-monitoring",
                subject_type="user",
                subject_id="usr_quality",
                business_key="anomaly_monitoring",
                effect="allow",
                data_source_ids_json=[1],
                dimensions_json={},
                enabled=True,
            )
        )
        session.add(
            FeishuBinding(
                id="fsb-quality",
                user_id="usr_quality",
                tenant_key="tenant",
                open_id="ou_quality",
                bind_status="bound",
            )
        )
        rule = session.query(service.MonitoringRule).one()
        rule.condition_json = {**rule.condition_json, "pendingPeriods": 1}
        service.record_observation(
            session,
            rule=rule,
            logical_period="2026-08-25",
            entity_id="PR06",
            entity_name="总装检测",
            current_value=0.84,
            sample_size=100,
            data_cutoff_at=now,
            now=now,
        )
        session.commit()

    assert delivery.run_event_worker_once(
        monitoring_session, worker_id="event-worker", now=now
    )
    with monitoring_session() as session:
        assert session.query(Message).filter_by(message_type="monitoring_alert").count() == 1
        assert session.query(MessageRecipient).filter_by(user_id="usr_quality").count() == 1
        row = session.query(AlertDelivery).one()
        assert row.channel == "feishu"
        assert row.status == "queued"

    provider = CaptureAlertProvider("feishu")
    assert delivery.run_delivery_worker_once(
        monitoring_session,
        worker_id="delivery-worker",
        providers={"feishu": provider},
        public_app_url="http://127.0.0.1:5173",
        now=now,
    )
    assert provider.messages[0].detail_url.endswith("/workspace/alerts/") is False
    assert "/workspace/alerts/" in provider.messages[0].detail_url
    with monitoring_session() as session:
        assert session.query(AlertDelivery).one().status == "sent"
