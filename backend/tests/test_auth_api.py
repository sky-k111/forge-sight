"""平台用户认证 HTTP 契约：注册/登录/me/种子管理员/渐进式身份覆盖。"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.agent import service as analysis_service
from app.api import auth as auth_api
from app.auth import email_verification
from app.auth import service as auth_service
from app.auth.mail_provider import CaptureMailProvider
from app.auth.deps import validate_email_gate_configuration
from app.config import settings
from app.db.models import Base, DataSource, PlatformUser
from app.main import app


@pytest.fixture()
def auth_session(monkeypatch):
    """认证隔离环境：SQLite 平台库 + 打桩 token secret/admin 配置 + 短路审计。"""
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(auth_service, "PlatformSession", session_factory)
    monkeypatch.setattr(email_verification, "PlatformSession", session_factory)
    monkeypatch.setattr(auth_service, "write_audit", lambda *args, **_kwargs: None)
    # 渐进式覆盖用例需要 analysis 服务写同一平台库
    monkeypatch.setattr(analysis_service, "PlatformSession", session_factory)
    monkeypatch.setattr(settings, "auth_token_secret", "test-secret")
    monkeypatch.setattr(settings, "auth_admin_username", "admin")
    monkeypatch.setattr(settings, "auth_admin_password", "admin123")
    monkeypatch.setattr(settings, "auth_admin_display_name", "系统管理员")
    monkeypatch.setattr(settings, "email_code_secret", "test-email-secret")
    monkeypatch.setattr(settings, "auth_email_gate_enabled", False)
    monkeypatch.setattr(settings, "auth_email_gate_allow_capture", True)
    monkeypatch.setattr(settings, "demo_mode_enabled", True)
    provider = CaptureMailProvider()
    monkeypatch.setattr(auth_api, "get_verification_mail_provider", lambda: provider)
    audit_events = []
    monkeypatch.setattr(auth_api, "write_audit", lambda *args, **kwargs: audit_events.append((args, kwargs)))
    with session_factory() as session:
        session.add(
            DataSource(
                name="认证测试数据源",
                type="mysql",
                host="127.0.0.1",
                port=3306,
                db_name="auth_test",
                username="reader",
                status="连接正常",
                is_default=True,
            )
        )
        session.commit()
    session_factory.mail_provider = provider
    session_factory.audit_events = audit_events
    return session_factory


# ---------- 注册 ----------

def test_register_creates_business_user_with_token(auth_session):
    client = TestClient(app)
    response = client.post(
        "/api/auth/register",
        json={
            "username": "zhangsan",
            "password": "pass1234",
            "displayName": "张三",
            "email": "ZhangSan@Example.COM",
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["token"].count(".") == 1
    assert body["user"]["id"].startswith("u_")
    assert body["user"]["username"] == "zhangsan"
    assert body["user"]["role"] == "business"
    assert body["user"]["displayName"] == "张三"
    assert body["user"]["emailMasked"] == "Z***@example.com"
    assert body["user"]["emailStatus"] == "pending_verification"
    assert body["nextAction"] == "verify_email"
    assert auth_service.decode_token(body["token"])["auth_scope"] == "restricted"
    assert "email" not in body["user"]
    assert "password_hash" not in body["user"]


def test_register_duplicate_username_conflict(auth_session):
    client = TestClient(app)
    payload = {"username": "lisi", "password": "pass1234", "email": "lisi@example.com"}
    assert client.post("/api/auth/register", json=payload).status_code == 201
    duplicate = client.post("/api/auth/register", json=payload)
    assert duplicate.status_code == 409
    assert duplicate.json()["errorCode"] == "AUTH_USERNAME_TAKEN"


def test_register_invalid_credentials_rejected(auth_session):
    client = TestClient(app)
    # 长度合法但含非法字符 → service 层校验 → 400
    assert (
        client.post(
            "/api/auth/register",
            json={"username": "a@b", "password": "pass1234", "email": "a@example.com"},
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/api/auth/register",
            json={"username": "含中文", "password": "pass1234", "email": "b@example.com"},
        ).status_code
        == 400
    )
    # 长度不合法 → pydantic 层校验 → 422
    assert (
        client.post(
            "/api/auth/register",
            json={"username": "ab", "password": "pass1234", "email": "ab@example.com"},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/auth/register",
            json={"username": "zhangsan", "password": "12345", "email": "z@example.com"},
        ).status_code
        == 422
    )


# ---------- 登录 ----------

def test_login_success_returns_token(auth_session):
    client = TestClient(app)
    client.post(
        "/api/auth/register",
        json={"username": "wangwu", "password": "pass1234", "email": "wangwu@example.com"},
    )
    response = client.post("/api/auth/login", json={"username": "wangwu", "password": "pass1234"})
    assert response.status_code == 200
    assert response.json()["user"]["username"] == "wangwu"
    assert response.json()["nextAction"] == "verify_email"


def test_register_requires_valid_unique_email(auth_session):
    client = TestClient(app)
    missing = client.post(
        "/api/auth/register", json={"username": "missingmail", "password": "pass1234"}
    )
    invalid = client.post(
        "/api/auth/register",
        json={"username": "badmail", "password": "pass1234", "email": "not-an-email"},
    )
    first = client.post(
        "/api/auth/register",
        json={"username": "mailone", "password": "pass1234", "email": "User@Example.com"},
    )
    duplicate = client.post(
        "/api/auth/register",
        json={"username": "mailtwo", "password": "pass1234", "email": "user@example.COM"},
    )

    assert missing.status_code == 422
    assert invalid.status_code == 400
    assert invalid.json()["errorCode"] == "AUTH_INVALID_EMAIL_FORMAT"
    assert first.status_code == 201
    assert duplicate.status_code == 409
    assert duplicate.json()["errorCode"] == "EMAIL_TAKEN"


def test_login_failure_message_does_not_leak_existence(auth_session):
    client = TestClient(app)
    client.post(
        "/api/auth/register",
        json={"username": "zhaoliu", "password": "pass1234", "email": "zhaoliu@example.com"},
    )
    wrong_password = client.post(
        "/api/auth/login", json={"username": "zhaoliu", "password": "wrong-password"}
    )
    missing_user = client.post(
        "/api/auth/login", json={"username": "ghost-user", "password": "whatever123"}
    )
    assert wrong_password.status_code == 401
    assert missing_user.status_code == 401
    assert wrong_password.json()["detail"] == missing_user.json()["detail"]
    assert wrong_password.json()["errorCode"] == "AUTH_INVALID_CREDENTIALS"


# ---------- /me ----------

def test_me_requires_valid_token(auth_session):
    client = TestClient(app)
    client.post(
        "/api/auth/register",
        json={"username": "qianqi", "password": "pass1234", "email": "qianqi@example.com"},
    )
    login = client.post("/api/auth/login", json={"username": "qianqi", "password": "pass1234"})
    token = login.json()["token"]

    ok = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert ok.status_code == 200
    assert ok.json()["user"]["username"] == "qianqi"
    assert ok.json()["user"]["emailMasked"] == "q***@example.com"
    assert ok.json()["user"]["emailStatus"] == "pending_verification"

    assert client.get("/api/auth/me").status_code == 401

    tampered = token[:-4] + "abcd"
    assert (
        client.get("/api/auth/me", headers={"Authorization": f"Bearer {tampered}"}).status_code == 401
    )


def test_me_rejects_expired_token(auth_session, monkeypatch):
    monkeypatch.setattr(settings, "auth_token_ttl_seconds", -60)
    client = TestClient(app)
    client.post(
        "/api/auth/register",
        json={"username": "sunba", "password": "pass1234", "email": "sunba@example.com"},
    )
    login = client.post("/api/auth/login", json={"username": "sunba", "password": "pass1234"})
    expired = login.json()["token"]
    assert auth_service.decode_token(expired) is None
    assert (
        client.get("/api/auth/me", headers={"Authorization": f"Bearer {expired}"}).status_code == 401
    )


def test_email_challenge_verify_and_replay_flow(auth_session):
    client = TestClient(app)
    registered = client.post(
        "/api/auth/register",
        json={"username": "verifyme", "password": "pass1234", "email": "Verify@Example.com"},
    ).json()
    headers = {"Authorization": f"Bearer {registered['token']}"}

    challenge = client.post("/api/auth/email/challenges", json={}, headers=headers)

    assert challenge.status_code == 200
    assert challenge.json()["emailMasked"] == "V***@example.com"
    assert challenge.json()["purpose"] == "verify"
    code = auth_session.mail_provider.messages[-1].code
    verified = client.post(
        "/api/auth/email/verify",
        json={"email": "verify@example.com", "code": code},
        headers=headers,
    )
    assert verified.status_code == 200
    assert verified.json()["user"]["emailStatus"] == "verified"
    assert verified.json()["nextAction"] is None
    assert auth_service.decode_token(verified.json()["token"])["auth_scope"] == "full"

    replay = client.post(
        "/api/auth/email/verify",
        json={"email": "verify@example.com", "code": code},
        headers=headers,
    )
    assert replay.status_code == 400
    assert replay.json()["errorCode"] == "CODE_INVALID"
    audit_actions = [args[0] for args, _kwargs in auth_session.audit_events]
    assert "请求邮箱验证码" in audit_actions
    assert "邮箱验证成功" in audit_actions
    assert "邮箱验证失败" in audit_actions
    assert "Verify@Example.com" not in repr(auth_session.audit_events)


def test_unbound_existing_user_gets_bind_action_and_can_submit_email(auth_session):
    auth_service.seed_admin()
    client = TestClient(app)
    logged_in = client.post(
        "/api/auth/login", json={"username": "admin", "password": "admin123"}
    )
    headers = {"Authorization": f"Bearer {logged_in.json()['token']}"}

    assert logged_in.status_code == 200
    assert logged_in.json()["user"]["emailStatus"] == "unbound"
    assert logged_in.json()["nextAction"] == "bind_email"
    missing = client.post("/api/auth/email/challenges", json={}, headers=headers)
    assert missing.status_code == 400
    assert missing.json()["errorCode"] == "AUTH_INVALID_EMAIL_FORMAT"

    challenge = client.post(
        "/api/auth/email/challenges",
        json={"email": "admin@example.com"},
        headers=headers,
    )
    assert challenge.status_code == 200
    assert challenge.json()["purpose"] == "bind"


def test_verified_user_can_change_email_without_changing_password_login(auth_session):
    client = TestClient(app)
    registered = client.post(
        "/api/auth/register",
        json={"username": "changer", "password": "pass1234", "email": "old@example.com"},
    ).json()
    headers = {"Authorization": f"Bearer {registered['token']}"}
    client.post("/api/auth/email/challenges", json={}, headers=headers)
    first_code = auth_session.mail_provider.messages[-1].code
    verified = client.post(
        "/api/auth/email/verify",
        json={"email": "old@example.com", "code": first_code},
        headers=headers,
    ).json()
    full_headers = {"Authorization": f"Bearer {verified['token']}"}

    changed = client.post(
        "/api/auth/email/change",
        json={"email": "New@Example.com"},
        headers=full_headers,
    )
    assert changed.status_code == 200
    assert changed.json()["purpose"] == "change"
    change_code = auth_session.mail_provider.messages[-1].code
    confirmed = client.post(
        "/api/auth/email/verify",
        json={"email": "new@example.com", "code": change_code},
        headers=full_headers,
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["user"]["emailMasked"] == "N***@example.com"

    login_again = client.post(
        "/api/auth/login", json={"username": "changer", "password": "pass1234"}
    )
    assert login_again.status_code == 200
    assert login_again.json()["user"]["emailStatus"] == "verified"
    assert login_again.json()["nextAction"] is None


# ---------- 种子管理员 ----------

def test_seed_admin_is_idempotent(auth_session):
    auth_service.seed_admin()
    auth_service.seed_admin()
    with auth_session() as session:
        admins = session.query(PlatformUser).filter_by(username="admin").all()
        assert len(admins) == 1
        assert admins[0].role == "admin"
        assert admins[0].display_name == "系统管理员"


# ---------- 渐进式身份覆盖 ----------

def test_token_identity_overrides_client_user_id(auth_session):
    client = TestClient(app)
    client.post(
        "/api/auth/register",
        json={"username": "overrider", "password": "pass1234", "email": "over@example.com"},
    )
    login = client.post("/api/auth/login", json={"username": "overrider", "password": "pass1234"})
    token = login.json()["token"]

    # 带 token 但 body 故意传 local-test-user → 归属应为 overrider
    created = client.post(
        "/api/analysis/sessions",
        headers={"Authorization": f"Bearer {token}"},
        json={"title": "身份覆盖会话", "sourceId": 1, "userId": "local-test-user"},
    )
    assert created.status_code == 201
    listed = client.get("/api/analysis/sessions", params={"userId": "overrider"}).json()
    assert any(item["id"] == created.json()["id"] for item in listed)
    # local-test-user 名下查不到（该会话被覆盖给 overrider）
    legacy = client.get("/api/analysis/sessions", params={"userId": "local-test-user"}).json()
    assert all(item["id"] != created.json()["id"] for item in legacy)


def test_no_token_falls_back_to_client_user_id(auth_session):
    client = TestClient(app)
    created = client.post(
        "/api/analysis/sessions",
        json={"title": "演示模式会话", "sourceId": 1, "userId": "local-test-user"},
    )
    assert created.status_code == 201
    listed = client.get("/api/analysis/sessions", params={"userId": "local-test-user"}).json()
    assert any(item["id"] == created.json()["id"] for item in listed)


# ---------- 邮箱访问门禁 ----------

def test_email_gate_blocks_restricted_and_anonymous_business_access(auth_session, monkeypatch):
    monkeypatch.setattr(settings, "auth_email_gate_enabled", True)
    monkeypatch.setattr(settings, "demo_mode_enabled", True)
    client = TestClient(app)
    registered = client.post(
        "/api/auth/register",
        json={"username": "gated", "password": "pass1234", "email": "gated@example.com"},
    ).json()

    restricted = client.get(
        "/api/analysis/sessions",
        headers={"Authorization": f"Bearer {registered['token']}"},
    )
    anonymous = client.get("/api/analysis/sessions")

    assert restricted.status_code == 403
    assert restricted.json()["errorCode"] == "EMAIL_VERIFICATION_REQUIRED"
    assert anonymous.status_code == 401
    assert anonymous.json()["errorCode"] == "AUTH_REQUIRED"


def test_email_gate_allows_restricted_auth_whitelist(auth_session, monkeypatch):
    monkeypatch.setattr(settings, "auth_email_gate_enabled", True)
    client = TestClient(app)
    registered = client.post(
        "/api/auth/register",
        json={"username": "whitelist", "password": "pass1234", "email": "white@example.com"},
    ).json()
    headers = {"Authorization": f"Bearer {registered['token']}"}

    assert client.get("/api/auth/me", headers=headers).status_code == 200
    assert client.post("/api/auth/email/challenges", json={}, headers=headers).status_code == 200
    assert client.post("/api/auth/logout", headers=headers).status_code == 200


def test_email_gate_allows_verified_full_token(auth_session, monkeypatch):
    monkeypatch.setattr(settings, "auth_email_gate_enabled", True)
    client = TestClient(app)
    registered = client.post(
        "/api/auth/register",
        json={"username": "fulluser", "password": "pass1234", "email": "full@example.com"},
    ).json()
    headers = {"Authorization": f"Bearer {registered['token']}"}
    client.post("/api/auth/email/challenges", json={}, headers=headers)
    code = auth_session.mail_provider.messages[-1].code
    verified = client.post(
        "/api/auth/email/verify",
        json={"email": "full@example.com", "code": code},
        headers=headers,
    ).json()

    response = client.get(
        "/api/analysis/sessions",
        headers={"Authorization": f"Bearer {verified['token']}"},
    )
    assert response.status_code == 200


def test_email_gate_rechecks_full_token_against_database(auth_session, monkeypatch):
    monkeypatch.setattr(settings, "auth_email_gate_enabled", True)
    client = TestClient(app)
    registered = client.post(
        "/api/auth/register",
        json={"username": "rechecked", "password": "pass1234", "email": "check@example.com"},
    ).json()
    headers = {"Authorization": f"Bearer {registered['token']}"}
    client.post("/api/auth/email/challenges", json={}, headers=headers)
    code = auth_session.mail_provider.messages[-1].code
    verified = client.post(
        "/api/auth/email/verify",
        json={"email": "check@example.com", "code": code},
        headers=headers,
    ).json()
    with auth_session() as session:
        user = session.query(PlatformUser).filter_by(username="rechecked").one()
        user.email_verified_at = None
        session.commit()

    response = client.get(
        "/api/analysis/sessions",
        headers={"Authorization": f"Bearer {verified['token']}"},
    )
    assert response.status_code == 403


def test_gate_disabled_uses_explicit_demo_mode(auth_session, monkeypatch):
    client = TestClient(app)
    monkeypatch.setattr(settings, "auth_email_gate_enabled", False)
    monkeypatch.setattr(settings, "demo_mode_enabled", False)
    assert client.get("/api/analysis/sessions").status_code == 401

    monkeypatch.setattr(settings, "demo_mode_enabled", True)
    assert client.get("/api/analysis/sessions").status_code == 200


def test_startup_guard_rejects_gate_with_capture_provider(monkeypatch):
    monkeypatch.setattr(settings, "auth_email_gate_enabled", True)
    monkeypatch.setattr(settings, "mail_provider", "capture")
    monkeypatch.setattr(settings, "auth_email_gate_allow_capture", False)

    with pytest.raises(RuntimeError, match="AUTH_EMAIL_GATE_ALLOW_CAPTURE=true"):
        validate_email_gate_configuration()

    monkeypatch.setattr(settings, "auth_email_gate_allow_capture", True)
    validate_email_gate_configuration()
