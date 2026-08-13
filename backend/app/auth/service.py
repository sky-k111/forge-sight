"""认证服务：scrypt 密码哈希 + HMAC 自签 token + 注册/登录/种子管理员。

零新依赖（Python 3.14 标准库），规避 passlib/PyJWT 的兼容风险。
token 无状态（无服务端吊销）：登出 = 前端清理；如需吊销后续加黑名单表。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from datetime import datetime, timedelta
from typing import Any

from app.agent.ids import new_id
from app.audit.service import write_audit
from app.auth.email_verification import (
    EmailVerificationError,
    canonicalize_email,
    mask_email,
    normalize_email,
)
from app.config import settings
from app.db.models import PlatformUser
from app.db.platform import PlatformSession
from sqlalchemy.exc import IntegrityError

SCRYPT_N, SCRYPT_R, SCRYPT_P, SCRYPT_DKLEN = 2**14, 8, 1, 32
USERNAME_RE = re.compile(r"^[a-zA-Z0-9_]{3,30}$")


# ---------- 密码 ----------

def hash_password(password: str) -> str:
    """scrypt 哈希，格式 `scrypt$n$r$p$salt_hex$hash_hex`。"""
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=SCRYPT_DKLEN
    )
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """校验明文密码与存储哈希；格式非法直接返回 False。"""
    try:
        algo, n, r, p, salt_hex, hash_hex = stored.split("$")
        if algo != "scrypt":
            return False
        digest = hashlib.scrypt(
            password.encode(),
            salt=bytes.fromhex(salt_hex),
            n=int(n), r=int(r), p=int(p),
            dklen=len(bytes.fromhex(hash_hex)),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest, bytes.fromhex(hash_hex))


# ---------- token ----------

def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def create_token(user: PlatformUser) -> str:
    """自签 token：`base64url(payload).base64url(hmac-sha256)`，payload 含 sub/role/iat/exp。"""
    now = int(time.time())
    payload = {
        "sub": user.username,
        "role": user.role,
        "auth_scope": "full" if user.email_verified_at is not None else "restricted",
        "iat": now,
        "exp": now + settings.auth_token_ttl_seconds,
    }
    raw = _b64url(json.dumps(payload, separators=(",", ":")).encode())
    sig = _b64url(
        hmac.new(settings.auth_token_secret.encode(), raw.encode(), hashlib.sha256).digest()
    )
    return f"{raw}.{sig}"


def decode_token(token: str) -> dict[str, Any] | None:
    """校验签名与过期；任何失败返回 None（不抛异常）。"""
    try:
        raw, sig = token.split(".")
        expected = hmac.new(
            settings.auth_token_secret.encode(), raw.encode(), hashlib.sha256
        ).digest()
        actual = base64.urlsafe_b64decode(sig + "=" * (-len(sig) % 4))
        if not hmac.compare_digest(expected, actual):
            return None
        payload = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
        if payload.get("exp", 0) < time.time():
            return None
        return payload
    except (ValueError, TypeError, json.JSONDecodeError):
        return None


# ---------- 用户 ----------

def normalize_username(username: str) -> str:
    return str(username or "").strip().lower()


def validate_credentials(username: str, password: str) -> None:
    """非法输入抛 ValueError（由 API 层转 400）。"""
    if not USERNAME_RE.match(username):
        raise ValueError("用户名需为 3-30 位字母、数字或下划线")
    if not (6 <= len(password) <= 64):
        raise ValueError("密码长度需为 6-64 位")


def user_payload(user: PlatformUser) -> dict:
    email_status = email_status_for(user)
    return {
        "id": user.id,
        "username": user.username,
        "displayName": user.display_name,
        "role": user.role,
        "title": user.title,
        "emailMasked": mask_email(user.email) if user.email else None,
        "emailStatus": email_status,
    }


def email_status_for(user: PlatformUser) -> str:
    if user.email_verified_at is not None:
        return "verified"
    if user.email is not None:
        return "pending_verification"
    return "unbound"


def next_action_for(user: PlatformUser) -> str | None:
    status = email_status_for(user)
    if status == "unbound":
        return "bind_email"
    if status == "pending_verification":
        return "verify_email"
    return None


def auth_payload(user: PlatformUser) -> dict:
    return {
        "token": create_token(user),
        "user": user_payload(user),
        "nextAction": next_action_for(user),
    }


def get_user_by_username(username: str) -> PlatformUser | None:
    with PlatformSession() as session:
        return session.query(PlatformUser).filter(PlatformUser.username == username).first()


def register_user(username: str, password: str, display_name: str, email: str) -> dict:
    """注册（默认业务角色），返回 `{token, user}`；冲突/非法抛 ValueError。"""
    username = normalize_username(username)
    validate_credentials(username, password)
    deliverable = normalize_email(email)
    canonical = canonicalize_email(deliverable)
    if get_user_by_username(username):
        raise ValueError("用户名已存在")
    with PlatformSession() as session:
        if session.query(PlatformUser.id).filter_by(email_canonical=canonical).first():
            raise EmailVerificationError("EMAIL_TAKEN")
    user = PlatformUser(
        id=new_id("u"),
        username=username,
        password_hash=hash_password(password),
        display_name=(display_name or username)[:50],
        role="business",
        title="业务人员",
        email=deliverable,
        email_canonical=canonical,
    )
    with PlatformSession() as session:
        session.add(user)
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            if session.query(PlatformUser.id).filter_by(username=username).first():
                raise ValueError("用户名已存在") from exc
            raise EmailVerificationError("EMAIL_TAKEN") from exc
    write_audit("注册用户", resource=username, result="已记录", user_name=username, role="业务人员")
    return auth_payload(user)


def login(username: str, password: str) -> dict:
    """登录，失败统一抛 ValueError（不泄露用户是否存在）。"""
    username = normalize_username(username)
    user = get_user_by_username(username)
    if user is None or not verify_password(password, user.password_hash):
        write_audit("登录失败", resource=username, result="已拦截", user_name=username, role="业务人员")
        raise ValueError("用户名或密码错误")
    if not user.enabled:
        write_audit("登录失败", resource=username, result="已拦截", detail="账号已停用", user_name=username, role=user.role)
        raise ValueError("账号已停用")
    write_audit("登录", resource=username, result="允许", user_name=username, role=user.role)
    return auth_payload(user)


def logout(username: str) -> None:
    write_audit("退出登录", resource=username, result="已记录", user_name=username, role="业务人员")


def seed_admin() -> None:
    """启动时幂等创建种子管理员（已存在同名则保持）。"""
    username = normalize_username(settings.auth_admin_username)
    if not username:
        return
    if get_user_by_username(username):
        return
    user = PlatformUser(
        id=new_id("u"),
        username=username,
        password_hash=hash_password(settings.auth_admin_password),
        display_name=settings.auth_admin_display_name or "系统管理员",
        role="admin",
        title="系统管理员",
    )
    with PlatformSession() as session:
        session.add(user)
        session.commit()
