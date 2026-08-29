"""Durable monitoring scheduler and synchronous worker entrypoint."""

from __future__ import annotations

import argparse
import re
import socket
import time
from datetime import date, datetime, time as clock_time, timedelta
from typing import Any

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.agent.ids import new_id
from app.db.models import (
    DataSource,
    DataTable,
    MonitoringCursor,
    MonitoringObservation,
    MonitoringRule,
    MonitoringTask,
    TableField,
)
from app.db.platform import PlatformSession
from app.gateway.executor import execute_readonly
from app.gateway.adapters import get_adapter
from app.gateway.connectors.base import PhysicalTableRef
from app.monitoring.service import record_observation

IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _identifier(value: Any, label: str) -> str:
    text = str(value or "")
    if not IDENTIFIER.fullmatch(text):
        raise ValueError(f"{label} 不是安全标识符")
    return text


def _quote(value: str, source_type: str) -> str:
    checked = _identifier(value, "字段或表名")
    return get_adapter(source_type).quote_identifier(checked)


def _validate_registered_plan(session: Session, rule: MonitoringRule) -> tuple[DataSource, dict]:
    source = session.get(DataSource, rule.source_id)
    if source is None:
        raise ValueError("规则数据源不存在")
    plan = rule.query_plan_json or {}
    table = _identifier(plan.get("table"), "queryPlan.table")
    schema_name = str(plan.get("schemaName") or "").strip()
    query = session.query(DataTable).filter_by(source_id=source.id, name=table, registered=True)
    if schema_name:
        query = query.filter_by(schema_name=schema_name)
    registered_rows = query.all()
    if len(registered_rows) > 1:
        raise PermissionError("监控规则引用了同名表，请在 queryPlan 中指定 schemaName")
    registered = registered_rows[0] if registered_rows else None
    if registered is None:
        raise PermissionError("监控规则引用了未登记表")
    required = {
        str(plan.get("timeField", "")),
        str(plan.get("entityField", "")),
        str(plan.get("numeratorField", "")),
        str(plan.get("valueField", "")),
        *[str(item) for item in plan.get("denominatorFields", [])],
        *[str(item.get("field", "")) for item in plan.get("filters", []) if isinstance(item, dict)],
    } - {""}
    for field in required:
        _identifier(field, "queryPlan 字段")
    registered_fields = {
        row[0]
        for row in session.query(TableField.name).filter_by(
            source_id=source.id, schema_name=registered.schema_name, table_name=table
        ).all()
    }
    missing = required - registered_fields
    if missing:
        raise PermissionError(f"监控规则引用了未登记字段：{sorted(missing)}")
    plan = {**plan, "schemaName": registered.schema_name}
    return source, plan


def _literal(value: Any) -> str:
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def compile_query(
    session: Session,
    rule: MonitoringRule,
    *,
    window_start: datetime | None,
    window_end: datetime | None,
) -> tuple[DataSource, str]:
    """Compile a bounded structured plan; arbitrary SQL is never accepted."""

    source, plan = _validate_registered_plan(session, rule)
    quote = lambda value: _quote(value, source.type)
    schema_name = str(plan.get("schemaName") or "").strip()
    table = get_adapter(source.type).qualify_table(
        PhysicalTableRef(schema_name, _identifier(plan["table"], "queryPlan.table"))
    )
    time_field = quote(plan["timeField"])
    kind = plan["kind"]
    if kind == "freshness":
        return source, f"SELECT MAX({time_field}) AS data_cutoff_at FROM {table}"

    entity_field = quote(plan.get("entityField") or plan["timeField"])
    where: list[str] = []
    if window_start:
        where.append(f"{time_field} >= {_literal(window_start.isoformat(sep=' '))}")
    if window_end:
        where.append(f"{time_field} < {_literal(window_end.isoformat(sep=' '))}")
    for item in plan.get("filters", []):
        if not isinstance(item, dict) or item.get("operator", "eq") != "eq":
            raise ValueError("queryPlan.filters 第一版只支持 eq")
        where.append(f"{quote(item['field'])} = {_literal(item.get('value'))}")
    predicate = f" WHERE {' AND '.join(where)}" if where else ""
    if kind == "ratio":
        numerator = quote(plan["numeratorField"])
        denominator = " + ".join(f"COALESCE(SUM({quote(field)}), 0)" for field in plan["denominatorFields"])
        value_expr = f"COALESCE(SUM({numerator}), 0) / NULLIF(({denominator}), 0)"
    elif kind == "sum":
        value_expr = f"COALESCE(SUM({quote(plan['valueField'])}), 0)"
    else:
        raise ValueError("不支持的监控查询计划")
    return source, (
        f"SELECT {entity_field} AS entity_id, {value_expr} AS current_value, "
        f"COUNT(*) AS sample_size, MAX({time_field}) AS data_cutoff_at "
        f"FROM {table}{predicate} GROUP BY {entity_field} ORDER BY {entity_field}"
    )


def enqueue_rule_task(
    session: Session,
    rule: MonitoringRule,
    *,
    logical_period: str,
    window_start: datetime | None,
    window_end: datetime | None,
    now: datetime | None = None,
) -> MonitoringTask:
    key = f"{rule.id}:v{rule.version}:{logical_period}"
    existing = session.query(MonitoringTask).filter_by(idempotency_key=key).first()
    if existing:
        return existing
    row = MonitoringTask(
        id=new_id("mtk"),
        rule_id=rule.id,
        rule_version=rule.version,
        logical_period=logical_period,
        window_start=window_start,
        window_end=window_end,
        status="queued",
        idempotency_key=key,
        next_attempt_at=now or datetime.now(),
    )
    session.add(row)
    session.flush()
    return row


def completed_window(rule: MonitoringRule, current: datetime) -> tuple[str, datetime, datetime]:
    """Return the last complete logical period to prevent partial-period alarms."""
    if rule.grain == "hour":
        end = current.replace(minute=0, second=0, microsecond=0)
        start = end - timedelta(hours=1)
        return start.strftime("%Y-%m-%dT%H"), start, end
    if rule.grain == "shift":
        shift_hour = (current.hour // 8) * 8
        end = current.replace(hour=shift_hour, minute=0, second=0, microsecond=0)
        start = end - timedelta(hours=8)
        return start.strftime("%Y-%m-%dT%H"), start, end
    end = datetime.combine(current.date(), clock_time.min)
    start = end - timedelta(days=1)
    return start.date().isoformat(), start, end


def _period_step(rule: MonitoringRule) -> timedelta:
    if rule.grain == "hour":
        return timedelta(hours=1)
    if rule.grain == "shift":
        return timedelta(hours=8)
    return timedelta(days=1)


def _logical_period(rule: MonitoringRule, start: datetime) -> str:
    if rule.grain in {"hour", "shift"}:
        return start.strftime("%Y-%m-%dT%H")
    return start.date().isoformat()


def completed_windows_since(
    rule: MonitoringRule,
    current: datetime,
    watermark_at: datetime | None,
) -> list[tuple[str, datetime, datetime]]:
    """Return every missing complete period, bounded for safe reconciliation."""

    latest_period, latest_start, latest_end = completed_window(rule, current)
    if watermark_at is None:
        return [(latest_period, latest_start, latest_end)]
    if watermark_at >= latest_end:
        return []
    step = _period_step(rule)
    start = watermark_at
    default_maximum = {"hour": 168, "shift": 90, "day": 31}.get(rule.grain, 31)
    maximum = min(
        366,
        max(
            1,
            int(
                (rule.quality_gate_json or {}).get(
                    "maxBackfillPeriods", default_maximum
                )
            ),
        ),
    )
    windows: list[tuple[str, datetime, datetime]] = []
    while start < latest_end and len(windows) < maximum:
        end = min(start + step, latest_end)
        windows.append((_logical_period(rule, start), start, end))
        start = end
    return windows


def enqueue_due_rules(session: Session, *, now: datetime | None = None) -> int:
    current = now or datetime.now()
    created = 0
    rules = session.query(MonitoringRule).filter_by(status="published").all()
    for rule in rules:
        if rule.last_scheduled_at and (current - rule.last_scheduled_at).total_seconds() < rule.schedule_seconds:
            continue
        cursor = session.query(MonitoringCursor).filter_by(rule_id=rule.id).first()
        for logical_period, start, end in completed_windows_since(
            rule,
            current,
            cursor.watermark_at if cursor else None,
        ):
            key = f"{rule.id}:v{rule.version}:{logical_period}"
            if session.query(MonitoringTask.id).filter_by(idempotency_key=key).first():
                continue
            enqueue_rule_task(
                session,
                rule,
                logical_period=logical_period,
                window_start=start,
                window_end=end,
                now=current,
            )
            created += 1
        rule.last_scheduled_at = current
    session.flush()
    return created


def claim_tasks(
    session: Session,
    worker_id: str,
    *,
    now: datetime | None = None,
    lease_seconds: int = 120,
    limit: int = 1,
) -> list[MonitoringTask]:
    current = now or datetime.now()
    eligible = or_(
        and_(
            MonitoringTask.status.in_(("queued", "retry_wait")),
            MonitoringTask.next_attempt_at <= current,
        ),
        and_(
            MonitoringTask.status == "running",
            MonitoringTask.lease_expires_at.is_not(None),
            MonitoringTask.lease_expires_at <= current,
        ),
    )
    query = session.query(MonitoringTask).filter(eligible).order_by(
        MonitoringTask.next_attempt_at, MonitoringTask.created_at, MonitoringTask.id
    ).limit(limit)
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        query = query.with_for_update(skip_locked=True)
    rows = query.all()
    for row in rows:
        row.status = "running"
        row.lease_owner = worker_id
        row.lease_expires_at = current + timedelta(seconds=max(10, lease_seconds))
        row.started_at = row.started_at or current
        row.attempt_count += 1
    session.flush()
    return rows


def _as_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, clock_time.min)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)


def execute_task_sync(
    session: Session,
    task: MonitoringTask,
    *,
    worker_id: str,
    now: datetime | None = None,
) -> dict[str, int]:
    current = now or datetime.now()
    if task.status != "running" or task.lease_owner != worker_id:
        raise ValueError("监控任务未被当前 Worker 认领")
    rule = session.get(MonitoringRule, task.rule_id)
    if rule is None or rule.version != task.rule_version or rule.status != "published":
        raise ValueError("监控任务引用的规则不可执行")
    source, sql = compile_query(
        session, rule, window_start=task.window_start, window_end=task.window_end
    )
    result = execute_readonly(sql, action=f"监控规则执行：{rule.name}", user_name="monitoring-worker", source=source)
    if not result.ok:
        raise RuntimeError(result.error or "监控查询失败")
    columns = {name: index for index, name in enumerate(result.columns)}
    processed = 0
    kind = (rule.query_plan_json or {}).get("kind")
    if kind == "freshness":
        cutoff = _as_datetime(result.rows[0][columns["data_cutoff_at"]]) if result.rows else None
        condition = rule.condition_json or {}
        # 空表本身就是最严重的断流信号。用确定性的越界值进入常规
        # 告警生命周期，同时保留 emptySource 证据，避免悄悄跳过告警。
        lag = (
            (current - cutoff).total_seconds()
            if cutoff
            else float(condition.get("openThreshold", 0)) + 1.0
        )
        record_observation(
            session,
            rule=rule,
            logical_period=task.logical_period,
            entity_id=f"source:{rule.source_id}",
            entity_name=source.name,
            current_value=lag,
            sample_size=1 if cutoff else 0,
            data_cutoff_at=cutoff,
            quality_status="ok",
            details={"query": sql, "lagSeconds": lag, "emptySource": cutoff is None},
            now=current,
        )
        processed = 1
    else:
        observed_entities: set[str] = set()
        for row in result.rows:
            entity_id = str(row[columns["entity_id"]])
            observed_entities.add(entity_id)
            record_observation(
                session,
                rule=rule,
                logical_period=task.logical_period,
                entity_id=entity_id,
                entity_name=entity_id,
                current_value=float(row[columns["current_value"]]) if row[columns["current_value"]] is not None else None,
                sample_size=int(row[columns["sample_size"]] or 0),
                data_cutoff_at=_as_datetime(row[columns["data_cutoff_at"]]),
                quality_status="ok" if row[columns["current_value"]] is not None else "no_data",
                details={"query": sql},
                now=current,
            )
            processed += 1
        # SQL GROUP BY has no row for an entity that disappeared entirely. Keep
        # that absence visible without turning missing data into a business
        # threshold alert. Previously seen entities receive their own no-data
        # observation; a never-seen/empty source receives one source sentinel.
        known_entities = {
            str(row[0])
            for row in session.query(MonitoringObservation.entity_id)
            .filter(
                MonitoringObservation.rule_id == rule.id,
                ~MonitoringObservation.entity_id.like("source:%"),
            )
            .distinct()
            .all()
        }
        missing_entities = sorted(known_entities - observed_entities)
        if not result.rows and not missing_entities:
            missing_entities = [f"source:{rule.source_id}"]
        for entity_id in missing_entities:
            record_observation(
                session,
                rule=rule,
                logical_period=task.logical_period,
                entity_id=entity_id,
                entity_name=source.name if entity_id.startswith("source:") else entity_id,
                current_value=None,
                sample_size=0,
                data_cutoff_at=None,
                quality_status="no_data",
                details={
                    "query": sql,
                    "emptyResult": not bool(result.rows),
                    "missingEntity": not entity_id.startswith("source:"),
                },
                now=current,
            )
            processed += 1
    task.status = "succeeded"
    task.result_json = {"processedObservations": processed, "rowCount": result.row_count}
    task.error_summary = None
    task.lease_owner = None
    task.lease_expires_at = None
    task.finished_at = current
    cursor = session.query(MonitoringCursor).filter_by(rule_id=rule.id).first()
    if cursor is None:
        cursor = MonitoringCursor(
            id=new_id("mcu"),
            rule_id=rule.id,
            source_id=rule.source_id,
        )
        session.add(cursor)
    next_watermark = task.window_end or current
    # Multiple workers may finish backfill tasks out of order. A cursor is a
    # high-water mark and must never move backwards.
    if cursor.watermark_at is None or next_watermark > cursor.watermark_at:
        cursor.logical_period = task.logical_period
        cursor.watermark_at = next_watermark
        cursor.task_id = task.id
        cursor.updated_at = current
    session.flush()
    return {"processed": processed}


def _fail_task(session: Session, task_id: str, worker_id: str, exc: Exception, now: datetime) -> None:
    task = session.query(MonitoringTask).filter_by(id=task_id).with_for_update().one()
    if task.status != "running" or task.lease_owner != worker_id:
        return
    exhausted = task.attempt_count >= task.max_attempts
    task.status = "dead_letter" if exhausted else "retry_wait"
    task.next_attempt_at = now + timedelta(seconds=min(900, 30 * (2 ** max(0, task.attempt_count - 1))))
    task.error_summary = f"{type(exc).__name__}: {exc}"[:500]
    task.lease_owner = None
    task.lease_expires_at = None
    task.finished_at = now if exhausted else None


def run_worker_once(
    session_factory=PlatformSession,
    *,
    worker_id: str,
    now: datetime | None = None,
    lease_seconds: int = 120,
) -> bool:
    current = now or datetime.now()
    with session_factory() as session:
        enqueue_due_rules(session, now=current)
        rows = claim_tasks(session, worker_id, now=current, lease_seconds=lease_seconds)
        session.commit()
        if not rows:
            return False
        task_id = rows[0].id
    with session_factory() as session:
        task = session.get(MonitoringTask, task_id)
        try:
            execute_task_sync(session, task, worker_id=worker_id, now=current)
            session.commit()
        except Exception as exc:  # durable failure boundary
            session.rollback()
            _fail_task(session, task_id, worker_id, exc, current)
            session.commit()
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="ForgeSight monitoring worker")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    args = parser.parse_args()
    worker_id = f"monitoring:{socket.gethostname()}"
    while True:
        worked = run_worker_once(worker_id=worker_id)
        if args.once:
            return
        if not worked:
            time.sleep(max(0.2, args.poll_seconds))


if __name__ == "__main__":
    main()
