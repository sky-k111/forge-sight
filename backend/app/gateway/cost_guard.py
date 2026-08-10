"""Database EXPLAIN cost gate for generated read-only SQL.

Only optimizer estimates are requested: MySQL ``EXPLAIN FORMAT=JSON`` and
PostgreSQL ``EXPLAIN (FORMAT JSON)``. ``ANALYZE`` is deliberately never used,
so the gate does not execute the candidate query.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from app.audit.service import write_audit
from app.config import settings
from app.db.models import DataSource
from app.gateway.adapters import get_engine


@dataclass(frozen=True)
class ExplainCheckResult:
    ok: bool
    allowed: bool
    dialect: str
    estimated_rows: int = 0
    estimated_cost: float | None = None
    full_scan_tables: list[str] = field(default_factory=list)
    reason: str = ""
    checks: list[str] = field(default_factory=list)
    retryable: bool = False
    error_kind: str = ""


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _mysql_summary(plan: Any) -> tuple[int, float | None, list[str]]:
    rows = 0
    costs: list[float] = []
    full_scans: set[str] = set()

    def visit(node: Any) -> None:
        nonlocal rows
        if isinstance(node, dict):
            table_name = str(node.get("table_name") or "")
            access_type = str(node.get("access_type") or "").upper()
            if table_name and access_type == "ALL":
                full_scans.add(table_name)
            for key in ("rows_examined_per_scan", "rows_produced_per_join"):
                number = _number(node.get(key))
                if number is not None:
                    rows = max(rows, int(number))
            cost_info = node.get("cost_info")
            if isinstance(cost_info, dict):
                number = _number(cost_info.get("query_cost"))
                if number is not None:
                    costs.append(number)
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(plan)
    return rows, max(costs) if costs else None, sorted(full_scans)


def _postgres_summary(plan: Any) -> tuple[int, float | None, list[str]]:
    root = plan
    if isinstance(root, list) and root:
        root = root[0]
    if isinstance(root, dict) and "Plan" in root:
        root = root["Plan"]
    rows = 0
    costs: list[float] = []
    full_scans: set[str] = set()

    def visit(node: Any) -> None:
        nonlocal rows
        if isinstance(node, dict):
            estimate = _number(node.get("Plan Rows"))
            if estimate is not None:
                rows = max(rows, int(estimate))
            cost = _number(node.get("Total Cost"))
            if cost is not None:
                costs.append(cost)
            if node.get("Node Type") == "Seq Scan" and node.get("Relation Name"):
                full_scans.add(str(node["Relation Name"]))
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(root)
    return rows, max(costs) if costs else None, sorted(full_scans)


def explain_readonly(sql: str, source: DataSource) -> ExplainCheckResult:
    """Estimate query work and reject plans above configured safety limits."""
    if not settings.sql_explain_enabled:
        return ExplainCheckResult(
            ok=True,
            allowed=True,
            dialect=source.type,
            reason="EXPLAIN 成本门控已由配置关闭",
            checks=["成本门控已跳过"],
        )
    statement = (
        f"EXPLAIN FORMAT=JSON {sql}"
        if source.type == "mysql"
        else f"EXPLAIN (FORMAT JSON) {sql}"
    )
    try:
        with get_engine(source).connect() as connection:
            raw = connection.execute(text(statement)).scalar()
        if isinstance(raw, str):
            raw = json.loads(raw)
        if source.type == "mysql":
            estimated_rows, estimated_cost, full_scans = _mysql_summary(raw)
        else:
            estimated_rows, estimated_cost, full_scans = _postgres_summary(raw)
    except Exception as exc:  # noqa: BLE001 - explain failure must become a safe gate result
        programming_error = isinstance(exc, ProgrammingError)
        if source.type == "mysql" and "FULL OUTER JOIN" in sql.upper():
            reason = "生成的 SQL 使用了 MySQL 不支持的 FULL OUTER JOIN"
            error_kind = "dialect_syntax"
        elif programming_error:
            reason = f"数据库无法解析生成的 SQL，可能存在与 {source.type.upper()} 方言不兼容的语法"
            error_kind = "dialect_syntax"
        else:
            reason = f"暂时无法取得数据库执行计划（{type(exc).__name__}）"
            error_kind = "explain_unavailable"
        return ExplainCheckResult(
            ok=False,
            allowed=False,
            dialect=source.type,
            reason=reason,
            retryable=programming_error,
            error_kind=error_kind,
        )

    row_limit = max(0, settings.sql_explain_max_estimated_rows)
    cost_limit = max(0.0, settings.sql_explain_max_cost)
    reasons = []
    if row_limit and estimated_rows > row_limit:
        reasons.append(f"预计扫描 {estimated_rows:,} 行，超过安全阈值 {row_limit:,} 行")
    if cost_limit and estimated_cost is not None and estimated_cost > cost_limit:
        reasons.append(f"优化器成本 {estimated_cost:,.2f}，超过安全阈值 {cost_limit:,.2f}")
    allowed = not reasons
    checks = [f"预计扫描行数：{estimated_rows:,}"]
    if estimated_cost is not None:
        checks.append(f"优化器成本：{estimated_cost:,.2f}")
    if full_scans:
        checks.append(f"全表扫描：{', '.join(full_scans)}")
    checks.append("成本门控通过" if allowed else "成本门控已阻断")
    result = ExplainCheckResult(
        ok=True,
        allowed=allowed,
        dialect=source.type,
        estimated_rows=estimated_rows,
        estimated_cost=estimated_cost,
        full_scan_tables=full_scans,
        reason="；".join(reasons),
        checks=checks,
    )
    if not allowed:
        write_audit(
            action="Agent SQL 成本门控",
            resource=",".join(full_scans) or "查询计划",
            result="已拦截",
            detail=result.reason,
            user_name="Agent",
        )
    return result
