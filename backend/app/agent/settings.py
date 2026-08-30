"""受限 Planner 灰度配置与对照指标。"""

from __future__ import annotations

from datetime import datetime

from app.config import settings
from app.db.models import AnalysisRun, PlatformFeatureFlag
from app.db.platform import PlatformSession


FEATURE_KEY = "bounded_business_planner"


def _setting(session) -> PlatformFeatureFlag:
    row = session.query(PlatformFeatureFlag).filter_by(key=FEATURE_KEY).first()
    if row is None:
        row = PlatformFeatureFlag(
            key=FEATURE_KEY,
            enabled=bool(settings.agent_planner_enabled),
            config_json={
                "rolloutPercent": max(0, min(int(settings.agent_planner_rollout_percent), 100)),
                "modelPlanningEnabled": bool(settings.agent_planner_model_enabled),
            },
        )
        session.add(row)
        session.flush()
    return row


def _payload(row: PlatformFeatureFlag) -> dict:
    config = row.config_json or {}
    return {
        "key": row.key,
        "enabled": bool(row.enabled),
        "rolloutPercent": max(0, min(int(config.get("rolloutPercent", 100)), 100)),
        "modelPlanningEnabled": bool(config.get("modelPlanningEnabled", True)),
        "updatedBy": row.updated_by,
        "updatedAt": row.updated_at.isoformat() if row.updated_at else None,
    }


def snapshot_settings(session=None) -> dict:
    if session is not None:
        return _payload(_setting(session))
    with PlatformSession() as db:
        row = _setting(db)
        db.commit()
        return _payload(row)


def update_settings(
    enabled: bool,
    *,
    rollout_percent: int,
    model_planning_enabled: bool,
    updated_by: str,
) -> dict:
    with PlatformSession() as session:
        row = _setting(session)
        row.enabled = bool(enabled)
        row.config_json = {
            "rolloutPercent": max(0, min(int(rollout_percent), 100)),
            "modelPlanningEnabled": bool(model_planning_enabled),
        }
        row.updated_by = updated_by or "admin"
        row.updated_at = datetime.now()
        session.commit()
        return _payload(row)


def experiment_summary() -> dict:
    with PlatformSession() as session:
        rows = session.query(AnalysisRun).filter(
            AnalysisRun.status.in_(["succeeded", "failed", "blocked"]),
            AnalysisRun.workflow_version.in_(["bounded-planner-v1", "governed-baseline-v1"]),
        ).all()
    groups = {
        "planner": [item for item in rows if item.workflow_version == "bounded-planner-v1"],
        "baseline": [item for item in rows if item.workflow_version == "governed-baseline-v1"],
    }
    result = {}
    for name, items in groups.items():
        durations = [
            (item.finished_at - item.started_at).total_seconds() * 1000
            for item in items if item.started_at and item.finished_at
        ]
        count = len(items)
        result[name] = {
            "runs": count,
            "successRate": round(sum(item.status == "succeeded" for item in items) / count, 4) if count else None,
            "averageTokens": round(sum(item.total_tokens or 0 for item in items) / count, 1) if count else None,
            "averageDurationMs": round(sum(durations) / len(durations), 1) if durations else None,
        }
    return result
