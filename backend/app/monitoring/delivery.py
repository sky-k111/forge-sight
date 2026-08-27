"""Reliable inbox expansion and external delivery for monitoring alerts."""

from __future__ import annotations

from datetime import datetime, timedelta
from urllib.parse import urlparse

from sqlalchemy import and_, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.agent.ids import new_id
from app.db.models import (
    AlertDelivery,
    DingTalkBinding,
    DomainEventOutbox,
    FeishuBinding,
    Message,
    MessageRecipient,
    MonitoringAlert,
    MonitoringRule,
    PlatformUser,
)
from app.delivery.provider import ProviderFailure
from app.monitoring.provider import AlertProvider, OutgoingAlert
from app.reports.repository import resolve_audience_scope

EVENT_TYPES = (
    "monitoring.alert.firing",
    "monitoring.alert.severity_changed",
    "monitoring.alert.resolved",
)


def _claim_events(
    session: Session,
    worker_id: str,
    *,
    now: datetime,
    lease_seconds: int,
    limit: int = 1,
) -> list[DomainEventOutbox]:
    eligible = or_(
        and_(
            DomainEventOutbox.status.in_(("pending", "retry_wait")),
            or_(DomainEventOutbox.next_retry_at.is_(None), DomainEventOutbox.next_retry_at <= now),
        ),
        and_(
            DomainEventOutbox.status == "processing",
            DomainEventOutbox.lease_expires_at.is_not(None),
            DomainEventOutbox.lease_expires_at <= now,
        ),
    )
    query = session.query(DomainEventOutbox).filter(
        DomainEventOutbox.event_type.in_(EVENT_TYPES), eligible
    ).order_by(DomainEventOutbox.created_at, DomainEventOutbox.id).limit(limit)
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        query = query.with_for_update(skip_locked=True)
    rows = query.all()
    for row in rows:
        row.status = "processing"
        row.lease_owner = worker_id
        row.lease_expires_at = now + timedelta(seconds=max(10, lease_seconds))
        row.attempt_count += 1
    session.flush()
    return rows


def _alert_allowed(session: Session, user_id: str, alert: MonitoringAlert) -> bool:
    scope = resolve_audience_scope(
        session,
        "anomaly_monitoring",
        {"userIds": [user_id]},
    )
    if alert.source_id not in scope.data_source_ids:
        return False
    restricted = scope.dimensions.get(alert.entity_type)
    return not restricted or alert.entity_id in restricted


def _summary(alert: MonitoringAlert, event_type: str) -> str:
    state = "已自动恢复" if event_type.endswith("resolved") else "正在告警"
    value = "暂无" if alert.current_value is None else f"{float(alert.current_value):,.4g}"
    baseline = "暂无" if alert.baseline_value is None else f"{float(alert.baseline_value):,.4g}"
    return (
        f"状态：{state}\n"
        f"指标：{alert.metric_name}\n"
        f"对象：{alert.entity_name or alert.entity_id}\n"
        f"当前值：{value}；基线：{baseline}\n"
        f"严重度：{alert.severity}；数据源：{alert.source_id}"
    )


def _message_recipient(session: Session, message_id: str, user_id: str) -> None:
    if session.query(MessageRecipient).filter_by(message_id=message_id, user_id=user_id).first():
        return
    try:
        with session.begin_nested():
            session.add(
                MessageRecipient(
                    id=new_id("mrc"),
                    message_id=message_id,
                    user_id=user_id,
                    message_status_snapshot="published",
                )
            )
            session.flush()
    except IntegrityError:
        pass


def _external_address(session: Session, user_id: str, channel: str) -> str | None:
    if channel == "feishu":
        row = session.query(FeishuBinding).filter_by(user_id=user_id, bind_status="bound").first()
        return row.open_id if row else None
    if channel == "dingtalk":
        row = session.query(DingTalkBinding).filter_by(user_id=user_id, bind_status="bound").first()
        return row.union_id if row else None
    return None


def consume_alert_event(
    session: Session,
    event_id: str,
    worker_id: str,
    *,
    now: datetime | None = None,
) -> Message:
    current = now or datetime.now()
    event = session.query(DomainEventOutbox).filter_by(id=event_id).with_for_update().one()
    if event.status != "processing" or event.lease_owner != worker_id:
        raise ValueError("警报事件未被当前 Worker 认领")
    alert = session.get(MonitoringAlert, event.aggregate_id)
    if alert is None:
        raise ValueError("警报事件引用不存在")
    rule = session.get(MonitoringRule, alert.rule_id)
    if rule is None:
        raise ValueError("警报规则不存在")
    policy = rule.notification_policy_json or {}
    requested = {str(value) for value in policy.get("userIds", [])}
    candidate_query = session.query(PlatformUser).filter(PlatformUser.enabled.is_(True))
    if requested:
        candidate_query = candidate_query.filter(PlatformUser.id.in_(requested))
    recipients = [
        row
        for row in candidate_query.all()
        if _alert_allowed(session, row.id, alert)
    ]
    idempotency_key = f"inbox:{event.event_type}:{event.id}"
    message = session.query(Message).filter_by(idempotency_key=idempotency_key).first()
    if message is None:
        message = Message(
            id=new_id("msg"),
            message_type="monitoring_alert",
            source="monitoring",
            title=("[恢复] " if event.event_type.endswith("resolved") else "") + alert.title,
            summary=_summary(alert, event.event_type),
            related_type="monitoring_alert",
            related_id=alert.id,
            priority={"critical": "urgent", "high": "high"}.get(alert.severity, "normal"),
            status="published",
            idempotency_key=idempotency_key,
            valid_from=current,
            published_at=current,
        )
        session.add(message)
        session.flush()
    channels = [value for value in policy.get("channels", ["feishu", "dingtalk"]) if value in {"feishu", "dingtalk"}]
    for user in recipients:
        _message_recipient(session, message.id, user.id)
        for channel in channels:
            address = _external_address(session, user.id, channel)
            if not address:
                continue
            delivery_key = f"alert-delivery:{event.event_type}:{event.id}:{user.id}:{channel}"
            if session.query(AlertDelivery).filter_by(idempotency_key=delivery_key).first():
                continue
            session.add(
                AlertDelivery(
                    id=new_id("adl"),
                    alert_id=alert.id,
                    event_type=event.event_type,
                    channel=channel,
                    recipient_user_id=user.id,
                    recipient_address=address,
                    status="queued",
                    idempotency_key=delivery_key,
                    next_attempt_at=current,
                )
            )
    event.status = "completed"
    event.acknowledged_at = current
    event.lease_owner = None
    event.lease_expires_at = None
    event.error_summary = None
    session.flush()
    return message


def _fail_event(session: Session, event_id: str, worker_id: str, exc: Exception, now: datetime) -> None:
    event = session.query(DomainEventOutbox).filter_by(id=event_id).with_for_update().one()
    if event.status != "processing" or event.lease_owner != worker_id:
        return
    event.status = "dead_letter" if event.attempt_count >= 5 else "retry_wait"
    event.next_retry_at = now + timedelta(seconds=min(900, 30 * (2 ** max(0, event.attempt_count - 1))))
    event.error_summary = f"{type(exc).__name__}: {exc}"[:500]
    event.lease_owner = None
    event.lease_expires_at = None


def run_event_worker_once(
    session_factory,
    *,
    worker_id: str,
    now: datetime | None = None,
    lease_seconds: int = 60,
) -> bool:
    current = now or datetime.now()
    with session_factory() as session:
        rows = _claim_events(session, worker_id, now=current, lease_seconds=lease_seconds)
        session.commit()
        if not rows:
            return False
        event_id = rows[0].id
    with session_factory() as session:
        try:
            consume_alert_event(session, event_id, worker_id, now=current)
            session.commit()
        except Exception as exc:
            session.rollback()
            _fail_event(session, event_id, worker_id, exc, current)
            session.commit()
    return True


def claim_deliveries(
    session: Session,
    worker_id: str,
    channels: tuple[str, ...],
    *,
    now: datetime,
    lease_seconds: int = 90,
) -> list[AlertDelivery]:
    eligible = or_(
        and_(AlertDelivery.status.in_(("queued", "retry_wait")), AlertDelivery.next_attempt_at <= now),
        and_(AlertDelivery.status == "sending", AlertDelivery.lease_expires_at <= now),
    )
    query = session.query(AlertDelivery).filter(
        AlertDelivery.channel.in_(channels), eligible
    ).order_by(AlertDelivery.created_at, AlertDelivery.id).limit(1)
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        query = query.with_for_update(skip_locked=True)
    rows = query.all()
    for row in rows:
        row.status = "sending"
        row.lease_owner = worker_id
        row.lease_expires_at = now + timedelta(seconds=max(10, lease_seconds))
        row.attempt_count += 1
    session.flush()
    return rows


def _detail_url(public_app_url: str, alert_id: str) -> str:
    parsed = urlparse(public_app_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ProviderFailure("configuration", "PUBLIC_APP_URL 无效", retryable=False)
    return f"{public_app_url.rstrip('/')}/workspace/alerts/{alert_id}"


def run_delivery_worker_once(
    session_factory,
    *,
    worker_id: str,
    providers: dict[str, AlertProvider],
    public_app_url: str,
    now: datetime | None = None,
    lease_seconds: int = 90,
) -> bool:
    current = now or datetime.now()
    with session_factory() as session:
        rows = claim_deliveries(
            session,
            worker_id,
            tuple(providers),
            now=current,
            lease_seconds=lease_seconds,
        )
        session.commit()
        if not rows:
            return False
        delivery_id = rows[0].id
        channel = rows[0].channel
    with session_factory() as session:
        delivery = session.query(AlertDelivery).filter_by(id=delivery_id).with_for_update().one()
        alert = session.get(MonitoringAlert, delivery.alert_id)
        if alert is None or not delivery.recipient_address:
            delivery.status = "failed"
            delivery.error_summary = "警报或接收地址不存在"
            delivery.lease_owner = None
            delivery.lease_expires_at = None
            session.commit()
            return True
        outgoing = OutgoingAlert(
            channel=delivery.channel,
            recipient_id=delivery.recipient_address,
            title=alert.title,
            text=_summary(alert, delivery.event_type),
            detail_url=_detail_url(public_app_url, alert.id),
            idempotency_key=delivery.idempotency_key,
        )
        session.commit()
    try:
        result = providers[channel].send(outgoing)
        failure = None
    except ProviderFailure as exc:
        result = None
        failure = exc
    except Exception as exc:
        result = None
        failure = ProviderFailure("provider_error", type(exc).__name__, retryable=False, uncertain=True)
    with session_factory() as session:
        delivery = session.query(AlertDelivery).filter_by(id=delivery_id).with_for_update().one()
        if failure is None:
            delivery.status = "sent"
            delivery.provider_message_id = result.provider_message_id
            delivery.sent_at = current
            delivery.error_summary = None
        else:
            exhausted = not failure.retryable or delivery.attempt_count >= delivery.max_attempts
            delivery.status = "failed" if exhausted else "retry_wait"
            delivery.next_attempt_at = current + timedelta(seconds=min(900, 30 * (2 ** max(0, delivery.attempt_count - 1))))
            delivery.error_summary = failure.category
        delivery.lease_owner = None
        delivery.lease_expires_at = None
        delivery.updated_at = current
        session.commit()
    return True
