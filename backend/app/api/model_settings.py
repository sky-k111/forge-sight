"""Runtime model-service configuration exposed to the authenticated profile page."""

from __future__ import annotations

from uuid import uuid4
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.auth.deps import optional_user
from app.config import settings

router = APIRouter(prefix="/api/model-settings", tags=["model-settings"])


class LlmSettingsPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    base_url: str = Field(default="", alias="baseUrl", max_length=500)
    model: str = Field(default="", max_length=160)
    api_key: str | None = Field(default=None, alias="apiKey", max_length=500)
    clear_key: bool = Field(default=False, alias="clearKey")
    profile_id: str | None = Field(default=None, alias="profileId", max_length=80)
    profile_name: str | None = Field(default=None, alias="profileName", max_length=80)
    activate: bool = Field(default=False)
    delete: bool = Field(default=False)


# Key 只驻留 API 进程，不进入响应、不进入浏览器 localStorage。环境变量配置会
# 自动成为第一个配置档；通过页面新增的配置档用于当前进程内快速切换，避免把
# 明文密钥写入业务数据库。生产部署可再将这一层替换成 KMS/Secret Manager。
_profiles: dict[str, dict[str, str]] = {}
_active_profile_id = ""


def _masked_key(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    if len(value) <= 8:
        return "•" * len(value)
    return f"{value[:4]}{'•' * 8}{value[-4:]}"


def _ensure_profiles() -> None:
    global _active_profile_id
    if _profiles:
        return
    if settings.llm_api_key.strip():
        _profiles["env"] = {
            "id": "env",
            "name": "环境变量配置",
            "baseUrl": settings.llm_base_url,
            "model": settings.llm_model,
            "apiKey": settings.llm_api_key,
        }
        _active_profile_id = "env"


def _profile_view(profile: dict[str, str]) -> dict:
    return {
        "id": profile["id"],
        "name": profile["name"],
        "baseUrl": profile["baseUrl"],
        "model": profile["model"],
        "configured": bool(profile.get("apiKey", "").strip()),
        "maskedKey": _masked_key(profile.get("apiKey", "")),
        "active": profile["id"] == _active_profile_id,
    }


def _payload() -> dict:
    _ensure_profiles()
    active = _profiles.get(_active_profile_id)
    # 保持旧版字段，已有前端和外部调用方无需同步升级。
    return {
        "configured": bool(active and active.get("apiKey", "").strip()),
        "baseUrl": active["baseUrl"] if active else settings.llm_base_url,
        "model": active["model"] if active else settings.llm_model,
        "maskedKey": _masked_key(active.get("apiKey", "") if active else settings.llm_api_key),
        "activeProfileId": _active_profile_id or None,
        "profiles": [_profile_view(item) for item in _profiles.values()],
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
    global _active_profile_id
    _ensure_profiles()

    if payload.profile_id and payload.profile_id not in _profiles:
        raise HTTPException(status_code=404, detail="模型配置不存在，请刷新后重试")
    if payload.delete:
        if not payload.profile_id:
            raise HTTPException(status_code=422, detail="请选择要删除的模型配置")
        if payload.profile_id == _active_profile_id:
            raise HTTPException(status_code=422, detail="当前生效配置不能删除，请先切换到其他配置")
        if payload.profile_id == "env":
            raise HTTPException(status_code=422, detail="环境变量配置不能删除")
        _profiles.pop(payload.profile_id, None)
        return _payload()

    profile_id = payload.profile_id or uuid4().hex[:12]
    existing = _profiles.get(profile_id, {})
    base_url = _validate_base_url(payload.base_url.strip()) if payload.base_url.strip() else existing.get("baseUrl", settings.llm_base_url)
    model = payload.model.strip() or existing.get("model", settings.llm_model)
    if payload.clear_key:
        api_key = ""
    elif payload.api_key is not None and payload.api_key.strip():
        api_key = payload.api_key.strip()
    else:
        api_key = existing.get("apiKey", "")
    if not api_key and not (payload.clear_key and payload.profile_id):
        raise HTTPException(status_code=422, detail="请输入有效的 API Key，或选择已有配置")
    _profiles[profile_id] = {
        "id": profile_id,
        "name": (payload.profile_name or existing.get("name") or f"{model} 配置").strip()[:80],
        "baseUrl": base_url,
        "model": model,
        "apiKey": api_key,
    }
    # 新建/保存时默认立即启用；activate 也可单独用于切换已有配置。
    if payload.activate or not payload.profile_id or profile_id == _active_profile_id:
        _active_profile_id = profile_id
    active = _profiles.get(_active_profile_id)
    if active:
        settings.llm_base_url = active["baseUrl"]
        settings.llm_model = active["model"]
        settings.llm_api_key = active["apiKey"]
        from app.llm.provider import reset_client
        reset_client()
    return _payload()
