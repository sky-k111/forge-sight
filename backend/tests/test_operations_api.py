"""T08: administrator-only operational metrics and actionable alerts."""

from datetime import datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import operations
from app.auth.deps import require_user
from app.db.models import (
    AnalysisRun,
    Base,
    DomainEventOutbox,
    EmailVerificationEvent,
    ReportDelivery,
    ReportFile,
    ReportSubscription,
)
from app.main import app


def _factory():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def test_operational_snapshot_reports_queue_alerts_without_sensitive_data(monkeypatch):
    factory = _factory()
    now = datetime(2026, 8, 13, 12, 0)
    with factory() as session:
        session.add_all(
            [
                ReportSubscription(
                    id="sub-due",
                    name="到期订阅",
                    template_id="tpl",
                    source_id=1,
                    owner_id="user-1",
                    next_run_at=now - timedelta(minutes=30),
                ),
                AnalysisRun(
                    id="run-failed",
                    session_id="session-1",
                    trigger_message_id="message-1",
                    source_id=1,
                    status="failed",
                    error_code="MODEL_TIMEOUT",
                    created_at=now - timedelta(minutes=5),
                    finished_at=now,
                ),
                ReportFile(
                    id="file-dead",
                    report_id="report-1",
                    version_id="version-1",
                    format="pdf",
                    status="dead_letter",
                    generation_attempt=3,
                    error_json={"message": "must not leak"},
                ),
                DomainEventOutbox(
                    id="event-dead",
                    event_type="message.published",
                    aggregate_id="message-1",
                    idempotency_key="event-key",
                    status="dead_letter",
                    error_summary="must not leak",
                ),
                ReportDelivery(
                    id="delivery-failed",
                    message_id="message-1",
                    channel="email",
                    status="failed",
                    recipient_user_id="user-1",
                    recipient_address_snapshot="secret@example.com",
                    error_category="invalid_address",
                    last_duration_ms=100,
                    created_at=now,
                    updated_at=now,
                ),
                ReportDelivery(
                    id="delivery-sent",
                    message_id="message-2",
                    channel="email",
                    status="sent",
                    recipient_user_id="user-2",
                    last_duration_ms=900,
                    created_at=now,
                    updated_at=now,
                ),
                EmailVerificationEvent(
                    id="verify-failed",
                    user_id="user-1",
                    email_canonical="secret@example.com",
                    purpose="verify",
                    event_type="verify_failed",
                    outcome="invalid",
                    ip="127.0.0.1",
                    created_at=now,
                ),
            ]
        )
        session.commit()

    monkeypatch.setattr(operations, "PlatformSession", factory)
    app.dependency_overrides[require_user] = lambda: {
        "id": "admin-id", "username": "admin", "role": "admin"
    }
    client = TestClient(app)
    response = client.get("/api/v1/operations/metrics")

    assert response.status_code == 200
    payload = response.json()
    assert payload["queues"]["outbox"]["dead_letter"] == 1
    assert payload["queues"]["files"]["dead_letter"] == 1
    assert payload["queues"]["email"]["failed"] == 1
    assert payload["scheduler"]["dueSubscriptions"] == 1
    assert payload["delivery"] == {"durationP95Ms": 900, "sampleCount": 2}
    assert {alert["code"] for alert in payload["alerts"]} >= {
        "OUTBOX_DEAD_LETTER",
        "FILE_DEAD_LETTER",
        "EMAIL_FINAL_FAILURE",
        "SCHEDULER_LAG",
    }
    serialized = response.text
    assert "secret@example.com" not in serialized
    assert "must not leak" not in serialized
    app.dependency_overrides.pop(require_user, None)


def test_operational_snapshot_rejects_non_admin(monkeypatch):
    app.dependency_overrides[require_user] = lambda: {
        "id": "user-id", "username": "user", "role": "business"
    }

    response = TestClient(app).get("/api/v1/operations/metrics")

    assert response.status_code == 403
    app.dependency_overrides.pop(require_user, None)
