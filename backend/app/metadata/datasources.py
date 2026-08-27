"""数据源管理：登记、测试连接、触发扫描。"""

from urllib.parse import urlparse

from app.audit.service import write_audit
from app.config import settings
from app.db.models import DataSource
from app.db.platform import PlatformSession
from app.gateway.adapters import SUPPORTED_TYPES, drop_engine, test_connection
from app.tenancy.service import DEFAULT_TENANT_ID


def seed_default_source() -> DataSource:
    """启动时确保默认数据源存在（取自 .env 的 BUSINESS_DB_URL）。"""
    with PlatformSession() as session:
        source = session.query(DataSource).filter_by(
            tenant_id=DEFAULT_TENANT_ID, is_default=True
        ).first()
        if source is not None:
            return source
        parsed = urlparse(settings.business_db_url)
        source = DataSource(
            tenant_id=DEFAULT_TENANT_ID,
            name="华东制造数据底座",
            type="mysql",
            host=parsed.hostname or "127.0.0.1",
            port=parsed.port or 3306,
            db_name=parsed.path.lstrip("/"),
            username=parsed.username or "",
            password=parsed.password or "",
            status="连接正常",
            is_default=True,
        )
        session.add(source)
        session.commit()
        session.refresh(source)
        return source


def list_sources(tenant_id: str = DEFAULT_TENANT_ID) -> list[dict]:
    with PlatformSession() as session:
        sources = (
            session.query(DataSource)
            .filter_by(tenant_id=tenant_id)
            .order_by(DataSource.id)
            .all()
        )
        from app.retrieval.index_jobs import latest_job, serialize_job

        return [
            {
                "id": s.id,
                "name": s.name,
                "type": s.type,
                "typeLabel": SUPPORTED_TYPES.get(s.type, {}).get("label", s.type),
                "host": s.host,
                "port": s.port,
                "database": s.db_name,
                "username": s.username,
                "status": s.status,
                "isDefault": s.is_default,
                "semanticIndex": serialize_job(latest_job(session, s.id)),
            }
            for s in sources
        ]


def create_source(payload: dict, tenant_id: str = DEFAULT_TENANT_ID) -> dict:
    source_type = payload.get("type", "")
    if source_type not in SUPPORTED_TYPES:
        raise ValueError(f"不支持的数据源类型：{source_type}（当前支持 mysql / postgresql）")
    with PlatformSession() as session:
        source = DataSource(
            tenant_id=tenant_id,
            name=payload["name"],
            type=source_type,
            host=payload.get("host", "127.0.0.1"),
            port=int(payload.get("port") or SUPPORTED_TYPES[source_type]["default_port"]),
            db_name=payload["database"],
            username=payload["username"],
            password=payload.get("password", ""),
            status="未测试",
            is_default=False,
        )
        session.add(source)
        session.commit()
        session.refresh(source)
        source_id = source.id
    write_audit(action=f"登记数据源 {payload['name']}", resource=payload["database"], result="已记录", role="管理员")
    return {"id": source_id}


def test_source(source_id: int, tenant_id: str = DEFAULT_TENANT_ID) -> dict:
    with PlatformSession() as session:
        source = session.query(DataSource).filter_by(id=source_id, tenant_id=tenant_id).first()
        if source is None:
            raise KeyError("数据源不存在")
        ok, message = test_connection(source)
        source.status = "连接正常" if ok else "连接失败"
        session.commit()
    write_audit(
        action="测试数据源连接",
        resource=f"{source.type}://{source.host}:{source.port}/{source.db_name}",
        result="允许" if ok else "已记录",
        detail=message,
        role="管理员",
    )
    if not ok:
        drop_engine(source_id)
    return {"ok": ok, "status": "连接正常" if ok else "连接失败", "message": message}


def update_source(source_id: int, payload: dict, tenant_id: str = DEFAULT_TENANT_ID) -> dict:
    """修改已有数据源配置。password 传空表示不修改；连接参数变化后状态重置为未测试。"""
    with PlatformSession() as session:
        source = session.query(DataSource).filter_by(id=source_id, tenant_id=tenant_id).first()
        if source is None:
            raise KeyError("数据源不存在")
        connection_changed = False
        for field, attr in (("name", "name"), ("host", "host"), ("database", "db_name"), ("username", "username")):
            new_value = payload.get(field)
            if new_value and getattr(source, attr) != new_value:
                if attr != "name":
                    connection_changed = True
                setattr(source, attr, new_value)
        if payload.get("port") and source.port != int(payload["port"]):
            source.port = int(payload["port"])
            connection_changed = True
        if payload.get("password") and source.password != payload["password"]:
            source.password = payload["password"]
            connection_changed = True
        if connection_changed:
            source.status = "未测试"
        session.commit()
        name, db_name = source.name, source.db_name
    if connection_changed:
        drop_engine(source_id)
    write_audit(action=f"修改数据源配置 {name}", resource=db_name, result="已记录", role="管理员")
    return {"id": source_id}


def get_source(source_id: int, tenant_id: str = DEFAULT_TENANT_ID) -> DataSource | None:
    with PlatformSession() as session:
        return session.query(DataSource).filter_by(id=source_id, tenant_id=tenant_id).first()
