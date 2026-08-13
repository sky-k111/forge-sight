"""邮箱验证码挑战的生成、频控与原子消费。"""

from __future__ import annotations

import hashlib
import hmac
import math
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy.exc import IntegrityError

from app.agent.ids import new_id
from app.config import settings
from app.db.models import (
    EmailVerificationChallenge,
    EmailVerificationEvent,
    PlatformUser,
)
from app.db.platform import PlatformSession

if TYPE_CHECKING:
    from app.auth.mail_provider import VerificationMailProvider


EMAIL_RE = re.compile(
    r"^(?P<local>[A-Za-z0-9!#$%&'*+/=?^_`{|}~.-]+)@"
    r"(?P<domain>(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
    r"[A-Za-z]{2,63})$"
)
PURPOSES = {"bind", "verify", "change"}
SEND_EVENT_TYPES = ("code_sent", "send_failed")


ERROR_MESSAGES = {
    "AUTH_INVALID_EMAIL_FORMAT": "邮箱格式不正确",
    "EMAIL_TAKEN": "该邮箱已被其他账号绑定",
    "EMAIL_ALREADY_VERIFIED": "该邮箱已完成验证",
    "CHALLENGE_RESEND_COOLDOWN": "验证码发送过于频繁，请稍后再试",
    "CHALLENGE_RATE_LIMITED": "操作过于频繁，请稍后再试",
    "CHALLENGE_NOT_FOUND": "验证请求不存在或已失效",
    "EMAIL_SEND_FAILED": "验证邮件发送失败，请稍后重试",
    "CODE_INVALID": "验证码不正确",
    "CODE_EXPIRED": "验证码已过期，请重新获取",
    "CODE_ATTEMPTS_EXCEEDED": "错误次数过多，请重新获取验证码",
    "USER_NOT_FOUND": "用户不存在",
}


class EmailVerificationError(ValueError):
    """可由后续 API 层稳定映射为 HTTP 状态和 errorCode。"""

    def __init__(self, code: str, *, retry_after_seconds: int | None = None):
        super().__init__(ERROR_MESSAGES[code])
        self.code = code
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True)
class IssuedEmailChallenge:
    challenge_id: str
    email_masked: str
    purpose: str
    retry_after_seconds: int
    code: str = field(repr=False)


def normalize_email(raw: str) -> str:
    """返回可投递邮箱：保留本地部分大小写，仅将域名转为小写。"""
    value = str(raw or "").strip()
    try:
        value.encode("ascii")
    except UnicodeEncodeError as exc:
        raise EmailVerificationError("AUTH_INVALID_EMAIL_FORMAT") from exc
    if len(value) > 254:
        raise EmailVerificationError("AUTH_INVALID_EMAIL_FORMAT")
    match = EMAIL_RE.fullmatch(value)
    if not match:
        raise EmailVerificationError("AUTH_INVALID_EMAIL_FORMAT")
    local = match.group("local")
    if len(local) > 64 or local.startswith(".") or local.endswith(".") or ".." in local:
        raise EmailVerificationError("AUTH_INVALID_EMAIL_FORMAT")
    return f"{local}@{match.group('domain').lower()}"


def canonicalize_email(deliverable: str) -> str:
    return deliverable.lower()


def mask_email(deliverable: str) -> str:
    local, domain = deliverable.rsplit("@", 1)
    masked_local = f"{local[0]}***" if len(local) >= 2 else "*"
    return f"{masked_local}@{domain}"


def generate_verification_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def _hash_code(code: str, challenge_id: str, email_canonical: str) -> str:
    secret = settings.email_code_secret
    if not secret:
        raise RuntimeError("EMAIL_CODE_SECRET must be configured before issuing email codes")
    message = f"{code}:{challenge_id}:{email_canonical}".encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def _event(
    *,
    user_id: str,
    email_canonical: str,
    purpose: str,
    challenge_id: str | None,
    event_type: str,
    outcome: str,
    ip: str,
    now: datetime,
) -> EmailVerificationEvent:
    return EmailVerificationEvent(
        id=new_id("e"),
        user_id=user_id,
        email_canonical=email_canonical,
        purpose=purpose,
        challenge_id=challenge_id,
        event_type=event_type,
        outcome=outcome,
        ip=(ip or "")[:45],
        created_at=now,
    )


def _send_limit_reached(session, user_id: str, email_canonical: str, ip: str, now: datetime) -> bool:
    cutoff = now - timedelta(hours=1)
    base = session.query(EmailVerificationEvent).filter(
        EmailVerificationEvent.created_at > cutoff,
        EmailVerificationEvent.event_type.in_(SEND_EVENT_TYPES),
    )
    account_count = base.filter(EmailVerificationEvent.user_id == user_id).count()
    email_count = base.filter(
        EmailVerificationEvent.email_canonical == email_canonical
    ).count()
    ip_count = (
        session.query(EmailVerificationEvent)
        .filter(
            EmailVerificationEvent.created_at > cutoff,
            EmailVerificationEvent.ip == (ip or "")[:45],
        )
        .count()
    )
    return (
        account_count >= settings.email_hourly_limit_account
        or email_count >= settings.email_hourly_limit_email
        or ip_count >= settings.email_hourly_limit_ip
    )


def issue_email_challenge(
    user_id: str,
    email: str,
    purpose: str,
    ip: str,
    *,
    now: datetime | None = None,
    session_factory=None,
    provider: VerificationMailProvider | None = None,
) -> IssuedEmailChallenge:
    """创建或刷新活动挑战；返回的明文验证码仅供后续邮件 Provider 使用。"""
    if purpose not in PURPOSES:
        raise ValueError(f"Unsupported email verification purpose: {purpose}")
    deliverable = normalize_email(email)
    canonical = canonicalize_email(deliverable)
    current_time = now or datetime.now()
    factory = session_factory or PlatformSession

    with factory() as session:
        user = (
            session.query(PlatformUser)
            .filter(PlatformUser.id == user_id)
            .with_for_update()
            .first()
        )
        if user is None:
            raise EmailVerificationError("USER_NOT_FOUND")
        occupied = (
            session.query(PlatformUser.id)
            .filter(
                PlatformUser.email_canonical == canonical,
                PlatformUser.id != user_id,
            )
            .first()
        )
        if occupied:
            raise EmailVerificationError("EMAIL_TAKEN")
        if user.email_verified_at is not None and user.email_canonical == canonical:
            raise EmailVerificationError("EMAIL_ALREADY_VERIFIED")

        active = (
            session.query(EmailVerificationChallenge)
            .filter(
                EmailVerificationChallenge.user_id == user_id,
                EmailVerificationChallenge.consumed_at.is_(None),
                EmailVerificationChallenge.superseded_at.is_(None),
            )
            .with_for_update()
            .first()
        )
        if _send_limit_reached(session, user_id, canonical, ip, current_time):
            session.add(
                _event(
                    user_id=user_id,
                    email_canonical=canonical,
                    purpose=purpose,
                    challenge_id=active.id if active else None,
                    event_type="send_failed",
                    outcome="rate_limited",
                    ip=ip,
                    now=current_time,
                )
            )
            session.commit()
            raise EmailVerificationError("CHALLENGE_RATE_LIMITED")

        if active and active.email_canonical == canonical:
            elapsed = max(0, (current_time - active.last_sent_at).total_seconds())
            if elapsed < settings.email_code_resend_seconds:
                retry_after = math.ceil(settings.email_code_resend_seconds - elapsed)
                session.add(
                    _event(
                        user_id=user_id,
                        email_canonical=canonical,
                        purpose=active.purpose,
                        challenge_id=active.id,
                        event_type="send_failed",
                        outcome="cooldown",
                        ip=ip,
                        now=current_time,
                    )
                )
                session.commit()
                raise EmailVerificationError(
                    "CHALLENGE_RESEND_COOLDOWN", retry_after_seconds=retry_after
                )
            challenge = active
            is_resend = True
        else:
            if active:
                active.superseded_at = current_time
            challenge = EmailVerificationChallenge(
                id=new_id("c"),
                user_id=user_id,
                purpose=purpose,
                email=deliverable,
                email_canonical=canonical,
                code_hash="pending",
                expires_at=current_time,
                attempt_count=0,
                send_count=1,
                last_sent_at=current_time,
                created_at=current_time,
            )
            session.add(challenge)
            is_resend = False

        code = generate_verification_code()
        code_hash = _hash_code(code, challenge.id, canonical)
        send_result = provider.send_code(deliverable, code, purpose) if provider else None
        if send_result is not None and not send_result.ok:
            if is_resend:
                challenge.last_sent_at = current_time
            else:
                challenge.code_hash = code_hash
                challenge.expires_at = current_time + timedelta(
                    seconds=settings.email_code_ttl_seconds
                )
                challenge.send_count = 0
            session.add(
                _event(
                    user_id=user_id,
                    email_canonical=canonical,
                    purpose=purpose,
                    challenge_id=challenge.id,
                    event_type="send_failed",
                    outcome="provider_error",
                    ip=ip,
                    now=current_time,
                )
            )
            session.commit()
            raise EmailVerificationError("EMAIL_SEND_FAILED")

        challenge.purpose = purpose
        challenge.email = deliverable
        challenge.code_hash = code_hash
        challenge.expires_at = current_time + timedelta(seconds=settings.email_code_ttl_seconds)
        challenge.last_sent_at = current_time
        challenge.attempt_count = 0
        if is_resend:
            challenge.send_count += 1
        session.add(
            _event(
                user_id=user_id,
                email_canonical=canonical,
                purpose=purpose,
                challenge_id=challenge.id,
                event_type="code_sent",
                outcome="sent",
                ip=ip,
                now=current_time,
            )
        )
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise EmailVerificationError("CHALLENGE_RATE_LIMITED") from exc

    return IssuedEmailChallenge(
        challenge_id=challenge.id,
        code=code,
        email_masked=mask_email(deliverable),
        purpose=purpose,
        retry_after_seconds=settings.email_code_resend_seconds,
    )


def _verification_ip_limited(session, ip: str, now: datetime) -> bool:
    cutoff = now - timedelta(hours=1)
    count = (
        session.query(EmailVerificationEvent)
        .filter(
            EmailVerificationEvent.created_at > cutoff,
            EmailVerificationEvent.ip == (ip or "")[:45],
        )
        .count()
    )
    return count >= settings.email_hourly_limit_ip


def verify_email_challenge(
    user_id: str,
    email: str,
    code: str,
    ip: str,
    *,
    now: datetime | None = None,
    session_factory=None,
) -> PlatformUser:
    """验证并单次消费挑战，同时原子更新用户邮箱。"""
    deliverable = normalize_email(email)
    canonical = canonicalize_email(deliverable)
    current_time = now or datetime.now()
    factory = session_factory or PlatformSession

    with factory() as session:
        user = (
            session.query(PlatformUser)
            .filter(PlatformUser.id == user_id)
            .with_for_update()
            .first()
        )
        if user is None:
            raise EmailVerificationError("USER_NOT_FOUND")
        challenge = (
            session.query(EmailVerificationChallenge)
            .filter(
                EmailVerificationChallenge.user_id == user_id,
                EmailVerificationChallenge.email_canonical == canonical,
            )
            .order_by(EmailVerificationChallenge.created_at.desc())
            .with_for_update()
            .first()
        )
        purpose = challenge.purpose if challenge else "verify"
        challenge_id = challenge.id if challenge else None

        if _verification_ip_limited(session, ip, current_time):
            session.add(
                _event(
                    user_id=user_id,
                    email_canonical=canonical,
                    purpose=purpose,
                    challenge_id=challenge_id,
                    event_type="verify_attempt",
                    outcome="rate_limited",
                    ip=ip,
                    now=current_time,
                )
            )
            session.commit()
            raise EmailVerificationError("CHALLENGE_RATE_LIMITED")
        if challenge is None or challenge.superseded_at is not None:
            session.add(
                _event(
                    user_id=user_id,
                    email_canonical=canonical,
                    purpose=purpose,
                    challenge_id=challenge_id,
                    event_type="verify_attempt",
                    outcome="not_found",
                    ip=ip,
                    now=current_time,
                )
            )
            session.commit()
            raise EmailVerificationError("CHALLENGE_NOT_FOUND")
        if challenge.consumed_at is not None or challenge.code_hash is None:
            outcome = "consumed"
            error_code = "CODE_INVALID"
        elif challenge.attempt_count >= settings.email_code_max_attempts:
            outcome = "attempts_exceeded"
            error_code = "CODE_ATTEMPTS_EXCEEDED"
        elif challenge.expires_at <= current_time:
            outcome = "expired"
            error_code = "CODE_EXPIRED"
        else:
            candidate_hash = _hash_code(str(code or ""), challenge.id, canonical)
            if not hmac.compare_digest(challenge.code_hash, candidate_hash):
                challenge.attempt_count += 1
                exceeded = challenge.attempt_count >= settings.email_code_max_attempts
                outcome = "attempts_exceeded" if exceeded else "invalid_code"
                error_code = "CODE_ATTEMPTS_EXCEEDED" if exceeded else "CODE_INVALID"
            else:
                outcome = ""
                error_code = ""

        if error_code:
            session.add(
                _event(
                    user_id=user_id,
                    email_canonical=canonical,
                    purpose=purpose,
                    challenge_id=challenge.id,
                    event_type="verify_attempt",
                    outcome=outcome,
                    ip=ip,
                    now=current_time,
                )
            )
            session.commit()
            raise EmailVerificationError(error_code)

        occupied = (
            session.query(PlatformUser.id)
            .filter(
                PlatformUser.email_canonical == canonical,
                PlatformUser.id != user_id,
            )
            .first()
        )
        if occupied:
            session.add(
                _event(
                    user_id=user_id,
                    email_canonical=canonical,
                    purpose=purpose,
                    challenge_id=challenge.id,
                    event_type="verify_attempt",
                    outcome="email_taken",
                    ip=ip,
                    now=current_time,
                )
            )
            session.commit()
            raise EmailVerificationError("EMAIL_TAKEN")

        user.email = challenge.email
        user.email_canonical = challenge.email_canonical
        user.email_verified_at = current_time
        challenge.consumed_at = current_time
        challenge.code_hash = None
        session.add(
            _event(
                user_id=user_id,
                email_canonical=canonical,
                purpose=purpose,
                challenge_id=challenge.id,
                event_type="verify_success",
                outcome="success",
                ip=ip,
                now=current_time,
            )
        )
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise EmailVerificationError("EMAIL_TAKEN") from exc
        return user
