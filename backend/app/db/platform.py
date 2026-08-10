"""平台库 forge_sight_app 的读写连接，存放知识、会话、审计、元数据登记。"""

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings

platform_engine = create_engine(settings.platform_db_url, pool_pre_ping=True, pool_size=5)
PlatformSession = sessionmaker(bind=platform_engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass
