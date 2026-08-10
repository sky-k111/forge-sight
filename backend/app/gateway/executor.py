"""受控执行器：校验 → 执行 → 摘要 → 审计。

数据源的一切查询只允许从这里出去。白名单 = 平台库中「已登记」的表，
未经管理员确认的新表对问析不可见（安全闸门）。
"""

import time
from dataclasses import dataclass, field

from sqlalchemy import text

from app.audit.service import write_audit
from app.config import settings
from app.db.models import DataSource, DataTable
from app.db.platform import PlatformSession
from app.gateway.adapters import get_engine
from app.gateway.sql_validator import validate_sql


@dataclass
class ExecutionResult:
    ok: bool
    columns: list[str] = field(default_factory=list)
    rows: list[list] = field(default_factory=list)
    row_count: int = 0
    duration_ms: int = 0
    truncated: bool = False
    tables: list[str] = field(default_factory=list)
    checks: list[str] = field(default_factory=list)
    error: str = ""


def get_default_source() -> DataSource:
    with PlatformSession() as session:
        source = session.query(DataSource).filter_by(is_default=True).first()
    if source is None:
        raise RuntimeError("未配置默认数据源")
    return source


def fetch_registered_tables(source_id: int) -> set[str]:
    """白名单：该数据源下已登记的表。"""
    with PlatformSession() as session:
        rows = session.query(DataTable.name).filter_by(source_id=source_id, registered=True).all()
    return {row[0] for row in rows}


def execute_readonly(
    sql: str,
    action: str = "执行查询",
    user_name: str = "演示用户",
    source: DataSource | None = None,
) -> ExecutionResult:
    """校验并执行只读 SQL，全程写审计。source 为空时使用默认数据源。"""
    if source is None:
        source = get_default_source()

    check = validate_sql(sql, fetch_registered_tables(source.id))
    if not check.ok:
        write_audit(
            action=action,
            resource=",".join(check.tables) or "未知表",
            result="已拦截",
            detail=check.reason,
            user_name=user_name,
        )
        return ExecutionResult(ok=False, tables=check.tables, error=check.reason)

    started = time.perf_counter()
    try:
        engine = get_engine(source)
        with engine.connect() as conn:
            cursor = conn.execute(text(sql))
            columns = list(cursor.keys())
            raw = cursor.fetchmany(settings.sql_max_rows + 1)
    except Exception as exc:  # noqa: BLE001 - 执行失败需转化为可理解的错误返回
        duration_ms = int((time.perf_counter() - started) * 1000)
        write_audit(action=action, resource=",".join(check.tables), result="已记录",
                    detail=f"执行失败：{type(exc).__name__}", user_name=user_name)
        return ExecutionResult(ok=False, tables=check.tables, duration_ms=duration_ms,
                               error=f"执行失败：{exc}")

    duration_ms = int((time.perf_counter() - started) * 1000)
    truncated = len(raw) > settings.sql_max_rows
    rows = [list(row) for row in raw[: settings.sql_max_rows]]

    write_audit(
        action=action,
        resource=",".join(check.tables),
        result="允许",
        detail=f"返回 {len(rows)} 行，耗时 {duration_ms}ms",
        user_name=user_name,
    )
    return ExecutionResult(
        ok=True,
        columns=columns,
        rows=rows,
        row_count=len(rows),
        duration_ms=duration_ms,
        truncated=truncated,
        tables=check.tables,
        checks=check.checks,
    )
