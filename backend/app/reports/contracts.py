"""阶段六报告 HTTP 契约。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator


class ReportCreate(BaseModel):
    templateId: str = Field(min_length=1, max_length=40)
    sourceId: int = Field(ge=1)
    periodStart: datetime
    periodEnd: datetime
    title: str | None = Field(default=None, min_length=1, max_length=200)
    ownerId: str = Field(default="local-test-user", min_length=1, max_length=64)
    prompt: str | None = Field(default=None, max_length=2000)
    scope: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_period(self):
        if self.periodEnd <= self.periodStart:
            raise ValueError("periodEnd 必须晚于 periodStart")
        if self.templateId == "tpl-topic-report" and not (self.prompt or "").strip():
            raise ValueError("专题分析报告必须填写分析问题")
        return self


class ReportReviewAction(BaseModel):
    userId: str = Field(default="local-test-user", min_length=1, max_length=64)


class ReportPublish(ReportReviewAction):
    pushToFeishu: bool = False


class SubscriptionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    templateId: str = Field(min_length=1, max_length=40)
    sourceId: int = Field(ge=1)
    ownerId: str = Field(default="local-test-user", min_length=1, max_length=64)
    cadence: str = Field(default="weekly", pattern=r"^(weekly|monthly)$")
    weekday: int | None = Field(default=1, ge=1, le=7)
    dayOfMonth: int | None = Field(default=None, ge=1, le=28)
    sendTime: str = Field(default="09:00", pattern=r"^\d{2}:\d{2}$")
    timezone: str = Field(default="Asia/Shanghai", min_length=1, max_length=50)
    channel: str = Field(default="email", pattern=r"^(email|feishu)$")
    publishPolicy: str = Field(default="auto_publish", pattern=r"^(after_review|auto_publish)$")

