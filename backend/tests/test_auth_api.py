"""平台用户认证 HTTP 契约：注册/登录/me/种子管理员/渐进式身份覆盖。"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.agent import service as analysis_service
from app.auth import service as auth_service
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
    monkeypatch.setattr(auth_service, "write_audit", lambda *args, **_kwargs: None)
    # 渐进式覆盖用例需要 analysis 服务写同一平台库
    monkeypatch.setattr(analysis_service, "PlatformSession", session_factory)
    monkeypatch.setattr(settings, "auth_token_secret", "test-secret")
    monkeypatch.setattr(settings, "auth_admin_username", "admin")
    monkeypatch.setattr(settings, "auth_admin_password", "admin123")
    monkeypatch.setattr(settings, "auth_admin_display_name", "系统管理员")
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
    return session_factory


# ---------- 注册 ----------

def test_register_creates_business_user_with_token(auth_session):
    client = TestClient(app)
    response = client.post(
        "/api/auth/register",
        json={"username": "zhangsan", "password": "pass1234", "displayName": "张三"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["token"].count(".") == 1
    assert body["user"]["id"].startswith("u_")
    assert body["user"]["username"] == "zhangsan"
    assert body["user"]["role"] == "business"
    assert body["user"]["displayName"] == "张三"
    assert "password_hash" not in body["user"]


def test_register_duplicate_username_conflict(auth_session):
    client = TestClient(app)
    payload = {"username": "lisi", "password": "pass1234"}
    assert client.post("/api/auth/register", json=payload).status_code == 201
    duplicate = client.post("/api/auth/register", json=payload)
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "用户名已存在"


def test_register_invalid_credentials_rejected(auth_session):
    client = TestClient(app)
    # 长度合法但含非法字符 → service 层校验 → 400
    assert (
        client.post("/api/auth/register", json={"username": "a@b", "password": "pass1234"}).status_code
        == 400
    )
    assert (
        client.post("/api/auth/register", json={"username": "含中文", "password": "pass1234"}).status_code
        == 400
    )
    # 长度不合法 → pydantic 层校验 → 422
    assert (
        client.post("/api/auth/register", json={"username": "ab", "password": "pass1234"}).status_code
        == 422
    )
    assert (
        client.post("/api/auth/register", json={"username": "zhangsan", "password": "12345"}).status_code
        == 422
    )


# ---------- 登录 ----------

def test_login_success_returns_token(auth_session):
    client = TestClient(app)
    client.post("/api/auth/register", json={"username": "wangwu", "password": "pass1234"})
    response = client.post("/api/auth/login", json={"username": "wangwu", "password": "pass1234"})
    assert response.status_code == 200
    assert response.json()["user"]["username"] == "wangwu"


def test_login_failure_message_does_not_leak_existence(auth_session):
    client = TestClient(app)
    client.post("/api/auth/register", json={"username": "zhaoliu", "password": "pass1234"})
    wrong_password = client.post(
        "/api/auth/login", json={"username": "zhaoliu", "password": "wrong-password"}
    )
    missing_user = client.post(
        "/api/auth/login", json={"username": "ghost-user", "password": "whatever123"}
    )
    assert wrong_password.status_code == 401
    assert missing_user.status_code == 401
    assert wrong_password.json()["detail"] == missing_user.json()["detail"]


# ---------- /me ----------

def test_me_requires_valid_token(auth_session):
    client = TestClient(app)
    client.post("/api/auth/register", json={"username": "qianqi", "password": "pass1234"})
    login = client.post("/api/auth/login", json={"username": "qianqi", "password": "pass1234"})
    token = login.json()["token"]

    ok = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert ok.status_code == 200
    assert ok.json()["user"]["username"] == "qianqi"

    assert client.get("/api/auth/me").status_code == 401

    tampered = token[:-4] + "abcd"
    assert (
        client.get("/api/auth/me", headers={"Authorization": f"Bearer {tampered}"}).status_code == 401
    )


def test_me_rejects_expired_token(auth_session, monkeypatch):
    monkeypatch.setattr(settings, "auth_token_ttl_seconds", -60)
    client = TestClient(app)
    client.post("/api/auth/register", json={"username": "sunba", "password": "pass1234"})
    login = client.post("/api/auth/login", json={"username": "sunba", "password": "pass1234"})
    expired = login.json()["token"]
    assert auth_service.decode_token(expired) is None
    assert (
        client.get("/api/auth/me", headers={"Authorization": f"Bearer {expired}"}).status_code == 401
    )


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
    client.post("/api/auth/register", json={"username": "overrider", "password": "pass1234"})
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
