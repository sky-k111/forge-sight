"""建模 HTTP 契约（镜像前端 mock 字段与 IMPLEMENTATION_PLAN 阶段 7 规划）。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ModelingTaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    source: str = Field(default="agent", pattern=r"^(agent|manual)$")
    sourceId: int = Field(default=1, ge=1)
    ownerId: str = Field(default="local-test-user", min_length=1, max_length=64)
    goal: str = Field(min_length=1, max_length=2000)
    taskType: str = Field(pattern=r"^(regression|classification|clustering|anomaly)$")
    datasetName: str = Field(default="", max_length=100)
    datasetSql: str = Field(min_length=1, max_length=8000)
    targetField: str = Field(default="", max_length=64)
    targetLabel: str = Field(default="", max_length=100)
    features: list[str] = Field(default_factory=list, max_length=50)
    candidates: list[str] = Field(default_factory=list, max_length=6)
    splitStrategy: str = Field(default="auto", pattern=r"^(auto|random|time|group)$")
    timeField: str = Field(default="", max_length=64)
    groupField: str = Field(default="", max_length=64)
    runId: str | None = Field(default=None, max_length=40)


class TrainRequest(BaseModel):
    strategy: str = Field(default="auto", pattern=r"^(auto|manual)$")
    candidateIds: list[str] = Field(default_factory=list, max_length=6)
    manualModelId: str | None = Field(default=None, max_length=30)
    optimizationLevel: str = Field(default="fast", pattern=r"^(fast|balanced|thorough)$")
    userId: str = Field(default="local-test-user", min_length=1, max_length=64)


class PredictRequest(BaseModel):
    records: list[dict[str, Any]] = Field(min_length=1, max_length=100)
    userId: str = Field(default="local-test-user", min_length=1, max_length=64)


class GovernanceReview(BaseModel):
    userId: str = Field(default="local-test-user", min_length=1, max_length=64)
    decision: str = Field(pattern=r"^(pass|reject)$")
    comment: str = Field(default="", max_length=500)


class GovernanceAction(BaseModel):
    userId: str = Field(default="local-test-user", min_length=1, max_length=64)


class ThresholdUpdate(BaseModel):
    userId: str = Field(default="local-test-user", min_length=1, max_length=64)
    threshold: float = Field(ge=0.05, le=0.95)
    reason: str = Field(min_length=1, max_length=500)
