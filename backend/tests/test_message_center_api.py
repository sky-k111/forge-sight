"""N01-N07: authenticated inbox and admin-only announcement APIs."""

from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import messages as messages_api
from app.auth.deps import require_user
from app.db.models import Base, Message, MessageRecipient, PlatformUser
from app.main import app


@pytest.fixture()
def api_session(monkeypatch):
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(messages_api, "PlatformSession", factory)
    monkeypatch.setattr(messages_api, "write_audit", lambda **_kwargs: None)
    with factory() as session:
        session.add_all(
            [
                PlatformUser(
                    id="user-a-id",
                    username="user-a",
                    password_hash="test",
                    display_name="用户甲",
                    role="business",
                ),
                PlatformUser(
                    id="admin-id",
                    username="admin",
                    password_hash="test",
                    display_name="管理员",
                    role="admin",
                ),
                Message(
                    id="message-1",
                    message_type="admin_announcement",
                    source="admin",
                    title="维护公告",
                    summary="今晚维护。",
                    related_type="announcement",
                    related_id="announcement-1",
                    idempotency_key="message-1",
                    published_at=datetime(2026, 8, 13, 9, 0),
                ),
                MessageRecipient(
                    id="recipient-1",
                    message_id="message-1",
                    user_id="user-a",
                ),
            ]
        )
        session.commit()
    yield factory
    app.dependency_overrides.pop(require_user, None)


def test_message_list_requires_authenticated_user():
    response = TestClient(app).get("/api/v1/messages")

    assert response.status_code == 401
    assert response.json()["detail"] == "请先登录"


def test_authenticated_user_can_list_count_and_mark_only_own_messages(api_session):
    app.dependency_overrides[require_user] = lambda: {
        "id": "user-a-id",
        "username": "user-a",
        "displayName": "用户甲",
        "role": "business",
    }
    client = TestClient(app)

    inbox = client.get("/api/v1/messages?read=false")
    count = client.get("/api/v1/messages/unread-count")
    detail = client.get("/api/v1/messages/message-1")
    repeated = client.post("/api/v1/messages/message-1/read")

    assert inbox.status_code == 200
    assert inbox.json()["items"][0]["title"] == "维护公告"
    assert count.json() == {"count": 1}
    assert detail.json()["isRead"] is True
    assert repeated.json()["readAt"] == detail.json()["readAt"]


def test_announcement_writes_are_admin_only_and_return_frozen_publish_contract(api_session):
    client = TestClient(app)
    app.dependency_overrides[require_user] = lambda: {
        "id": "user-a-id",
        "username": "user-a",
        "displayName": "用户甲",
        "role": "business",
    }
    denied = client.post(
        "/api/v1/announcements",
        json={
            "title": "维护公告",
            "body": "今晚维护。",
            "audience": {"userIds": ["user-a"]},
        },
    )
    assert denied.status_code == 403

    app.dependency_overrides[require_user] = lambda: {
        "id": "admin-id",
        "username": "admin",
        "displayName": "管理员",
        "role": "admin",
    }
    created = client.post(
        "/api/v1/announcements",
        json={
            "title": "维护公告",
            "body": "今晚维护。",
            "priority": "high",
            "audience": {"userIds": ["user-a"]},
        },
    )
    assert created.status_code == 201
    announcement_id = created.json()["id"]
    published = client.post(f"/api/v1/announcements/{announcement_id}/publish")
    changed = client.patch(
        f"/api/v1/announcements/{announcement_id}",
        json={"title": "发布后篡改"},
    )
    withdrawn = client.post(
        f"/api/v1/announcements/{announcement_id}/withdraw",
        json={"reason": "维护取消"},
    )

    assert published.status_code == 200
    assert published.json()["status"] == "published"
    assert published.json()["estimatedRecipientCount"] == 1
    assert changed.status_code == 409
    assert withdrawn.json()["status"] == "withdrawn"
    assert withdrawn.json()["withdrawalReason"] == "维护取消"


def test_openapi_contains_message_card_and_announcement_examples():
    schema = TestClient(app).get("/openapi.json").json()

    inbox_example = schema["paths"]["/api/v1/messages"]["get"]["responses"]["200"][
        "content"
    ]["application/json"]["example"]
    announcement_example = schema["components"]["schemas"]["AnnouncementCreate"][
        "example"
    ]

    assert inbox_example["items"][0]["type"] == "weekly_report"
    assert announcement_example["audience"] == {"organizationIds": ["org-east"]}


def test_missing_announcement_returns_not_found(api_session):
    app.dependency_overrides[require_user] = lambda: {
        "id": "admin-id",
        "username": "admin",
        "displayName": "管理员",
        "role": "admin",
    }
    client = TestClient(app, raise_server_exceptions=False)

    response = client.patch(
        "/api/v1/announcements/missing-announcement",
        json={"title": "不存在"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "公告不存在"


def test_announcement_update_rejects_null_for_required_fields(api_session):
    app.dependency_overrides[require_user] = lambda: {
        "id": "admin-id",
        "username": "admin",
        "displayName": "管理员",
        "role": "admin",
    }

    response = TestClient(app).patch(
        "/api/v1/announcements/any-announcement",
        json={"title": None},
    )

    assert response.status_code == 422
