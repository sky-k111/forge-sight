"""数据源适配层：按数据源类型构建只读连接。

借鉴 datafoundry data-gateway 的适配器思路：
- 每种类型一个适配实现（当前 mysql / postgresql）
- 连接级只读与超时强制设置（PG: READ ONLY 事务 + statement_timeout；MySQL: 会话只读 + MAX_EXECUTION_TIME）
- 引擎按数据源缓存，避免每次重建连接池
"""

from urllib.parse import quote_plus

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine

from app.config import settings
from app.db.models import DataSource

SUPPORTED_TYPES = {
    "mysql": {"label": "MySQL", "default_port": 3306},
    "postgresql": {"label": "PostgreSQL", "default_port": 5432},
}

_engines: dict[int, Engine] = {}


def build_url(source: DataSource) -> str:
    password = quote_plus(source.password)
    if source.type == "mysql":
        return (
            f"mysql+pymysql://{source.username}:{password}"
            f"@{source.host}:{source.port}/{source.db_name}?charset=utf8mb4"
        )
    if source.type == "postgresql":
        return f"postgresql+psycopg2://{source.username}:{password}@{source.host}:{source.port}/{source.db_name}"
    raise ValueError(f"不支持的数据源类型：{source.type}（当前支持 mysql / postgresql）")


def _apply_readonly(engine: Engine, source_type: str) -> None:
    @event.listens_for(engine, "connect")
    def _set_readonly(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        if source_type == "mysql":
            cursor.execute("SET SESSION TRANSACTION READ ONLY")
            cursor.execute(f"SET SESSION MAX_EXECUTION_TIME = {settings.sql_timeout_seconds * 1000}")
        else:  # postgresql
            cursor.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
            cursor.execute(f"SET statement_timeout = {settings.sql_timeout_seconds * 1000}")
        cursor.close()


def get_engine(source: DataSource) -> Engine:
    if source.id not in _engines:
        engine = create_engine(build_url(source), pool_pre_ping=True, pool_size=5)
        _apply_readonly(engine, source.type)
        _engines[source.id] = engine
    return _engines[source.id]


def drop_engine(source_id: int) -> None:
    engine = _engines.pop(source_id, None)
    if engine is not None:
        engine.dispose()


def test_connection(source: DataSource) -> tuple[bool, str]:
    """测试连接可用性与只读性。返回 (是否成功, 说明)。"""
    try:
        engine = get_engine(source)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True, "连接正常"
    except Exception as exc:  # noqa: BLE001 - 连接失败原因需返回给管理员
        drop_engine(source.id)
        return False, f"{type(exc).__name__}: {exc}"[:300]
