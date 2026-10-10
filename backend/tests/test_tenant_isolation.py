from datetime import datetime

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.agent import service as analysis_service
from app.config import settings
from app.db.models import (
    AgentMemory,
    Base,
    DataSource,
    PlatformUser,
    Tenant,
    TenantMembership,
)
from app.knowledge import candidates as knowledge_candidates
from app.knowledge import service as knowledge_service
from app.memory import service as memory_service
from app.metadata import datasources
from app.tenancy import service as tenancy


@pytest.fixture()
def tenant_session(monkeypatch):
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(tenancy, "PlatformSession", factory)
    monkeypatch.setattr(datasources, "PlatformSession", factory)
    monkeypatch.setattr(analysis_service, "PlatformSession", factory)
    monkeypatch.setattr(knowledge_service, "PlatformSession", factory)
    monkeypatch.setattr(knowledge_candidates, "PlatformSession", factory)
    monkeypatch.setattr(memory_service, "PlatformSession", factory)
    monkeypatch.setattr(datasources, "write_audit", lambda **_kwargs: None)
    monkeypatch.setattr(knowledge_service, "write_audit", lambda **_kwargs: None)
    with factory() as session:
        session.add_all([
            Tenant(id="tenant-a", slug="factory-a", name="甲制造"),
            Tenant(id="tenant-b", slug="factory-b", name="乙制造"),
            PlatformUser(
                id="user-a", username="user_a", password_hash="test", display_name="甲用户",
                role="business", enabled=True, created_at=datetime.now(),
            ),
            TenantMembership(
                id="tm-a", tenant_id="tenant-a", user_id="user-a",
                member_role="owner", is_default=True,
            ),
            DataSource(
                id=11, tenant_id="tenant-a", name="甲数据底座", type="mysql",
                db_name="factory_a", username="reader", is_default=True,
            ),
            DataSource(
                id=22, tenant_id="tenant-b", name="乙数据底座", type="postgresql",
                db_name="factory_b", username="reader", is_default=True,
            ),
        ])
        session.commit()
    return factory


def test_membership_rejects_cross_tenant_selection(tenant_session):
    user = {"id": "user-a", "role": "business"}
    context = tenancy.resolve_tenant_context(user, "tenant-a")
    assert context.tenant_name == "甲制造"
    assert context.can_administer is True

    with pytest.raises(HTTPException) as exc:
        tenancy.resolve_tenant_context(user, "tenant-b")
    assert exc.value.status_code == 403


def test_legacy_backfill_keeps_new_users_in_their_private_tenant(tenant_session):
    with tenant_session() as session:
        session.add(
            PlatformUser(
                id="legacy-user", username="legacy_user", password_hash="test",
                role="business", enabled=True,
            )
        )
        session.commit()

    tenancy.seed_legacy_memberships()

    with tenant_session() as session:
        private_memberships = session.query(TenantMembership).filter_by(user_id="user-a").all()
        legacy_memberships = session.query(TenantMembership).filter_by(user_id="legacy-user").all()
        assert [(row.tenant_id, row.is_default) for row in private_memberships] == [("tenant-a", True)]
        assert [(row.tenant_id, row.is_default) for row in legacy_memberships] == [("tenant-default", True)]


def test_sources_and_analysis_sessions_are_tenant_scoped(tenant_session):
    assert [item["id"] for item in datasources.list_sources("tenant-a")] == [11]
    assert [item["id"] for item in datasources.list_sources("tenant-b")] == [22]

    created = analysis_service.create_session("甲工厂调查", 11, "user-a", "tenant-a")
    assert [item["id"] for item in analysis_service.list_sessions("user-a", "tenant-a")] == [created["id"]]
    assert analysis_service.list_sessions("user-a", "tenant-b") == []

    with pytest.raises(analysis_service.AnalysisNotFound):
        analysis_service.create_session("越权调查", 22, "user-a", "tenant-a")


def test_knowledge_codes_are_unique_per_tenant(tenant_session):
    """跨租户允许复用同一稳定编码；同租户内仍然冲突。"""

    created_a = knowledge_service.create_topic({"code": "QUAL", "name": "甲-质量"}, "tenant-a")
    created_b = knowledge_service.create_topic({"code": "QUAL", "name": "乙-质量"}, "tenant-b")
    assert {created_a["code"], created_b["code"]} == {"QUAL"}

    with pytest.raises(knowledge_service.KnowledgeConflict):
        knowledge_service.create_topic({"code": "QUAL", "name": "同租户重复"}, "tenant-a")

    assert [item["code"] for item in knowledge_service.list_topics("tenant-a")] == ["QUAL"]
    assert [item["code"] for item in knowledge_service.list_topics("tenant-b")] == ["QUAL"]


def test_auto_codes_are_generated_per_tenant(tenant_session):
    first_a = knowledge_service.create_rule({"name": "甲规则一", "scope": "全局"}, "tenant-a")
    second_a = knowledge_service.create_rule({"name": "甲规则二", "scope": "全局"}, "tenant-a")
    first_b = knowledge_service.create_rule({"name": "乙规则一", "scope": "全局"}, "tenant-b")

    assert [first_a["id"], second_a["id"]] == ["R-001", "R-002"]
    # 乙企业不受甲企业编码序列影响，同样从 R-001 开始。
    assert first_b["id"] == "R-001"


def test_analysis_access_requires_same_owner_within_tenant(tenant_session):
    created = analysis_service.create_session("甲调查", 11, "user-a", "tenant-a")

    with tenant_session() as session:
        row = tenancy.ensure_analysis_session_access(
            session, created["id"], "tenant-a", user_id="user-a"
        )
        assert row.id == created["id"]

        with pytest.raises(HTTPException) as exc:
            tenancy.ensure_analysis_session_access(
                session, created["id"], "tenant-a", user_id="someone-else"
            )
    assert exc.value.status_code == 404


def test_anonymous_requests_only_escalate_in_demo_mode(tenant_session, monkeypatch):
    monkeypatch.setattr(settings, "demo_mode_enabled", False)
    assert tenancy.resolve_tenant_context(None, None).can_administer is False

    monkeypatch.setattr(settings, "demo_mode_enabled", True)
    assert tenancy.resolve_tenant_context(None, None).can_administer is True


def test_memory_writes_default_to_normalized_default_tenant(tenant_session):
    memory_service.remember("记住图表偏好", user_id="user-a", source_id=11)

    with tenant_session() as session:
        row = session.query(AgentMemory).one()
    assert row.tenant_id == tenancy.DEFAULT_TENANT_ID
