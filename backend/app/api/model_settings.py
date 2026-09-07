"""Runtime model-service configuration exposed to the authenticated profile page."""

from __future__ import annotations

from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.auth.deps import optional_user
from app.config import settings

router = APIRouter(prefix="/api/model-settings", tags=["model-settings"])


class LlmSettingsPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    base_url: str = Field(default="", alias="baseUrl", max_length=500)
    model: str = Field(default="", min_length=1, max_length=160)
    api_key: str | None = Field(default=None, alias="apiKey", max_length=500)
    clear_key: bool = Field(default=False, alias="clearKey")


def _masked_key(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    if len(value) <= 8:
        return "•" * len(value)
    return f"{value[:4]}{'•' * 8}{value[-4:]}"


def _payload() -> dict:
    return {
        "configured": bool(settings.llm_api_key.strip()),
        "baseUrl": settings.llm_base_url,
        "model": settings.llm_model,
        "maskedKey": _masked_key(settings.llm_api_key),
        "scope": "当前 API 进程",
    }


def _validate_base_url(value: str) -> str:
    normalized = value.strip()
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=422, detail="Base URL 必须是 http 或 https 地址")
    return normalized.rstrip("/")


@router.get("/llm")
def get_llm_settings(user: dict | None = Depends(optional_user)) -> dict:
    return _payload()


@router.patch("/llm")
def update_llm_settings(
    payload: LlmSettingsPayload,
    user: dict | None = Depends(optional_user),
) -> dict:
    """Apply settings immediately without ever returning the clear-text key."""
    if payload.clear_key:
        settings.llm_api_key = ""
    elif payload.api_key is not None:
        candidate = payload.api_key.strip()
        if not candidate:
            raise HTTPException(status_code=422, detail="请输入有效的 API Key，或选择清除当前 Key")
        settings.llm_api_key = candidate
    if payload.base_url.strip():
        settings.llm_base_url = _validate_base_url(payload.base_url)
    if payload.model.strip():
        settings.llm_model = payload.model.strip()
    return _payload()
