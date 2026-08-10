"""阶段 5 对外稳定契约。内部 LangGraph 状态不得直接暴露给前端。"""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_USER = "waiting_user"
    RETRYING = "retrying"
    SUCCEEDED = "succeeded"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SessionCreate(BaseModel):
    title: str = Field(default="新分析", min_length=1, max_length=200)
    sourceId: int = Field(ge=1)
    userId: str = Field(default="local-test-user", min_length=1, max_length=64)


class RunCreate(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


class ClarificationResponse(BaseModel):
    answer: str = Field(min_length=1, max_length=500)


class BranchCreate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    userId: str = Field(default="local-test-user", min_length=1, max_length=64)
    instruction: str | None = Field(default=None, min_length=1, max_length=2000)
    adjustments: dict[str, Any] = Field(default_factory=dict)
    reuseArtifacts: str = Field(default="auto", pattern=r"^(auto|refresh|snapshot)$")


class UsageRecord(BaseModel):
    input_tokens: int | None = Field(default=None, ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    context_window: int | None = Field(default=None, ge=0)
    provider_request_id: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)
