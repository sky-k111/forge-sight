"""Monitoring API contracts and authorization boundaries."""

from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import monitoring as monitoring_api
from app.auth.deps import require_user
from app.db.models import (
    Base,
    DataScopePolicy,
    DataSource,
    DataTable,
    MonitoringRule,
    PlatformUser,
    TableField,
)
from app.main import app
from app.monitoring import service


@pytest.fixture()
def monitoring_api_session(monkeypatch):
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(monitoring_api, "PlatformSession", factory)
    monkeypatch.setattr(monitoring_api, "write_audit", lambda **_kwargs: None)
    with factory() as session:
        source = DataSource(
            name="API 测试数据源",
            type="mysql",
            host="127.0.0.1",
            port=3306,
            db_name="monitoring_api",
            username="reader",
            status="连接正常",
            is_default=True,
        )
        session.add(source)
        session.flush()
        session.add(
            DataTable(
                source_id=source.id,
                name="eqp_downtime_record",
                cn_name="停机记录",
                topic="设备",
                registered=True,
            )
        )
        for index, field in enumerate(
            ["start_time", "equipment_id", "downtime_minutes", "is_planned"], 1
        ):
            session.add(
                TableField(
                    source_id=source.id,
                    table_name="eqp_downtime_record",
                    name=field,
                    cn_name=field,
                    data_type="datetime" if field == "start_time" else "decimal",
                    ordinal=index,
                )
            )
        session.add_all(
            [
                PlatformUser(
                    id="admin-id",
                    username="admin",
                    password_hash="test",
                    display_name="管理员",
                    role="admin",
                ),
                PlatformUser(
                    id="user-id",
                    username="quality",
                    password_hash="test",
                    display_name="质量主管",
                    role="business",
                ),
                DataScopePolicy(
                    id="scope-monitoring",
                    subject_type="user",
                    subject_id="user-id",
                    business_key="anomaly_monitoring",
                    effect="allow",
                    data_source_ids_json=[source.id],
                    dimensions_json={},
                    enabled=True,
                ),
            ]
        )
        session.commit()
    yield factory
    app.dependency_overrides.pop(require_user, None)


def _admin():
    return {
        "id": "admin-id",
        "username": "admin",
        "displayName": "管理员",
        "role": "admin",
    }


def _business():
    return {
        "id": "user-id",
        "username": "quality",
        "displayName": "质量主管",
        "role": "business",
    }


def _payload():
    return {
        "name": "非计划停机过长",
        "code": "unplanned-downtime",
        "sourceId": 1,
        "templateCode": "unplanned-downtime",
        "metricName": "非计划停机时长",
        "entityType": "equipment",
        "grain": "day",
        "queryPlan": {
            "kind": "sum",
            "table": "eqp_downtime_record",
            "timeField": "start_time",
            "entityField": "equipment_id",
            "valueField": "downtime_minutes",
            "filters": [{"field": "is_planned", "operator": "eq", "value": False}],
        },
        "condition": {
            "operator": "gt",
            "openThreshold": 120,
            "closeThreshold": 30,
            "pendingPeriods": 1,
            "recoveryPeriods": 1,
        },
        "notificationPolicy": {"userIds": ["user-id"]},
    }


def test_rule_governance_is_admin_only(monitoring_api_session):
    client = TestClient(app)
    app.dependency_overrides[require_user] = _business
    assert client.post("/api/v1/monitoring/rules", json=_payload()).status_code == 403

    app.dependency_overrides[require_user] = _admin
    created = client.post("/api/v1/monitoring/rules", json=_payload())
    assert created.status_code == 201
    rule_id = created.json()["id"]
    assert client.get(f"/api/v1/monitoring/rules/{rule_id}").json()["code"] == "unplanned-downtime"
    assert client.get("/api/v1/monitoring/rule-templates").status_code == 200
    assert client.post(f"/api/v1/monitoring/rules/{rule_id}/validate").json()["valid"] is True
    published = client.post(f"/api/v1/monitoring/rules/{rule_id}/publish")
    assert published.json()["status"] == "published"
    paused = client.post(f"/api/v1/monitoring/rules/{rule_id}/pause")
    assert paused.json()["status"] == "paused"
    resumed = client.post(f"/api/v1/monitoring/rules/{rule_id}/resume")
    assert resumed.json()["status"] == "published"
    queued = client.post(f"/api/v1/monitoring/rules/{rule_id}/run")
    assert queued.status_code == 202
    assert queued.json()["status"] == "queued"
    assert client.get("/api/v1/monitoring/runs").json()["items"][0]["ruleId"] == rule_id
    assert client.get("/api/v1/monitoring/operations/metrics").json()["tasks"]["queued"] == 1
    assert client.get("/api/v1/monitoring/dead-letters").json()["items"] == []


def test_business_alert_api_is_read_only_and_scope_filtered(monitoring_api_session):
    with monitoring_api_session() as session:
        rule = service.create_rule(
            session,
            name="非计划停机过长",
            code="unplanned-downtime",
            source_id=1,
            template_code="unplanned-downtime",
            metric_name="非计划停机时长",
            entity_type="equipment",
            grain="day",
            query_plan=_payload()["queryPlan"],
            condition=_payload()["condition"],
            notification_policy={"userIds": ["user-id"]},
            created_by="admin-id",
        )
        service.publish_rule(session, rule.id, published_by="admin-id")
        service.record_observation(
            session,
            rule=rule,
            logical_period="2026-08-26",
            entity_id="E0009",
            entity_name="冲压机 9",
            current_value=180,
            sample_size=2,
            data_cutoff_at=datetime(2026, 8, 26, 23),
            now=datetime(2026, 8, 27, 1),
        )
        session.commit()

    app.dependency_overrides[require_user] = _business
    client = TestClient(app)
    alerts = client.get("/api/v1/monitoring/alerts")
    assert alerts.status_code == 200
    assert alerts.json()["items"][0]["entityId"] == "E0009"
    alert_id = alerts.json()["items"][0]["id"]
    assert client.get(f"/api/v1/monitoring/alerts/{alert_id}/evidence").status_code == 200
    assert client.get(f"/api/v1/monitoring/alerts/{alert_id}/timeline").status_code == 200
    assert client.post(f"/api/v1/monitoring/alerts/{alert_id}/close").status_code == 404


def test_unscoped_business_user_sees_no_alerts(monitoring_api_session):
    app.dependency_overrides[require_user] = lambda: {
        "id": "unknown-user",
        "username": "unknown",
        "displayName": "无权限用户",
        "role": "business",
    }
    response = TestClient(app).get("/api/v1/monitoring/alerts")
    assert response.status_code == 200
    assert response.json()["items"] == []
