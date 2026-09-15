"""飞书 OAuth 账号绑定、重放防护和注册身份门禁。"""

from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import feishu as feishu_api
from app.auth import service as auth_service
from app.config import settings
from app.db.models import Base, FeishuBinding, FeishuOAuthState, PlatformUser
from app.main import app


@pytest.fixture()
def feishu_session(monkeypatch):
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(auth_service, "PlatformSession", session_factory)
    monkeypatch.setattr(feishu_api, "PlatformSession", session_factory)
    monkeypatch.setattr(auth_service, "write_audit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(feishu_api, "write_audit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(settings, "auth_token_secret", "feishu-test-secret")
    monkeypatch.setattr(settings, "auth_external_identity_gate_enabled", True)
    monkeypatch.setattr(settings, "auth_email_gate_enabled", False)
    monkeypatch.setattr(settings, "demo_mode_enabled", False)
    monkeypatch.setattr(settings, "feishu_app_id", "cli_test_app")
    monkeypatch.setattr(settings, "feishu_app_secret", "test-secret-never-returned")
    monkeypatch.setattr(
        settings,
        "feishu_redirect_uri",
        "http://127.0.0.1:8000/api/feishu/oauth/callback",
    )
    monkeypatch.setattr(settings, "public_app_url", "http://127.0.0.1:5173")
    with session_factory() as session:
        user = PlatformUser(
            id="usr-feishu",
            username="feishu_user",
            password_hash=auth_service.hash_password("pass1234"),
            display_name="飞书用户",
            role="business",
            enabled=True,
            email="feishu@example.com",
            email_canonical="feishu@example.com",
            email_verified_at=datetime.now(),
            external_identity_required=True,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        token = auth_service.create_token(user)
    session_factory.token = token
    yield session_factory
    engine.dispose()


def _headers(session_factory):
    return {"Authorization": f"Bearer {session_factory.token}"}


def _fake_profile():
    return {
        "open_id": "ou_feishu_open",
        "union_id": "on_feishu_union",
        "user_id": "employee-1",
        "tenant_key": "tenant-1",
        "name": "飞书测试用户",
        "avatar_url": "https://example.invalid/avatar.png",
        "mobile": "13800000000",
        "email": "feishu@example.com",
    }


def test_start_oauth_creates_state_without_exposing_secret(feishu_session):
    response = TestClient(app).get(
        "/api/feishu/oauth/start",
        params={"redirectPath": "/login"},
        headers=_headers(feishu_session),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["authUrl"].startswith(
        "https://accounts.feishu.cn/open-apis/authen/v1/authorize?"
    )
    assert "cli_test_app" in body["authUrl"]
    assert "test-secret-never-returned" not in repr(body)
    with feishu_session() as session:
        state = session.query(FeishuOAuthState).one()
        assert state.state_token == body["state"]
        assert state.user_id == "usr-feishu"
        assert state.consumed_at is None


def test_callback_binds_identity_and_rejects_state_replay(monkeypatch, feishu_session):
    client = TestClient(app)
    started = client.get(
        "/api/feishu/oauth/start",
        headers=_headers(feishu_session),
    ).json()
    monkeypatch.setattr(
        feishu_api,
        "_exchange_user_access_token",
        lambda code: {"access_token": f"token-for-{code}"},
    )
    monkeypatch.setattr(feishu_api, "_get_current_feishu_user", lambda _token: _fake_profile())

    callback = client.get(
        "/api/feishu/oauth/callback",
        params={"state": started["state"], "code": "authorization-code"},
        follow_redirects=False,
    )

    assert callback.status_code == 302
    assert "feishuBind=success" in callback.headers["location"]
    with feishu_session() as session:
        binding = session.query(FeishuBinding).one()
        assert binding.user_id == "usr-feishu"
        assert binding.tenant_key == "tenant-1"
        assert binding.union_id == "on_feishu_union"
        assert binding.open_id == "ou_feishu_open"
        assert binding.bind_status == "bound"
        assert session.query(FeishuOAuthState).one().consumed_at is not None

    replay = client.get(
        "/api/feishu/oauth/callback",
        params={"state": started["state"], "code": "authorization-code"},
        follow_redirects=False,
    )
    assert replay.status_code == 302
    assert "feishuBind=failed" in replay.headers["location"]
    assert "state+%E5%B7%B2%E8%A2%AB%E4%BD%BF%E7%94%A8" in replay.headers["location"]


def test_external_identity_gate_opens_after_binding_and_closes_after_unbind(
    monkeypatch,
    feishu_session,
):
    client = TestClient(app)
    blocked = client.get("/api/analysis/sessions", headers=_headers(feishu_session))
    assert blocked.status_code == 403
    assert blocked.json()["errorCode"] == "EXTERNAL_IDENTITY_BINDING_REQUIRED"

    started = client.get(
        "/api/feishu/oauth/start",
        headers=_headers(feishu_session),
    ).json()
    monkeypatch.setattr(
        feishu_api,
        "_exchange_user_access_token",
        lambda _code: {"access_token": "user-access-token"},
    )
    monkeypatch.setattr(feishu_api, "_get_current_feishu_user", lambda _token: _fake_profile())
    client.get(
        "/api/feishu/oauth/callback",
        params={"state": started["state"], "code": "code"},
        follow_redirects=False,
    )

    allowed = client.get("/api/analysis/sessions", headers=_headers(feishu_session))
    assert allowed.status_code == 200
    status = client.get("/api/feishu/binding/me", headers=_headers(feishu_session))
    assert status.status_code == 200
    assert status.json()["bound"] is True
    assert status.json()["displayName"] == "飞书测试用户"

    unbound = client.delete("/api/feishu/binding/me", headers=_headers(feishu_session))
    assert unbound.status_code == 200
    assert unbound.json() == {"ok": True, "bound": False}
    blocked_again = client.get("/api/analysis/sessions", headers=_headers(feishu_session))
    assert blocked_again.status_code == 403
    assert blocked_again.json()["errorCode"] == "EXTERNAL_IDENTITY_BINDING_REQUIRED"


def test_same_feishu_identity_cannot_bind_two_platform_users(monkeypatch, feishu_session):
    with feishu_session() as session:
        other = PlatformUser(
            id="usr-feishu-other",
            username="feishu_other",
            password_hash=auth_service.hash_password("pass1234"),
            role="business",
            enabled=True,
            external_identity_required=True,
        )
        session.add(other)
        session.commit()
        other_token = auth_service.create_token(other)

    client = TestClient(app)
    monkeypatch.setattr(
        feishu_api,
        "_exchange_user_access_token",
        lambda _code: {"access_token": "user-access-token"},
    )
    monkeypatch.setattr(feishu_api, "_get_current_feishu_user", lambda _token: _fake_profile())

    for token, expected in (
        (feishu_session.token, "success"),
        (other_token, "failed"),
    ):
        started = client.get(
            "/api/feishu/oauth/start",
            headers={"Authorization": f"Bearer {token}"},
        ).json()
        callback = client.get(
            "/api/feishu/oauth/callback",
            params={"state": started["state"], "code": "code"},
            follow_redirects=False,
        )
        assert f"feishuBind={expected}" in callback.headers["location"]

    with feishu_session() as session:
        assert session.query(FeishuBinding).count() == 1
        assert session.query(FeishuBinding).one().user_id == "usr-feishu"
