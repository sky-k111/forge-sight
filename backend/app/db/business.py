"""业务数据底座 forge_sight 的只读连接。

红线：此连接只用于 SELECT。任何写操作都说明实现有误。
"""

from sqlalchemy import create_engine, event

from app.config import settings

business_engine = create_engine(settings.business_db_url, pool_pre_ping=True, pool_size=5)


@event.listens_for(business_engine, "connect")
def _set_session_readonly(dbapi_connection, _connection_record):
    """会话级只读 + 语句超时，双保险（账号本身也应只有 SELECT 权限）。"""
    cursor = dbapi_connection.cursor()
    cursor.execute("SET SESSION TRANSACTION READ ONLY")
    cursor.execute(f"SET SESSION MAX_EXECUTION_TIME = {settings.sql_timeout_seconds * 1000}")
    cursor.close()
