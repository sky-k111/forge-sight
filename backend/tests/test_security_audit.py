"""审计复核闭环：筛选、真实统计与拦截记录复核。"""

from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import security as security_api
from app.audit import service as audit_service
from app.auth import deps as auth_deps
from app.db.models import AuditLog, Base
from app.main import app
from app.tenancy.service import TenantContext


@pytest.fixture()
def audit_session(monkeypatch):
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(audit_service, "PlatformSession", session_factory)

    def add(user, action, result, resource="", tenant_id="tenant-a", reviewed=None, reviewer=""):
        with session_factory() as session:
            session.add(
                AuditLog(
                    tenant_id=tenant_id,
                    created_at=datetime(2026, 9, 15, 10, 0),
                    user_name=user,
                    action=action,
                    resource=resource,
                    result=result,
                    reviewed_at=reviewed,
                    reviewed_by=reviewer,
                )
            )
            session.commit()

    add("王敏", "查询工序良率", "允许", resource="fact_quality")
    add("王敏", "查询产量", "允许", resource="fact_production")
    add("林涛", "DELETE FROM dim_process", "已拦截", resource="dim_process")
    add("林涛", "DROP TABLE fact_quality", "已拦截", resource="fact_quality")
    add("林涛", "UPDATE fact_quality SET ...", "已拦截", resource="fact_quality", reviewed=datetime(2026, 9, 15, 11, 0), reviewer="admin")
    add("王敏", "口径变更：良率", "已记录", resource="metric_yield")
    add("王敏", "SELECT * FROM other", "已拦截", resource="other", tenant_id="tenant-b")
    return session_factory


def test_list_audit_logs_stats_and_filters(audit_session):
    payload = audit_service.list_audit_logs(tenant_id="tenant-a")
    assert payload["stats"] == {"total": 6, "allowed": 2, "blocked": 3, "recorded": 1, "unreviewed": 2}
    assert set(payload["users"]) == {"王敏", "林涛"}

    blocked = audit_service.list_audit_logs(result="已拦截", tenant_id="tenant-a")
    assert [item["action"] for item in blocked["items"]] == [
        "UPDATE fact_quality SET ...",
        "DROP TABLE fact_quality",
        "DELETE FROM dim_process",
    ]

    pending = audit_service.list_audit_logs(reviewed="待复核", tenant_id="tenant-a")
    assert {item["action"] for item in pending["items"]} == {
        "DELETE FROM dim_process",
        "DROP TABLE fact_quality",
    }
    reviewed = audit_service.list_audit_logs(reviewed="已复核", tenant_id="tenant-a")
    assert [item["reviewedBy"] for item in reviewed["items"]] == ["admin"]

    by_user = audit_service.list_audit_logs(user="林涛", tenant_id="tenant-a")
    assert len(by_user["items"]) == 3
    by_keyword = audit_service.list_audit_logs(keyword="dim_process", tenant_id="tenant-a")
    assert [item["action"] for item in by_keyword["items"]] == ["DELETE FROM dim_process"]
    # 关键词按字面匹配，% 不作为通配符
    wildcard = audit_service.list_audit_logs(keyword="%", tenant_id="tenant-a")
    assert wildcard["items"] == []


def test_audit_cursor_snapshot_and_export_are_tenant_scoped(audit_session):
    first = audit_service.list_audit_logs(tenant_id="tenant-a", limit=2)
    assert first["page"]["matchedTotal"] == 6
    assert first["page"]["hasMore"] is True
    with audit_session() as session:
        session.add(AuditLog(tenant_id="tenant-a", user_name="=CMD()", action="new", result="允许"))
        session.commit()
    items = list(first["items"])
    cursor = first["page"]["nextCursor"]
    while cursor:
        page = audit_service.list_audit_logs(tenant_id="tenant-a", limit=2, cursor=cursor, snapshot_id=first["page"]["snapshotId"])
        assert page["page"]["matchedTotal"] == 6
        items.extend(page["items"])
        cursor = page["page"]["nextCursor"]
    assert len({item["id"] for item in items}) == 6
    csv = "".join(audit_service.export_audit_csv(tenant_id="tenant-a"))
    assert "other" not in csv
    assert "'=CMD()" in csv
    assert csv.startswith("\ufeff")


def test_review_audit_log_marks_only_tenant_blocked_rows(audit_session):
    target = next(
        row.id
        for row in audit_session().query(AuditLog).filter_by(action="DELETE FROM dim_process")
    )
    result = audit_service.review_audit_log(target, tenant_id="tenant-a", reviewer="admin")
    assert result is not None and result["reviewedBy"] == "admin"
    row = audit_session().get(AuditLog, target)
    assert row.reviewed_at is not None

    # 重复复核仍返回成功（幂等展示由前端过滤）
    assert audit_service.review_audit_log(target, tenant_id="tenant-a", reviewer="admin") is not None
    # 非本租户、非拦截记录、不存在均拒绝
    other = next(row.id for row in audit_session().query(AuditLog).filter_by(tenant_id="tenant-b"))
    assert audit_service.review_audit_log(other, tenant_id="tenant-a", reviewer="admin") is None
    allowed = next(row.id for row in audit_session().query(AuditLog).filter_by(result="允许"))
    assert audit_service.review_audit_log(allowed, tenant_id="tenant-a", reviewer="admin") is None
    assert audit_service.review_audit_log(9999, tenant_id="tenant-a", reviewer="admin") is None


def test_review_batch_marks_only_unreviewed_blocked_rows(audit_session):
    all_ids = [row.id for row in audit_session().query(AuditLog).filter_by(tenant_id="tenant-a")]
    result = audit_service.review_audit_logs_batch(all_ids, tenant_id="tenant-a", reviewer="admin")
    assert result == {"reviewed": 2, "requested": 6}
    # 再次批量复核不重复计数；已复核/非拦截/跨租户记录均不受影响
    assert audit_service.review_audit_logs_batch(all_ids, tenant_id="tenant-a", reviewer="admin") == {
        "reviewed": 0,
        "requested": 6,
    }
    payload = audit_service.list_audit_logs(tenant_id="tenant-a")
    assert payload["stats"]["unreviewed"] == 0 and payload["stats"]["blocked"] == 3
    other = audit_service.list_audit_logs(tenant_id="tenant-b")
    assert other["stats"]["unreviewed"] == 1
    assert audit_service.review_audit_logs_batch([], tenant_id="tenant-a", reviewer="admin") == {
        "reviewed": 0,
        "requested": 0,
    }


def test_security_api_review_flow(audit_session, monkeypatch):
    context = TenantContext(
        tenant_id="tenant-a",
        tenant_name="A",
        tenant_slug="a",
        member_role="owner",
        user_id="u-1",
    )
    app.dependency_overrides[security_api.require_tenant_admin] = lambda: context
    app.dependency_overrides[auth_deps.optional_user] = lambda: {"username": "admin"}
    try:
        client = TestClient(app)
        payload = client.get("/api/security/audit-logs", params={"reviewed": "待复核"}).json()
        assert payload["stats"]["unreviewed"] == 2
        target = payload["items"][0]["id"]
        reviewed = client.post(f"/api/security/audit-logs/{target}/review")
        assert reviewed.status_code == 200
        assert reviewed.json()["reviewedBy"] == "admin"
        after = client.get("/api/security/audit-logs", params={"reviewed": "待复核"}).json()
        assert after["stats"]["unreviewed"] == 1
        missing = client.post("/api/security/audit-logs/9999/review")
        assert missing.status_code == 404
        # 批量复核清掉剩余待办，列表项带完整时间与详情
        remaining = after["items"][0]["id"]
        batch = client.post("/api/security/audit-logs/review-batch", json={"ids": [remaining]})
        assert batch.status_code == 200 and batch.json() == {"reviewed": 1, "requested": 1}
        cleared = client.get("/api/security/audit-logs", params={"reviewed": "待复核"}).json()
        assert cleared["stats"]["unreviewed"] == 0 and cleared["items"] == []
        detail_row = client.get("/api/security/audit-logs", params={"reviewed": "已复核"}).json()
        assert all(item["at"] and "detail" in item for item in detail_row["items"])
        downloaded = client.get("/api/security/audit-logs/export", params={"user": "王敏"})
        assert downloaded.status_code == 200
        assert "林涛" not in downloaded.text and "王敏" in downloaded.text
        assert client.get("/api/security/audit-logs", params={"limit": 201}).status_code == 422
    finally:
        app.dependency_overrides.clear()


def test_runtime_configuration_is_real_readonly_and_secret_free(monkeypatch):
    from app.api import model_settings
    from app.metadata import datasources
    from app.config import settings
    context = TenantContext(tenant_id="tenant-a", tenant_name="A", tenant_slug="a", member_role="owner", user_id="u-1")
    app.dependency_overrides[security_api.require_tenant_admin] = lambda: context
    monkeypatch.setattr(datasources, "list_sources", lambda tenant: [{"name": "实际数据源 " + tenant, "typeLabel": "MySQL", "status": "未测试", "password": "SECRET"}])
    monkeypatch.setattr(model_settings, "_payload", lambda: {"model": "real-model", "protocol": "openai_compatible", "configured": False, "apiKey": "SECRET"})
    monkeypatch.setattr(settings, "sql_timeout_seconds", 17)
    try:
        response = TestClient(app).get("/api/security/runtime")
        assert response.status_code == 200
        assert "SECRET" not in response.text
        assert "实际数据源 tenant-a" in response.text and "real-model" in response.text
        assert any(policy["value"] == "17s" for policy in response.json()["policies"])
        assert TestClient(app).patch("/api/security/runtime", json={"readonly": False}).status_code == 405
    finally:
        app.dependency_overrides.clear()
