"""渐进式认证依赖：token 可选，未登录回退演示模式，绝不强制 401。"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, Header, HTTPException

from app.auth.service import decode_token, get_user_by_username, user_payload


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


def current_user_display(user: dict | None) -> tuple[str, str]:
    """返回 (user_name, role) 供审计使用；未登录为演示用户。"""
    if user:
        return user["displayName"] or user["username"], "业务人员" if user["role"] == "business" else "管理员"
    return "演示用户", "业务人员"
