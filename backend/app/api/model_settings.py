"""Runtime model-service configuration exposed to the authenticated profile page."""

from __future__ import annotations

from hashlib import sha256
from typing import Any, Literal
from urllib.parse import urlparse
from uuid import uuid4

import httpx
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
    api_keys: list[str] = Field(default_factory=list, alias="apiKeys", max_length=20)
    active_key_id: str | None = Field(default=None, alias="activeKeyId", max_length=80)
    remove_key_ids: list[str] = Field(default_factory=list, alias="removeKeyIds", max_length=20)
    clear_key: bool = Field(default=False, alias="clearKey")
    profile_id: str | None = Field(default=None, alias="profileId", max_length=80)
    profile_name: str | None = Field(default=None, alias="profileName", max_length=80)
    protocol: Literal["openai_compatible", "anthropic_messages"] | None = None
    auth_mode: Literal["auto", "bearer", "x_api_key"] | None = Field(default=None, alias="authMode")
    activate: bool = Field(default=False)
    delete: bool = Field(default=False)


class ModelDiscoveryPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    base_url: str = Field(alias="baseUrl", max_length=500)
    api_key: str | None = Field(default=None, alias="apiKey", max_length=500)
    profile_id: str | None = Field(default=None, alias="profileId", max_length=80)
    active_key_id: str | None = Field(default=None, alias="activeKeyId", max_length=80)
    protocol: Literal["openai_compatible", "anthropic_messages"] = "openai_compatible"
    auth_mode: Literal["auto", "bearer", "x_api_key"] = Field(default="auto", alias="authMode")


# Key 只驻留 API 进程，不进入响应、不进入浏览器 localStorage。环境变量配置会
# 自动成为第一个配置档；生产部署可将这一层替换成 KMS/Secret Manager。
_profiles: dict[str, dict[str, Any]] = {}
_active_profile_id = ""


def _key_id(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()[:16]


def _masked_key(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    if len(value) <= 8:
        return "•" * len(value)
    return f"{value[:4]}{'•' * 8}{value[-4:]}"


def _profile_keys(profile: dict[str, Any]) -> list[str]:
    """Read the new list shape while accepting profiles made by older code/tests."""
    values = profile.get("apiKeys")
    if not isinstance(values, list):
        values = [profile.get("apiKey", "")]
    return list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))


def _selected_key(profile: dict[str, Any] | None) -> str:
    if not profile:
        return ""
    keys = _profile_keys(profile)
    active_id = str(profile.get("activeKeyId") or "")
    return next((key for key in keys if _key_id(key) == active_id), keys[0] if keys else "")


def _ensure_profiles() -> None:
    global _active_profile_id
    if _profiles:
        for profile in _profiles.values():
            keys = _profile_keys(profile)
            profile["apiKeys"] = keys
            profile["activeKeyId"] = profile.get("activeKeyId") or (_key_id(keys[0]) if keys else "")
            profile["protocol"] = profile.get("protocol") or "openai_compatible"
            profile["authMode"] = profile.get("authMode") or "auto"
            profile.pop("apiKey", None)
        return
    if settings.llm_api_key.strip():
        key = settings.llm_api_key.strip()
        _profiles["env"] = {
            "id": "env",
            "name": "环境变量配置",
            "baseUrl": settings.llm_base_url,
            "model": settings.llm_model,
            "protocol": settings.llm_protocol,
            "authMode": settings.llm_auth_mode,
            "apiKeys": [key],
            "activeKeyId": _key_id(key),
        }
        _active_profile_id = "env"


def _key_view(value: str, active_key_id: str) -> dict[str, Any]:
    key_id = _key_id(value)
    return {"id": key_id, "maskedKey": _masked_key(value), "active": key_id == active_key_id}


def _profile_view(profile: dict[str, Any]) -> dict[str, Any]:
    keys = _profile_keys(profile)
    active_key_id = str(profile.get("activeKeyId") or "")
    selected = _selected_key(profile)
    return {
        "id": profile["id"], "name": profile["name"], "baseUrl": profile["baseUrl"],
        "model": profile["model"], "protocol": profile.get("protocol", "openai_compatible"),
        "authMode": profile.get("authMode", "auto"),
        "configured": bool(selected), "maskedKey": _masked_key(selected),
        "activeKeyId": active_key_id or None, "keyCount": len(keys),
        "keys": [_key_view(value, active_key_id) for value in keys],
        "active": profile["id"] == _active_profile_id,
    }


def resolve_runtime_profile(profile_id: str | None = None) -> dict[str, Any]:
    """Resolve one server-side model profile without exposing its credential to APIs.

    Runs call this by the profile id frozen at submission time.  The returned object
    must stay inside the backend process and must never be serialized into a run,
    event, checkpoint, response, or browser payload.
    """

    _ensure_profiles()
    selected_id = profile_id or _active_profile_id
    profile = _profiles.get(selected_id)
    if profile is None:
        raise KeyError(selected_id or "default")
    api_key = _selected_key(profile)
    if not api_key:
        raise ValueError("模型配置没有可用凭据")
    return {
        "id": profile["id"],
        "name": profile["name"],
        "protocol": profile.get("protocol", "openai_compatible"),
        "authMode": profile.get("authMode", "auto"),
        "baseUrl": profile["baseUrl"],
        "model": profile["model"],
        "apiKey": api_key,
    }


def _payload() -> dict[str, Any]:
    _ensure_profiles()
    active = _profiles.get(_active_profile_id)
    selected = _selected_key(active)
    return {
        "configured": bool(selected),
        "baseUrl": active["baseUrl"] if active else settings.llm_base_url,
        "model": active["model"] if active else settings.llm_model,
        "protocol": active.get("protocol", "openai_compatible") if active else settings.llm_protocol,
        "authMode": active.get("authMode", "auto") if active else settings.llm_auth_mode,
        "maskedKey": _masked_key(selected or settings.llm_api_key),
        "activeProfileId": _active_profile_id or None,
        "activeKeyId": (active or {}).get("activeKeyId") or None,
        "profiles": [_profile_view(item) for item in _profiles.values()],
        "scope": "当前 API 进程",
    }


def _validate_base_url(value: str) -> str:
    normalized = value.strip()
    parsed = urlparse(normalized)
    if (parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password
            or parsed.query or parsed.fragment):
        raise HTTPException(status_code=422, detail="Base URL 必须是无凭据、查询参数和片段的 http 或 https 地址")
    return normalized.rstrip("/")


def _apply_active_profile() -> None:
    active = _profiles.get(_active_profile_id)
    if not active:
        return
    settings.llm_base_url = active["baseUrl"]
    settings.llm_model = active["model"]
    settings.llm_api_key = _selected_key(active)
    settings.llm_protocol = active.get("protocol", "openai_compatible")
    settings.llm_auth_mode = active.get("authMode", "auto")
    from app.llm.provider import reset_client
    reset_client()


@router.get("/llm")
def get_llm_settings(user: dict | None = Depends(optional_user)) -> dict:
    return _payload()


@router.patch("/llm")
def update_llm_settings(payload: LlmSettingsPayload, user: dict | None = Depends(optional_user)) -> dict:
    """Apply settings immediately without ever returning any clear-text key."""
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
    base_url = (_validate_base_url(payload.base_url) if payload.base_url.strip()
                else existing.get("baseUrl", settings.llm_base_url))
    model = payload.model.strip() or existing.get("model", settings.llm_model)
    protocol = payload.protocol or existing.get("protocol") or "openai_compatible"
    auth_mode = payload.auth_mode or existing.get("authMode") or "auto"
    keys = [] if payload.clear_key else _profile_keys(existing)
    remove_ids = set(payload.remove_key_ids)
    keys = [key for key in keys if _key_id(key) not in remove_ids]
    additions = [payload.api_key or "", *payload.api_keys]
    keys.extend(str(key).strip() for key in additions if str(key).strip())
    keys = list(dict.fromkeys(keys))
    if len(keys) > 20:
        raise HTTPException(status_code=422, detail="单个模型配置最多保存 20 个 Key")
    if not keys and not (payload.clear_key and payload.profile_id):
        raise HTTPException(status_code=422, detail="请输入有效的 API Key，或选择已有配置")
    available_ids = {_key_id(key) for key in keys}
    requested_key_id = payload.active_key_id or str(existing.get("activeKeyId") or "")
    if payload.active_key_id and payload.active_key_id not in available_ids:
        raise HTTPException(status_code=422, detail="所选 Key 不存在或已被移除")
    active_key_id = requested_key_id if requested_key_id in available_ids else (_key_id(keys[0]) if keys else "")
    _profiles[profile_id] = {
        "id": profile_id,
        "name": (payload.profile_name or existing.get("name") or f"{model} 配置").strip()[:80],
        "baseUrl": base_url, "model": model, "protocol": protocol, "authMode": auth_mode,
        "apiKeys": keys, "activeKeyId": active_key_id,
    }
    if payload.activate or not payload.profile_id or profile_id == _active_profile_id:
        _active_profile_id = profile_id
        _apply_active_profile()
    return _payload()


def _discovery_endpoint(base_url: str, protocol: str) -> str:
    normalized = base_url.rstrip("/")
    if protocol == "anthropic_messages":
        if normalized.endswith("/models"):
            return normalized
        if normalized.endswith("/v1"):
            return f"{normalized}/models"
        return f"{normalized}/v1/models"
    return f"{normalized}/models"


def _discovery_headers(api_key: str, protocol: str, auth_mode: str) -> dict[str, str]:
    selected = auth_mode if auth_mode in {"bearer", "x_api_key"} else (
        "x_api_key" if protocol == "anthropic_messages" else "bearer"
    )
    headers = {"Accept": "application/json"}
    if selected == "x_api_key":
        headers["x-api-key"] = api_key
    else:
        headers["Authorization"] = f"Bearer {api_key}"
    if protocol == "anthropic_messages":
        headers["anthropic-version"] = "2023-06-01"
    return headers


def _discover_models(base_url: str, api_key: str, protocol: str = "openai_compatible", auth_mode: str = "auto") -> list[str]:
    endpoint = _discovery_endpoint(base_url, protocol)
    try:
        with httpx.Client(timeout=10.0, proxy=settings.llm_proxy_url or None, trust_env=False,
                          follow_redirects=False) as client:
            response = client.get(endpoint, headers=_discovery_headers(api_key, protocol, auth_mode))
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        raise HTTPException(status_code=502, detail="无法连接模型服务，请检查 Base URL、代理或网络") from exc
    if response.status_code in {401, 403}:
        raise HTTPException(status_code=422, detail="模型服务拒绝了当前 Key，请检查密钥权限")
    if response.status_code == 404:
        protocol_label = "Anthropic" if protocol == "anthropic_messages" else "OpenAI 兼容"
        raise HTTPException(status_code=422, detail=f"该服务未提供 {protocol_label} 的模型列表接口，请手动填写模型名称")
    if response.status_code == 429:
        raise HTTPException(status_code=429, detail="模型服务请求过于频繁，请稍后再检测")
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"模型服务检测失败（HTTP {response.status_code}）")
    try:
        body = response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="模型服务的 /models 返回了非 JSON 内容") from exc
    raw_models = body.get("data") if isinstance(body, dict) else None
    if not isinstance(raw_models, list) and isinstance(body, dict):
        raw_models = body.get("models")
    ids = []
    for item in raw_models if isinstance(raw_models, list) else []:
        model_id = item.get("id") if isinstance(item, dict) else item
        if model_id and str(model_id).strip():
            ids.append(str(model_id).strip())
    return sorted(dict.fromkeys(ids), key=str.lower)[:200]


@router.post("/llm/discover-models")
def discover_llm_models(payload: ModelDiscoveryPayload, user: dict | None = Depends(optional_user)) -> dict[str, Any]:
    """Inspect the selected protocol's models endpoint with the explicitly selected key."""
    _ensure_profiles()
    profile = _profiles.get(payload.profile_id or "")
    selected_profile_key = ""
    if profile and payload.active_key_id:
        selected_profile_key = next(
            (key for key in _profile_keys(profile) if _key_id(key) == payload.active_key_id), "",
        )
    api_key = (payload.api_key or "").strip() or selected_profile_key or _selected_key(profile)
    if not api_key:
        raise HTTPException(status_code=422, detail="请先输入或选择一个 API Key")
    base_url = _validate_base_url(payload.base_url)
    models = _discover_models(base_url, api_key, payload.protocol, payload.auth_mode)
    return {"models": models, "count": len(models),
            "endpoint": _discovery_endpoint(base_url, payload.protocol),
            "message": "检测成功" if models else "接口可访问，但没有返回可用模型"}
