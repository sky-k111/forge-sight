"""E08: administrator delivery visibility and audited retry API."""

from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import deliveries as delivery_api
from app.auth.deps import require_user
from app.db.models import Base, ReportDelivery
from app.main import app


@pytest.fixture()
def delivery_api_session(monkeypatch):
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(delivery_api, "PlatformSession", factory)
    audits = []
    monkeypatch.setattr(delivery_api, "write_audit", lambda **values: audits.append(values))
    with factory() as session:
        session.add(
            ReportDelivery(
                id="delivery-1",
                message_id="message-1",
                channel="email",
                status="failed",
                attempt=1,
                attempt_count=2,
                recipient_user_id="user-1",
                recipient_address_snapshot="person@example.com",
                idempotency_key="email:message-1:user-1",
                error_category="network",
                created_at=datetime(2026, 8, 13, 9, 0),
            )
        )
        session.commit()
    yield factory, audits
    app.dependency_overrides.pop(require_user, None)


def _user(role: str):
    return {
        "id": "admin-id" if role == "admin" else "user-id",
        "username": role,
        "displayName": role,
        "role": role,
    }


def test_admin_can_filter_masked_deliveries_and_retry_in_place(delivery_api_session):
    factory, audits = delivery_api_session
    app.dependency_overrides[require_user] = lambda: _user("admin")
    client = TestClient(app)

    listed = client.get("/api/v1/deliveries", params={"status": "failed"})
    retried = client.post("/api/v1/deliveries/delivery-1/retry")

    assert listed.status_code == 200
    assert listed.json()["items"][0]["recipientAddress"] == "p***@example.com"
    assert "person@example.com" not in listed.text
    assert retried.status_code == 200
    assert retried.json()["id"] == "delivery-1"
    assert retried.json()["status"] == "queued"
    with factory() as session:
        assert session.query(ReportDelivery).count() == 1
        assert session.get(ReportDelivery, "delivery-1").attempt_count == 2
    assert audits[-1]["action"] == "人工重发邮件"


def test_business_user_cannot_view_or_retry_deliveries(delivery_api_session):
    app.dependency_overrides[require_user] = lambda: _user("business")
    client = TestClient(app)

    assert client.get("/api/v1/deliveries").status_code == 403
    assert client.post("/api/v1/deliveries/delivery-1/retry").status_code == 403


def test_retry_rejects_sent_delivery(delivery_api_session):
    factory, _audits = delivery_api_session
    with factory() as session:
        delivery = session.get(ReportDelivery, "delivery-1")
        delivery.status = "sent"
        session.commit()
    app.dependency_overrides[require_user] = lambda: _user("admin")

    response = TestClient(app).post("/api/v1/deliveries/delivery-1/retry")

    assert response.status_code == 409
