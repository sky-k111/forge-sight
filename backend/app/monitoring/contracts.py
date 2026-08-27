"""HTTP contracts for governed monitoring rules."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class MonitoringRuleCreate(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "name": "工序良率低于红线",
                "code": "process-yield-low",
                "sourceId": 1,
                "templateCode": "process-yield-drop",
                "metricName": "工序良率",
                "entityType": "process",
                "grain": "day",
                "queryPlan": {
                    "kind": "ratio",
                    "table": "mes_process_output",
                    "timeField": "stat_date",
                    "entityField": "process_id",
                    "numeratorField": "good_qty",
                    "denominatorFields": ["input_qty"],
                },
                "condition": {
                    "operator": "lt",
                    "openThreshold": 0.9,
                    "closeThreshold": 0.93,
                    "pendingPeriods": 2,
                    "recoveryPeriods": 2,
                },
                "notificationPolicy": {"userIds": []},
            }
        }
    )

    name: str = Field(min_length=1, max_length=160)
    code: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    sourceId: int = Field(ge=1)
    templateCode: str = Field(min_length=1, max_length=80)
    metricName: str = Field(min_length=1, max_length=120)
    entityType: str = Field(default="source", min_length=1, max_length=80)
    grain: str = Field(default="day", pattern=r"^(hour|day|shift)$")
    queryPlan: dict[str, Any]
    condition: dict[str, Any]
    qualityGate: dict[str, Any] = Field(default_factory=dict)
    severityPolicy: dict[str, Any] = Field(default_factory=dict)
    notificationPolicy: dict[str, Any] = Field(default_factory=dict)
    scheduleSeconds: int = Field(default=86400, ge=60, le=2592000)
