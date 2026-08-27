"""Governed monitoring rule and read-only alert APIs."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import NoResultFound

from app.audit.service import write_audit
from app.auth.deps import current_user_display, require_user
from app.db.models import (
    AlertDelivery,
    AlertEvidence,
    DataSource,
    MonitoringAlert,
    MonitoringObservation,
    MonitoringRule,
    MonitoringRuleTemplate,
    MonitoringTask,
)
from app.db.platform import PlatformSession
from app.monitoring import service, worker
from app.monitoring.contracts import MonitoringRuleCreate
from app.reports.repository import resolve_audience_scope

router = APIRouter(prefix="/api/v1/monitoring", tags=["monitoring"])


def _require_admin(user: dict) -> None:
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="只有管理员可以治理监控规则")


def _allowed_sources(session, user: dict) -> set[int]:
    if user.get("role") == "admin":
        return {row[0] for row in session.query(DataSource.id).all()}
    scope = resolve_audience_scope(
        session,
        "anomaly_monitoring",
        {"userIds": [user["id"]]},
    )
    return {int(source) for source in scope.data_source_ids}


def _alert_in_dimension_scope(session, user: dict, alert: MonitoringAlert) -> bool:
    if user.get("role") == "admin":
        return True
    scope = resolve_audience_scope(
        session,
        "anomaly_monitoring",
        {"userIds": [user["id"]]},
    )
    restricted = scope.dimensions.get(alert.entity_type)
    return not restricted or alert.entity_id in restricted


def _audit(user: dict, *, action: str, resource: str, detail: str = "") -> None:
    user_name, role = current_user_display(user)
    write_audit(
        action=action,
        resource=resource,
        result="允许",
        detail=detail,
        user_name=user_name,
        role=role,
    )


def _domain_error(exc: Exception):
    if isinstance(exc, NoResultFound):
        raise HTTPException(status_code=404, detail="监控资源不存在") from exc
    if isinstance(exc, PermissionError):
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    raise exc


@router.get("/rule-templates")
def list_rule_templates(user: dict = Depends(require_user)):
    _require_admin(user)
    with PlatformSession() as session:
        rows = session.query(MonitoringRuleTemplate).filter_by(status="published").order_by(
            MonitoringRuleTemplate.topic, MonitoringRuleTemplate.name
        ).all()
        return {
            "items": [
                {
                    "id": row.id,
                    "code": row.code,
                    "version": row.version,
                    "name": row.name,
                    "topic": row.topic,
                    "description": row.description,
                    "defaultCondition": row.default_condition_json or {},
                    "defaultQualityGate": row.default_quality_gate_json or {},
                }
                for row in rows
            ]
        }


@router.get("/rules")
def list_rules(user: dict = Depends(require_user)):
    _require_admin(user)
    with PlatformSession() as session:
        rows = session.query(MonitoringRule).order_by(
            MonitoringRule.created_at.desc(), MonitoringRule.id.desc()
        ).all()
        return {"items": [service.rule_dict(row) for row in rows]}


@router.get("/rules/{rule_id}")
def get_rule(rule_id: str, user: dict = Depends(require_user)):
    _require_admin(user)
    with PlatformSession() as session:
        row = session.get(MonitoringRule, rule_id)
        if row is None:
            raise HTTPException(status_code=404, detail="监控规则不存在")
        return service.rule_dict(row)


@router.post("/rules", status_code=status.HTTP_201_CREATED)
def create_rule(payload: MonitoringRuleCreate, user: dict = Depends(require_user)):
    _require_admin(user)
    try:
        with PlatformSession() as session:
            if session.get(DataSource, payload.sourceId) is None:
                raise ValueError("数据源不存在")
            row = service.create_rule(
                session,
                name=payload.name,
                code=payload.code,
                source_id=payload.sourceId,
                template_code=payload.templateCode,
                metric_name=payload.metricName,
                entity_type=payload.entityType,
                grain=payload.grain,
                query_plan=payload.queryPlan,
                condition=payload.condition,
                quality_gate=payload.qualityGate,
                severity_policy=payload.severityPolicy,
                notification_policy=payload.notificationPolicy,
                schedule_seconds=payload.scheduleSeconds,
                created_by=user["id"],
            )
            result = service.rule_dict(row)
            session.commit()
        _audit(user, action="创建监控规则", resource=f"monitoring-rule:{result['id']}")
        return result
    except Exception as exc:
        _domain_error(exc)


@router.post("/rules/{rule_id}/validate")
def validate_rule(rule_id: str, user: dict = Depends(require_user)):
    _require_admin(user)
    try:
        with PlatformSession() as session:
            rule = session.query(MonitoringRule).filter_by(id=rule_id).one()
            source, sql = worker.compile_query(
                session, rule, window_start=None, window_end=None
            )
            return {"valid": True, "sourceId": source.id, "compiledSql": sql}
    except Exception as exc:
        _domain_error(exc)


@router.post("/rules/{rule_id}/publish")
def publish_rule(rule_id: str, user: dict = Depends(require_user)):
    _require_admin(user)
    try:
        with PlatformSession() as session:
            rule = session.query(MonitoringRule).filter_by(id=rule_id).one()
            worker.compile_query(session, rule, window_start=None, window_end=None)
            row = service.publish_rule(session, rule_id, published_by=user["id"])
            result = service.rule_dict(row)
            session.commit()
        _audit(user, action="发布监控规则", resource=f"monitoring-rule:{rule_id}")
        return result
    except Exception as exc:
        _domain_error(exc)


@router.post("/rules/{rule_id}/pause")
def pause_rule(rule_id: str, user: dict = Depends(require_user)):
    _require_admin(user)
    try:
        with PlatformSession() as session:
            row = service.pause_rule(session, rule_id)
            result = service.rule_dict(row)
            session.commit()
        _audit(user, action="暂停监控规则", resource=f"monitoring-rule:{rule_id}")
        return result
    except Exception as exc:
        _domain_error(exc)


@router.post("/rules/{rule_id}/resume")
def resume_rule(rule_id: str, user: dict = Depends(require_user)):
    _require_admin(user)
    try:
        with PlatformSession() as session:
            rule = session.query(MonitoringRule).filter_by(id=rule_id).one()
            worker.compile_query(session, rule, window_start=None, window_end=None)
            row = service.resume_rule(session, rule_id, resumed_by=user["id"])
            result = service.rule_dict(row)
            session.commit()
        _audit(user, action="恢复监控规则", resource=f"monitoring-rule:{rule_id}")
        return result
    except Exception as exc:
        _domain_error(exc)


@router.post("/rules/{rule_id}/run", status_code=status.HTTP_202_ACCEPTED)
def run_rule(rule_id: str, user: dict = Depends(require_user)):
    _require_admin(user)
    current = datetime.now()
    try:
        with PlatformSession() as session:
            rule = session.query(MonitoringRule).filter_by(id=rule_id).one()
            if rule.status != "published":
                raise ValueError("只有已发布规则可以执行")
            logical_period, start, end = worker.completed_window(rule, current)
            task = worker.enqueue_rule_task(
                session,
                rule,
                # Manual runs remain idempotent for the same complete period.
                logical_period=logical_period,
                window_start=start,
                window_end=end,
                now=current,
            )
            result = {"taskId": task.id, "status": task.status}
            session.commit()
        _audit(user, action="触发监控规则", resource=f"monitoring-rule:{rule_id}")
        return result
    except Exception as exc:
        _domain_error(exc)


@router.get("/alerts")
def list_alerts(
    status_value: str | None = Query(default=None, alias="status", pattern=r"^(firing|recovering|resolved)$"),
    severity: str | None = Query(default=None, pattern=r"^(low|medium|high|critical)$"),
    source_id: int | None = Query(default=None, alias="sourceId", ge=1),
    limit: int = Query(default=100, ge=1, le=300),
    user: dict = Depends(require_user),
):
    with PlatformSession() as session:
        allowed = _allowed_sources(session, user)
        query = session.query(MonitoringAlert).filter(MonitoringAlert.source_id.in_(allowed))
        if source_id is not None:
            query = query.filter(MonitoringAlert.source_id == source_id)
        if status_value:
            query = query.filter(MonitoringAlert.status == status_value)
        if severity:
            query = query.filter(MonitoringAlert.severity == severity)
        rows = [
            row
            for row in query.order_by(MonitoringAlert.last_seen_at.desc()).limit(limit * 3).all()
            if _alert_in_dimension_scope(session, user, row)
        ][:limit]
        return {"items": [service.alert_dict(row) for row in rows], "total": len(rows)}


def _visible_alert(session, alert_id: str, user: dict) -> MonitoringAlert:
    row = session.get(MonitoringAlert, alert_id)
    if (
        row is None
        or row.source_id not in _allowed_sources(session, user)
        or not _alert_in_dimension_scope(session, user, row)
    ):
        raise HTTPException(status_code=404, detail="警报不存在或无权访问")
    return row


@router.get("/alerts/{alert_id}")
def get_alert(alert_id: str, user: dict = Depends(require_user)):
    with PlatformSession() as session:
        alert = _visible_alert(session, alert_id, user)
        result = service.alert_dict(alert)
        rule = session.get(MonitoringRule, alert.rule_id)
        result["rule"] = service.rule_dict(rule) if rule else None
        return result


@router.get("/alerts/{alert_id}/evidence")
def get_alert_evidence(alert_id: str, user: dict = Depends(require_user)):
    with PlatformSession() as session:
        _visible_alert(session, alert_id, user)
        rows = session.query(AlertEvidence, MonitoringObservation).join(
            MonitoringObservation, MonitoringObservation.id == AlertEvidence.observation_id
        ).filter(AlertEvidence.alert_id == alert_id).order_by(
            MonitoringObservation.created_at
        ).all()
        return {
            "items": [
                {
                    "id": evidence.id,
                    "observationId": observation.id,
                    "logicalPeriod": observation.logical_period,
                    "currentValue": float(observation.current_value) if observation.current_value is not None else None,
                    "baselineValue": float(observation.baseline_value) if observation.baseline_value is not None else None,
                    "sampleSize": observation.sample_size,
                    "qualityStatus": observation.quality_status,
                    "evaluationState": observation.evaluation_state,
                    "dataCutoffAt": observation.data_cutoff_at.isoformat() if observation.data_cutoff_at else None,
                    "createdAt": observation.created_at.isoformat(),
                }
                for evidence, observation in rows
            ]
        }


@router.get("/alerts/{alert_id}/timeline")
def get_alert_timeline(alert_id: str, user: dict = Depends(require_user)):
    with PlatformSession() as session:
        alert = _visible_alert(session, alert_id, user)
        rows = session.query(AlertEvidence, MonitoringObservation).join(
            MonitoringObservation,
            MonitoringObservation.id == AlertEvidence.observation_id,
        ).filter(AlertEvidence.alert_id == alert_id).order_by(
            MonitoringObservation.created_at
        ).all()
        items = [
            {
                "at": observation.created_at.isoformat(),
                "state": observation.evaluation_state,
                "logicalPeriod": observation.logical_period,
                "currentValue": float(observation.current_value) if observation.current_value is not None else None,
            }
            for _, observation in rows
        ]
        if alert.resolved_at:
            items.append({"at": alert.resolved_at.isoformat(), "state": "resolved"})
        return {"items": items}


@router.get("/alerts/{alert_id}/deliveries")
def get_alert_deliveries(alert_id: str, user: dict = Depends(require_user)):
    with PlatformSession() as session:
        _visible_alert(session, alert_id, user)
        rows = session.query(AlertDelivery).filter_by(alert_id=alert_id).order_by(
            AlertDelivery.created_at.desc()
        ).all()
        return {
            "items": [
                {
                    "id": row.id,
                    "eventType": row.event_type,
                    "channel": row.channel,
                    "status": row.status,
                    "attemptCount": row.attempt_count,
                    "error": row.error_summary,
                    "sentAt": row.sent_at.isoformat() if row.sent_at else None,
                }
                for row in rows
            ]
        }


@router.get("/runs")
def list_runs(user: dict = Depends(require_user)):
    _require_admin(user)
    with PlatformSession() as session:
        rows = session.query(MonitoringTask).order_by(MonitoringTask.created_at.desc()).limit(200).all()
        return {
            "items": [
                {
                    "id": row.id,
                    "ruleId": row.rule_id,
                    "logicalPeriod": row.logical_period,
                    "status": row.status,
                    "attemptCount": row.attempt_count,
                    "error": row.error_summary,
                    "createdAt": row.created_at.isoformat(),
                    "finishedAt": row.finished_at.isoformat() if row.finished_at else None,
                }
                for row in rows
            ]
        }


@router.post("/runs/{task_id}/retry", status_code=status.HTTP_202_ACCEPTED)
def retry_run(task_id: str, user: dict = Depends(require_user)):
    _require_admin(user)
    try:
        with PlatformSession() as session:
            row = session.query(MonitoringTask).filter_by(id=task_id).with_for_update().one()
            if row.status not in {"dead_letter", "retry_wait"}:
                raise ValueError("只有失败或等待重试的任务可以重新排队")
            row.status = "queued"
            row.next_attempt_at = datetime.now()
            row.error_summary = None
            row.lease_owner = None
            row.lease_expires_at = None
            row.finished_at = None
            result = {"taskId": row.id, "status": row.status}
            session.commit()
        _audit(user, action="重试监控任务", resource=f"monitoring-task:{task_id}")
        return result
    except Exception as exc:
        _domain_error(exc)


@router.get("/operations/metrics")
def monitoring_metrics(user: dict = Depends(require_user)):
    _require_admin(user)
    with PlatformSession() as session:
        task_counts = {
            state: session.query(MonitoringTask).filter_by(status=state).count()
            for state in ("queued", "running", "retry_wait", "succeeded", "dead_letter")
        }
        alert_counts = {
            state: session.query(MonitoringAlert).filter_by(status=state).count()
            for state in ("firing", "recovering", "resolved")
        }
        delivery_counts = {
            state: session.query(AlertDelivery).filter_by(status=state).count()
            for state in ("queued", "sending", "retry_wait", "sent", "failed")
        }
        return {"tasks": task_counts, "alerts": alert_counts, "deliveries": delivery_counts}


@router.get("/dead-letters")
def list_dead_letters(user: dict = Depends(require_user)):
    _require_admin(user)
    with PlatformSession() as session:
        rows = session.query(MonitoringTask).filter_by(status="dead_letter").order_by(
            MonitoringTask.finished_at.desc(), MonitoringTask.created_at.desc()
        ).limit(200).all()
        return {
            "items": [
                {
                    "id": row.id,
                    "ruleId": row.rule_id,
                    "logicalPeriod": row.logical_period,
                    "attemptCount": row.attempt_count,
                    "error": row.error_summary,
                    "finishedAt": row.finished_at.isoformat() if row.finished_at else None,
                }
                for row in rows
            ]
        }
