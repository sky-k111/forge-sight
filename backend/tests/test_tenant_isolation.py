from datetime import datetime

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.agent import service as analysis_service
from app.db.models import Base, DataSource, PlatformUser, Tenant, TenantMembership
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
    monkeypatch.setattr(datasources, "write_audit", lambda **_kwargs: None)
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


def test_sources_and_analysis_sessions_are_tenant_scoped(tenant_session):
    assert [item["id"] for item in datasources.list_sources("tenant-a")] == [11]
    assert [item["id"] for item in datasources.list_sources("tenant-b")] == [22]

    created = analysis_service.create_session("甲工厂调查", 11, "user-a", "tenant-a")
    assert [item["id"] for item in analysis_service.list_sessions("user-a", "tenant-a")] == [created["id"]]
    assert analysis_service.list_sessions("user-a", "tenant-b") == []

    with pytest.raises(analysis_service.AnalysisNotFound):
        analysis_service.create_session("越权调查", 22, "user-a", "tenant-a")
