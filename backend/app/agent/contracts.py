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
    MODELING_PROPOSAL = "modeling-proposal"  # 建模提案待用户确认，非终态


class SessionCreate(BaseModel):
    title: str = Field(default="新分析", min_length=1, max_length=200)
    sourceId: int = Field(ge=1)
    userId: str = Field(default="local-test-user", min_length=1, max_length=64)


class SessionRename(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class SessionBulkDelete(BaseModel):
    sessionIds: list[str] = Field(min_length=1, max_length=100)


class RunCreate(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    modelProfileId: str | None = Field(default=None, min_length=1, max_length=80)


class ClarificationResponse(BaseModel):
    answer: str = Field(min_length=1, max_length=500)


class BranchCreate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    userId: str = Field(default="local-test-user", min_length=1, max_length=64)
    instruction: str | None = Field(default=None, min_length=1, max_length=2000)
    adjustments: dict[str, Any] = Field(default_factory=dict)
    reuseArtifacts: str = Field(default="auto", pattern=r"^(auto|refresh|snapshot)$")


