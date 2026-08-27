"""Monitoring rule governance and rule-driven alert lifecycle."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from statistics import mean, median, pstdev
from typing import Any

from sqlalchemy.orm import Session

from app.agent.ids import new_id
from app.db.models import (
    AlertEvidence,
    DomainEventOutbox,
    MonitoringAlert,
    MonitoringObservation,
    MonitoringRule,
)

RULE_KINDS = {"freshness", "ratio", "sum"}
OPERATORS = {"lt", "lte", "gt", "gte"}


@dataclass(frozen=True)
class ObservationResult:
    observation: MonitoringObservation
    alert: MonitoringAlert | None
    transition: str | None = None


def _stable_hash(parts: list[Any]) -> str:
    value = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def validate_rule_definition(query_plan: dict, condition: dict) -> None:
    kind = query_plan.get("kind")
    if kind not in RULE_KINDS:
        raise ValueError("queryPlan.kind 必须是 freshness、ratio 或 sum")
    for field in ("table", "timeField"):
        if not str(query_plan.get(field, "")).strip():
            raise ValueError(f"queryPlan.{field} 不能为空")
    if kind != "freshness" and not str(query_plan.get("entityField", "")).strip():
        raise ValueError(f"{kind} 计划缺少 entityField")
    if kind == "ratio":
        if not str(query_plan.get("numeratorField", "")).strip():
            raise ValueError("ratio 计划缺少 numeratorField")
        denominator = query_plan.get("denominatorFields")
        if not isinstance(denominator, list) or not denominator:
            raise ValueError("ratio 计划缺少 denominatorFields")
    if kind == "sum" and not str(query_plan.get("valueField", "")).strip():
        raise ValueError("sum 计划缺少 valueField")
    operator = condition.get("operator")
    if kind != "freshness" and operator not in OPERATORS:
        raise ValueError("condition.operator 无效")
    if (
        kind != "freshness"
        and condition.get("openThreshold") is None
        and not condition.get("baselineMethod")
    ):
        raise ValueError("condition.openThreshold 不能为空")
    pending = int(condition.get("pendingPeriods", 1))
    recovery = int(condition.get("recoveryPeriods", 1))
    if pending < 1 or recovery < 1:
        raise ValueError("pendingPeriods 和 recoveryPeriods 必须大于 0")


def create_rule(
    session: Session,
    *,
    name: str,
    code: str,
    source_id: int,
    template_code: str,
    metric_name: str,
    entity_type: str,
    grain: str,
    query_plan: dict,
    condition: dict,
    notification_policy: dict,
    created_by: str,
    quality_gate: dict | None = None,
    severity_policy: dict | None = None,
    schedule_seconds: int = 86400,
) -> MonitoringRule:
    validate_rule_definition(query_plan, condition)
    if not name.strip() or not code.strip() or not metric_name.strip():
        raise ValueError("规则名称、编码和指标名称不能为空")
    if schedule_seconds < 60:
        raise ValueError("scheduleSeconds 不能小于 60")
    latest = (
        session.query(MonitoringRule)
        .filter_by(code=code.strip(), source_id=source_id)
        .order_by(MonitoringRule.version.desc())
        .first()
    )
    rule = MonitoringRule(
        id=new_id("mrl"),
        code=code.strip(),
        version=(latest.version + 1) if latest else 1,
        template_code=template_code.strip(),
        source_id=source_id,
        name=name.strip(),
        metric_name=metric_name.strip(),
        entity_type=entity_type.strip() or "source",
        grain=grain,
        query_plan_json=query_plan,
        condition_json=condition,
        quality_gate_json=quality_gate or {},
        severity_policy_json=severity_policy or {},
        notification_policy_json=notification_policy or {},
        schedule_seconds=schedule_seconds,
        status="draft",
        created_by=created_by,
    )
    session.add(rule)
    session.flush()
    return rule


def publish_rule(
    session: Session,
    rule_id: str,
    *,
    published_by: str,
    now: datetime | None = None,
) -> MonitoringRule:
    rule = session.query(MonitoringRule).filter_by(id=rule_id).with_for_update().one()
    if rule.status == "published":
        return rule
    if rule.status != "draft":
        raise ValueError("只有草稿规则可以发布")
    validate_rule_definition(rule.query_plan_json or {}, rule.condition_json or {})
    # A code identifies one logical monitor. Publishing a new version must be
    # atomic with retiring the former active version; otherwise both versions
    # evaluate the same source and emit duplicate alerts.
    (
        session.query(MonitoringRule)
        .filter(
            MonitoringRule.code == rule.code,
            MonitoringRule.source_id == rule.source_id,
            MonitoringRule.id != rule.id,
            MonitoringRule.status == "published",
        )
        .update({MonitoringRule.status: "retired"}, synchronize_session="fetch")
    )
    rule.status = "published"
    rule.published_by = published_by
    rule.published_at = now or datetime.now()
    session.flush()
    return rule


def pause_rule(session: Session, rule_id: str) -> MonitoringRule:
    rule = session.query(MonitoringRule).filter_by(id=rule_id).with_for_update().one()
    if rule.status not in {"published", "paused"}:
        raise ValueError("只有已发布规则可以暂停")
    rule.status = "paused"
    session.flush()
    return rule


def resume_rule(
    session: Session,
    rule_id: str,
    *,
    resumed_by: str,
    now: datetime | None = None,
) -> MonitoringRule:
    rule = session.query(MonitoringRule).filter_by(id=rule_id).with_for_update().one()
    if rule.status == "published":
        return rule
    if rule.status != "paused":
        raise ValueError("只有已暂停规则可以恢复运行")
    validate_rule_definition(rule.query_plan_json or {}, rule.condition_json or {})
    (
        session.query(MonitoringRule)
        .filter(
            MonitoringRule.code == rule.code,
            MonitoringRule.source_id == rule.source_id,
            MonitoringRule.id != rule.id,
            MonitoringRule.status == "published",
        )
        .update({MonitoringRule.status: "retired"}, synchronize_session="fetch")
    )
    rule.status = "published"
    rule.published_by = resumed_by
    rule.published_at = now or datetime.now()
    rule.last_scheduled_at = None
    session.flush()
    return rule


def _compare(value: float, operator: str, threshold: float) -> bool:
    return {
        "lt": value < threshold,
        "lte": value <= threshold,
        "gt": value > threshold,
        "gte": value >= threshold,
    }[operator]


def _is_breached(value: float, condition: dict) -> bool:
    return _compare(value, condition["operator"], float(condition["openThreshold"]))


def _is_recovered(value: float, condition: dict) -> bool:
    operator = condition["operator"]
    close = float(condition.get("closeThreshold", condition["openThreshold"]))
    if operator in {"lt", "lte"}:
        return value >= close
    return value <= close


def _baseline_bounds(
    session: Session,
    rule: MonitoringRule,
    entity_id: str,
    logical_period: str,
    condition: dict,
) -> tuple[float | None, float | None, float | None]:
    """Calculate deterministic robust/control-chart bounds from prior observations."""
    method = str(condition.get("baselineMethod", ""))
    if method not in {"median_mad", "same_weekday", "same_shift", "shewhart"}:
        return None, None, None
    lookback = min(365, max(3, int(condition.get("baselineLookbackPeriods", 30))))
    rows = session.query(
        MonitoringObservation.logical_period,
        MonitoringObservation.current_value,
    ).filter_by(rule_id=rule.id, entity_id=str(entity_id)).filter(
        MonitoringObservation.quality_status.in_(("ok", "insufficient_baseline")),
        MonitoringObservation.current_value.is_not(None)
    ).order_by(MonitoringObservation.created_at.desc()).limit(lookback * 3).all()
    if method == "same_weekday":
        try:
            target = datetime.fromisoformat(logical_period[:10]).weekday()
            rows = [row for row in rows if datetime.fromisoformat(row[0][:10]).weekday() == target]
        except ValueError:
            rows = []
    elif method == "same_shift":
        try:
            target = int(logical_period[11:13]) // 8
            rows = [row for row in rows if len(row[0]) >= 13 and int(row[0][11:13]) // 8 == target]
        except (ValueError, IndexError):
            rows = []
    values = [float(row[1]) for row in rows[:lookback]]
    required = max(3, int(condition.get("minimumBaselinePeriods", 7)))
    if len(values) < required:
        return None, None, None
    width = float(condition.get("sigma", 3.0))
    if method == "shewhart":
        center = mean(values)
        spread = pstdev(values)
    else:
        center = median(values)
        spread = median([abs(value - center) for value in values]) * 1.4826
    if spread <= 0:
        return center, center, center
    return center, center - width * spread, center + width * spread


def _dynamic_state(value: float, lower: float, upper: float, condition: dict) -> tuple[bool, bool]:
    direction = str(condition.get("direction", "two_sided"))
    breached = (
        value < lower if direction == "lower" else
        value > upper if direction == "upper" else
        value < lower or value > upper
    )
    return breached, not breached


def _severity(value: float, condition: dict, policy: dict) -> str:
    configured = str(policy.get("default", "medium"))
    if configured not in {"low", "medium", "high", "critical"}:
        configured = "medium"
    critical = policy.get("criticalThreshold")
    high = policy.get("highThreshold")
    operator = condition.get("operator")
    if critical is not None and _compare(value, operator, float(critical)):
        return "critical"
    if high is not None and _compare(value, operator, float(high)):
        return "high"
    return configured


def _event(
    session: Session,
    alert: MonitoringAlert,
    observation: MonitoringObservation,
    event_type: str,
    now: datetime,
) -> None:
    key = f"{event_type}:{alert.id}:{observation.id}"
    if session.query(DomainEventOutbox).filter_by(idempotency_key=key).first():
        return
    session.add(
        DomainEventOutbox(
            id=new_id("evt"),
            event_type=event_type,
            aggregate_id=alert.id,
            idempotency_key=key,
            payload_json={
                "alertId": alert.id,
                "ruleId": alert.rule_id,
                "sourceId": alert.source_id,
                "severity": alert.severity,
                "status": alert.status,
                "observationId": observation.id,
                "occurredAt": now.isoformat(),
            },
        )
    )


def _attach_evidence(
    session: Session,
    alert: MonitoringAlert,
    observation: MonitoringObservation,
) -> None:
    existing = session.query(AlertEvidence).filter_by(
        alert_id=alert.id, observation_id=observation.id
    ).first()
    if existing:
        return
    session.add(
        AlertEvidence(
            id=new_id("aev"),
            alert_id=alert.id,
            observation_id=observation.id,
            snapshot_json={
                "logicalPeriod": observation.logical_period,
                "currentValue": _number(observation.current_value),
                "baselineValue": _number(observation.baseline_value),
                "sampleSize": observation.sample_size,
                "qualityStatus": observation.quality_status,
                "evaluationState": observation.evaluation_state,
            },
        )
    )


def _number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    return float(value)


def _prior_breach_count(session: Session, rule: MonitoringRule, entity_id: str) -> int:
    rows = (
        session.query(MonitoringObservation.evaluation_state)
        .filter_by(rule_id=rule.id, entity_id=entity_id)
        .order_by(MonitoringObservation.created_at.desc(), MonitoringObservation.id.desc())
        .limit(max(1, int((rule.condition_json or {}).get("pendingPeriods", 1))))
        .all()
    )
    count = 0
    for (state,) in rows:
        if state not in {"pending", "firing"}:
            break
        count += 1
    return count


def record_observation(
    session: Session,
    *,
    rule: MonitoringRule,
    logical_period: str,
    entity_id: str,
    entity_name: str = "",
    current_value: float | None,
    baseline_value: float | None = None,
    lower_bound: float | None = None,
    upper_bound: float | None = None,
    sample_size: int = 0,
    data_cutoff_at: datetime | None = None,
    quality_status: str = "ok",
    details: dict | None = None,
    now: datetime | None = None,
) -> ObservationResult:
    """Persist one idempotent observation and advance its correlated alert."""

    now = now or datetime.now()
    idempotency_key = _stable_hash(
        [rule.id, rule.version, entity_id, logical_period]
    )
    existing = session.query(MonitoringObservation).filter_by(
        idempotency_key=idempotency_key
    ).first()
    if existing:
        alert = session.query(MonitoringAlert).filter_by(
            rule_id=rule.id, entity_id=entity_id
        ).first()
        return ObservationResult(existing, alert)

    condition = rule.condition_json or {}
    quality_gate = rule.quality_gate_json or {}
    minimum_samples = int(
        quality_gate.get("minimumSamples", quality_gate.get("minimumSampleSize", 0))
    )
    if baseline_value is None and condition.get("baselineMethod") and current_value is not None:
        computed_baseline, computed_lower, computed_upper = _baseline_bounds(
            session, rule, str(entity_id), logical_period, condition
        )
        baseline_value = computed_baseline
        lower_bound = computed_lower
        upper_bound = computed_upper
    if quality_status != "ok" or current_value is None or sample_size < minimum_samples:
        state = "no_data"
        breached = False
        recovered = False
    else:
        value = float(current_value)
        if condition.get("baselineMethod"):
            if lower_bound is None or upper_bound is None:
                breached = recovered = False
                state = "no_data"
                quality_status = "insufficient_baseline"
            else:
                breached, recovered = _dynamic_state(
                    value, float(lower_bound), float(upper_bound), condition
                )
                state = "pending" if breached else "normal"
        else:
            breached = _is_breached(value, condition)
            recovered = _is_recovered(value, condition)
            state = "pending" if breached else "normal"

    observation = MonitoringObservation(
        id=new_id("mob"),
        rule_id=rule.id,
        rule_version=rule.version,
        source_id=rule.source_id,
        logical_period=logical_period,
        entity_type=rule.entity_type,
        entity_id=str(entity_id),
        entity_name=entity_name,
        current_value=current_value,
        baseline_value=baseline_value,
        lower_bound=lower_bound,
        upper_bound=upper_bound,
        sample_size=max(0, int(sample_size)),
        quality_status=quality_status,
        evaluation_state=state,
        data_cutoff_at=data_cutoff_at,
        details_json=details or {},
        idempotency_key=idempotency_key,
        created_at=now,
    )
    session.add(observation)
    session.flush()

    alert = session.query(MonitoringAlert).filter_by(
        rule_id=rule.id, entity_id=str(entity_id)
    ).with_for_update().first()
    transition = None

    if breached:
        pending_count = _prior_breach_count(session, rule, str(entity_id))
        required = int(condition.get("pendingPeriods", 1))
        if pending_count < required:
            observation.evaluation_state = "pending"
            return ObservationResult(observation, alert)
        observation.evaluation_state = "firing"
        severity = _severity(float(current_value), condition, rule.severity_policy_json or {})
        if alert is None:
            alert = MonitoringAlert(
                id=new_id("mal"),
                rule_id=rule.id,
                rule_version=rule.version,
                source_id=rule.source_id,
                metric_name=rule.metric_name,
                entity_type=rule.entity_type,
                entity_id=str(entity_id),
                entity_name=entity_name,
                title=f"{entity_name or entity_id} · {rule.name}",
                severity=severity,
                status="firing",
                correlation_key=_stable_hash(
                    [rule.source_id, rule.code, rule.version, rule.metric_name, rule.entity_type, entity_id]
                ),
                current_value=current_value,
                baseline_value=baseline_value,
                sample_size=sample_size,
                pending_count=pending_count,
                first_seen_at=now,
                last_seen_at=now,
                latest_observation_id=observation.id,
            )
            session.add(alert)
            session.flush()
            transition = "firing"
            _event(session, alert, observation, "monitoring.alert.firing", now)
        else:
            old_severity = alert.severity
            if alert.status == "resolved":
                alert.recurrence_count += 1
                transition = "firing"
                _event(session, alert, observation, "monitoring.alert.firing", now)
            elif old_severity != severity:
                transition = "severity_changed"
                _event(session, alert, observation, "monitoring.alert.severity_changed", now)
            alert.status = "firing"
            alert.resolved_at = None
            alert.recovery_count = 0
            alert.severity = severity
            alert.current_value = current_value
            alert.baseline_value = baseline_value
            alert.sample_size = sample_size
            alert.pending_count = pending_count
            alert.last_seen_at = now
            alert.latest_observation_id = observation.id
        _attach_evidence(session, alert, observation)
    elif alert is not None and alert.status != "resolved" and quality_status == "ok":
        if recovered:
            alert.recovery_count += 1
            required = int(condition.get("recoveryPeriods", 1))
            alert.status = "resolved" if alert.recovery_count >= required else "recovering"
            alert.current_value = current_value
            alert.baseline_value = baseline_value
            alert.sample_size = sample_size
            alert.last_seen_at = now
            alert.latest_observation_id = observation.id
            if alert.status == "resolved":
                alert.resolved_at = now
                transition = "resolved"
                _event(session, alert, observation, "monitoring.alert.resolved", now)
            else:
                transition = "recovering"
            _attach_evidence(session, alert, observation)
        else:
            alert.status = "firing"
            alert.recovery_count = 0

    session.flush()
    return ObservationResult(observation, alert, transition)


def list_alert_events(session: Session) -> list[DomainEventOutbox]:
    return (
        session.query(DomainEventOutbox)
        .filter(DomainEventOutbox.event_type.like("monitoring.alert.%"))
        .order_by(DomainEventOutbox.created_at, DomainEventOutbox.id)
        .all()
    )


def rule_dict(rule: MonitoringRule) -> dict:
    return {
        "id": rule.id,
        "code": rule.code,
        "version": rule.version,
        "templateCode": rule.template_code,
        "sourceId": rule.source_id,
        "name": rule.name,
        "metricName": rule.metric_name,
        "entityType": rule.entity_type,
        "grain": rule.grain,
        "queryPlan": rule.query_plan_json or {},
        "condition": rule.condition_json or {},
        "qualityGate": rule.quality_gate_json or {},
        "severityPolicy": rule.severity_policy_json or {},
        "notificationPolicy": rule.notification_policy_json or {},
        "scheduleSeconds": rule.schedule_seconds,
        "status": rule.status,
        "createdBy": rule.created_by,
        "publishedBy": rule.published_by,
        "publishedAt": rule.published_at.isoformat() if rule.published_at else None,
        "createdAt": rule.created_at.isoformat(),
    }


def alert_dict(alert: MonitoringAlert) -> dict:
    return {
        "id": alert.id,
        "ruleId": alert.rule_id,
        "ruleVersion": alert.rule_version,
        "sourceId": alert.source_id,
        "metricName": alert.metric_name,
        "entityType": alert.entity_type,
        "entityId": alert.entity_id,
        "entityName": alert.entity_name,
        "title": alert.title,
        "severity": alert.severity,
        "status": alert.status,
        "currentValue": _number(alert.current_value),
        "baselineValue": _number(alert.baseline_value),
        "sampleSize": alert.sample_size,
        "recurrenceCount": alert.recurrence_count,
        "firstSeenAt": alert.first_seen_at.isoformat(),
        "lastSeenAt": alert.last_seen_at.isoformat(),
        "resolvedAt": alert.resolved_at.isoformat() if alert.resolved_at else None,
    }
