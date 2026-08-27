"""Idempotent monitoring templates and default user data scopes."""

from app.agent.ids import new_id
from app.db.models import (
    DataScopePolicy,
    DataSource,
    MonitoringRuleTemplate,
    PlatformUser,
)
from app.db.platform import PlatformSession

TEMPLATES = (
    (
        "source-data-stale",
        "数据更新与断流",
        "data_quality",
        {"operator": "gt", "openThreshold": 86400, "closeThreshold": 3600, "pendingPeriods": 1, "recoveryPeriods": 1},
    ),
    (
        "process-yield-drop",
        "工序良率下降",
        "quality",
        {"operator": "lt", "openThreshold": 0.9, "closeThreshold": 0.93, "pendingPeriods": 2, "recoveryPeriods": 2},
    ),
    (
        "unplanned-downtime",
        "非计划停机",
        "equipment",
        {"operator": "gt", "openThreshold": 120, "closeThreshold": 30, "pendingPeriods": 1, "recoveryPeriods": 1},
    ),
)


def seed_monitoring_baseline(session_factory=PlatformSession) -> dict[str, int]:
    templates_created = 0
    scopes_created = 0
    with session_factory() as session:
        for code, name, topic, condition in TEMPLATES:
            if session.query(MonitoringRuleTemplate).filter_by(code=code, version=1).first():
                continue
            session.add(
                MonitoringRuleTemplate(
                    id=new_id("mrt"),
                    code=code,
                    version=1,
                    name=name,
                    topic=topic,
                    default_condition_json=condition,
                    default_quality_gate_json={},
                    status="published",
                )
            )
            templates_created += 1
        source_ids = [row[0] for row in session.query(DataSource.id).all()]
        if source_ids:
            for user_id, in session.query(PlatformUser.id).filter(PlatformUser.enabled.is_(True)).all():
                if session.query(DataScopePolicy).filter_by(
                    subject_type="user",
                    subject_id=user_id,
                    business_key="anomaly_monitoring",
                ).first():
                    continue
                session.add(
                    DataScopePolicy(
                        id=new_id("dsp"),
                        subject_type="user",
                        subject_id=user_id,
                        business_key="anomaly_monitoring",
                        effect="allow",
                        data_source_ids_json=source_ids,
                        dimensions_json={},
                        enabled=True,
                        created_by="system",
                    )
                )
                scopes_created += 1
        session.commit()
    return {"templatesCreated": templates_created, "scopesCreated": scopes_created}
