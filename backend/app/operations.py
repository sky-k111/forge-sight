"""Sanitized operational snapshot for release monitoring and alerting."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from math import ceil

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import (
    AnalysisRun,
    DomainEventOutbox,
    EmailVerificationEvent,
    ReportDelivery,
    ReportFile,
    ReportSubscription,
)


def _status_counts(session: Session, column) -> dict[str, int]:
    return {str(status): int(count) for status, count in session.query(column, func.count()).group_by(column)}


def _alert(code: str, severity: str, count: int, resource_type: str) -> dict:
    return {
        "code": code,
        "severity": severity,
        "count": count,
        "resourceType": resource_type,
    }


def operational_snapshot(session: Session, *, now: datetime | None = None) -> dict:
    current = now or datetime.now()
    outbox = _status_counts(session, DomainEventOutbox.status)
    files = _status_counts(session, ReportFile.status)
    email = _status_counts(session, ReportDelivery.status)
    runs = _status_counts(session, AnalysisRun.status)
    due_subscriptions = session.query(ReportSubscription).filter(
        ReportSubscription.enabled.is_(True),
        ReportSubscription.next_run_at.is_not(None),
        ReportSubscription.next_run_at <= current,
    ).count()
    verification_events = session.query(EmailVerificationEvent.event_type).filter(
        EmailVerificationEvent.created_at >= current - timedelta(hours=1)
    ).all()
    verification = dict(Counter(str(row[0]) for row in verification_events))
    attempts = session.query(ReportDelivery.last_duration_ms).filter(
        ReportDelivery.last_duration_ms.is_not(None)
    ).all()
    durations = sorted(int(row[0]) for row in attempts)
    p95_index = max(0, ceil(len(durations) * 0.95) - 1) if durations else 0

    alerts: list[dict] = []
    if outbox.get("dead_letter", 0):
        alerts.append(_alert("OUTBOX_DEAD_LETTER", "critical", outbox["dead_letter"], "outbox"))
    if files.get("dead_letter", 0):
        alerts.append(_alert("FILE_DEAD_LETTER", "critical", files["dead_letter"], "report_file"))
    if email.get("failed", 0):
        alerts.append(_alert("EMAIL_FINAL_FAILURE", "critical", email["failed"], "delivery"))
    if due_subscriptions:
        alerts.append(_alert("SCHEDULER_LAG", "warning", due_subscriptions, "subscription"))
    queue_backlog = sum(outbox.get(key, 0) for key in ("pending", "retry_wait", "processing"))
    if queue_backlog >= settings.operations_queue_backlog_warning:
        alerts.append(_alert("OUTBOX_BACKLOG", "warning", queue_backlog, "outbox"))

    return {
        "generatedAt": current.isoformat(),
        "switches": {
            "agentWorker": settings.agent_worker_enabled,
            "reportScheduler": settings.report_scheduler_enabled,
            "mailWorker": settings.mail_worker_enabled,
            "emailGate": settings.auth_email_gate_enabled,
        },
        "scheduler": {"dueSubscriptions": due_subscriptions},
        "queues": {"runs": runs, "outbox": outbox, "files": files, "email": email},
        "delivery": {
            "durationP95Ms": durations[p95_index] if durations else None,
            "sampleCount": len(durations),
        },
        "verification": {"lastHour": verification},
        "alerts": alerts,
    }
