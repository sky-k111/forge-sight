"""Audited report-detail PDF delivery endpoint."""

from datetime import datetime

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import reports as reports_api
from app.auth.deps import require_user
from app.db.models import (
    Base,
    DataScopePolicy,
    DingTalkBinding,
    FeishuBinding,
    PlatformUser,
    Report,
    ReportDelivery,
    ReportVersion,
)
from app.main import app


def test_report_pdf_delivery_endpoint_is_audited_and_idempotent(monkeypatch):
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with factory() as session:
        session.add_all(
            [
                PlatformUser(
                    id="user-api",
                    username="user-api",
                    password_hash="test-only",
                    display_name="API User",
                    enabled=True,
                ),
                FeishuBinding(
                    id="feishu-api",
                    user_id="user-api",
                    tenant_key="tenant-api",
                    open_id="ou-api",
                    bind_status="bound",
                ),
                DingTalkBinding(
                    id="dingtalk-api",
                    user_id="user-api",
                    corp_id="corp-api",
                    union_id="union-api",
                    bind_status="bound",
                ),
                DataScopePolicy(
                    id="scope-api",
                    subject_type="user",
                    subject_id="user-api",
                    business_key="weekly_report",
                    data_source_ids_json=[1],
                    dimensions_json={},
                ),
                Report(
                    id="report-api",
                    title="接口验收周报",
                    template_id="tpl-weekly",
                    source_id=1,
                    report_type="weekly",
                    owner_id="user-api",
                    status="published",
                    period_start=datetime(2026, 8, 17),
                    period_end=datetime(2026, 8, 23),
                    current_version_id="version-api",
                    scope_json={"audience": {"userIds": ["user-api"]}},
                ),
                ReportVersion(
                    id="version-api",
                    report_id="report-api",
                    version=1,
                    status="published",
                    summary="接口验收。",
                    published_at=datetime(2026, 8, 23, 10, 0),
                ),
            ]
        )
        session.commit()

    audits = []
    monkeypatch.setattr(reports_api, "PlatformSession", factory)
    monkeypatch.setattr(reports_api, "_authorize_report", lambda *_args: None)
    monkeypatch.setattr(reports_api, "write_audit", lambda **values: audits.append(values))
    app.dependency_overrides[require_user] = lambda: {
        "id": "user-api",
        "username": "user-api",
        "displayName": "API User",
        "role": "business",
    }
    client = TestClient(app)

    first = client.post("/api/v1/reports/report-api/deliveries/feishu")
    repeated = client.post("/api/v1/reports/report-api/deliveries/feishu")
    dingtalk = client.post("/api/v1/reports/report-api/deliveries/dingtalk")
    invalid = client.post("/api/v1/reports/report-api/deliveries/webhook")

    assert first.status_code == repeated.status_code == dingtalk.status_code == 202
    assert first.json()["id"] == repeated.json()["id"]
    assert {first.json()["channel"], dingtalk.json()["channel"]} == {
        "feishu",
        "dingtalk",
    }
    assert invalid.status_code == 400
    assert audits[-1]["action"] == "投递周报 PDF"
    with factory() as session:
        assert session.query(ReportDelivery).count() == 2
    app.dependency_overrides.pop(require_user, None)
