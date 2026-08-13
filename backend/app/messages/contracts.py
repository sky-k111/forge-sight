"""HTTP request contracts for inbox announcements."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AnnouncementCreate(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "title": "系统维护公告",
                "body": "今晚 22:00 至 23:00 进行例行维护。",
                "priority": "high",
                "isPinned": True,
                "audience": {"organizationIds": ["org-east"]},
                "validFrom": "2026-08-13T09:00:00",
                "expiresAt": "2026-08-14T09:00:00",
            }
        }
    )
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=10000)
    priority: str = Field(default="normal", pattern=r"^(normal|high|urgent)$")
    isPinned: bool = False
    audience: dict[str, Any]
    validFrom: datetime | None = None
    expiresAt: datetime | None = None

    @model_validator(mode="after")
    def validate_period(self):
        if self.validFrom and self.expiresAt and self.expiresAt <= self.validFrom:
            raise ValueError("expiresAt 必须晚于 validFrom")
        return self


class AnnouncementUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    body: str | None = Field(default=None, min_length=1, max_length=10000)
    priority: str | None = Field(default=None, pattern=r"^(normal|high|urgent)$")
    isPinned: bool | None = None
    audience: dict[str, Any] | None = None
    validFrom: datetime | None = None
    expiresAt: datetime | None = None

    @model_validator(mode="after")
    def reject_null_required_fields(self):
        required_when_present = {"title", "body", "priority", "isPinned", "audience"}
        for field_name in required_when_present & self.__pydantic_fields_set__:
            if getattr(self, field_name) is None:
                raise ValueError(f"{field_name} 不能为 null")
        return self


class AnnouncementWithdraw(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={"example": {"reason": "维护窗口取消"}}
    )
    reason: str = Field(min_length=1, max_length=500)
