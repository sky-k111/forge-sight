"""PostgreSQL Checkpointer 生命周期。SQLAlchemy URL 与 psycopg DSN 在此隔离。"""

from contextlib import AbstractContextManager

from langgraph.checkpoint.postgres import PostgresSaver

from app.config import settings

_context: AbstractContextManager | None = None
_saver: PostgresSaver | None = None


def _psycopg_dsn(url: str) -> str:
    return url.replace("postgresql+psycopg2://", "postgresql://").replace(
        "postgresql+psycopg://", "postgresql://"
    )


def initialize_checkpointer() -> PostgresSaver:
    global _context, _saver
    if _saver is not None:
        return _saver
    _context = PostgresSaver.from_conn_string(_psycopg_dsn(settings.platform_db_url))
    _saver = _context.__enter__()
    _saver.setup()
    return _saver


def get_checkpointer() -> PostgresSaver:
    if _saver is None:
        raise RuntimeError("LangGraph Checkpointer 尚未初始化")
    return _saver


def close_checkpointer() -> None:
    global _context, _saver
    if _context is not None:
        _context.__exit__(None, None, None)
    _context = None
    _saver = None

