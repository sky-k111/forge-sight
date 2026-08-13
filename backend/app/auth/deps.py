"""渐进式认证依赖：token 可选，未登录回退演示模式，绝不强制 401。"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, Header, HTTPException
from starlette.responses import JSONResponse

from app.config import settings
from app.auth.service import decode_token, get_user_by_username, user_payload


PUBLIC_AUTH_PATHS = {
    "/api/auth/register",
    "/api/auth/login",
}
RESTRICTED_AUTH_PATHS = {
    "/api/auth/me",
    "/api/auth/logout",
    "/api/auth/email/challenges",
    "/api/auth/email/change",
    "/api/auth/email/verify",
}


def _gate_error(status_code: int, detail: str, error_code: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"detail": detail, "errorCode": error_code},
    )


def email_gate_response(path: str, authorization: str | None) -> JSONResponse | None:
    """Return a denial response for protected API paths, otherwise None."""
    if not path.startswith("/api/") or path in PUBLIC_AUTH_PATHS:
        return None
    if path in RESTRICTED_AUTH_PATHS:
        return None

    claims = None
    user = None
    if authorization and authorization.startswith("Bearer "):
        claims = decode_token(authorization.removeprefix("Bearer ").strip())
        if claims:
            user = get_user_by_username(claims.get("sub", ""))
            if user is not None and not user.enabled:
                user = None

    if settings.auth_email_gate_enabled:
        if user is None:
            return _gate_error(401, "请先登录", "AUTH_REQUIRED")
        is_full = (
            claims.get("auth_scope") == "full"
            and user.email_verified_at is not None
        )
        if not is_full:
            return _gate_error(403, "请先完成邮箱验证", "EMAIL_VERIFICATION_REQUIRED")
        return None

    if user is not None or settings.demo_mode_enabled:
        return None
    return _gate_error(401, "请先登录", "AUTH_REQUIRED")


def validate_email_gate_configuration() -> None:
    if (
        settings.auth_email_gate_enabled
        and settings.mail_provider.strip().lower() == "capture"
        and not settings.auth_email_gate_allow_capture
    ):
        raise RuntimeError(
            "AUTH_EMAIL_GATE_ENABLED cannot be used with capture mail unless "
            "AUTH_EMAIL_GATE_ALLOW_CAPTURE=true"
        )


def optional_user(authorization: str | None = Header(default=None)) -> dict | None:
    """解析 Bearer token → 用户 payload；无 token/无效/过期/停用 → None。

    绝不抛 401：未登录保持 local-test-user 演示模式，由调用方回退。
    """
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization.removeprefix("Bearer ").strip()
    claims = decode_token(token)
    if not claims:
        return None
    user = get_user_by_username(claims.get("sub", ""))
    if not user or not user.enabled:
        return None
    return user_payload(user)


def require_user(user: dict | None = Depends(optional_user)) -> dict:
    """强制登录依赖（登录/注册/me 等端点用）；无效 → 401。"""
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    return user


def effective_user_id(raw: str | None, user: dict | None) -> str:
    """token 用户优先，未登录回退客户端传入值。"""
    if user:
        return user["username"]
    return raw or "local-test-user"


def effective_stable_user_id(raw: str | None, user: dict | None) -> str:
    """Use the immutable account id for persisted ownership relationships."""

    if user:
        return user["id"]
    return raw or "local-test-user"


def current_user_display(user: dict | None) -> tuple[str, str]:
    """返回 (user_name, role) 供审计使用；未登录为演示用户。"""
    if user:
        return user["displayName"] or user["username"], "业务人员" if user["role"] == "business" else "管理员"
    return "演示用户", "业务人员"
