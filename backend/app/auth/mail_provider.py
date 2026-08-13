"""Replaceable verification-mail provider boundary."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from app.config import settings
from app.delivery.provider import OutgoingMail, ProviderFailure, provider_from_settings


@dataclass(frozen=True)
class SendResult:
    ok: bool
    provider_message_id: str = ""
    error_category: str = ""
    error_summary: str = ""


class VerificationMailProvider(Protocol):
    def send_code(self, to: str, code: str, purpose: str) -> SendResult: ...


@dataclass(frozen=True)
class CapturedVerificationMail:
    to: str
    purpose: str
    code: str = field(repr=False)


@dataclass
class CaptureMailProvider:
    """Development/test provider; messages exist only in this object and are never logged."""

    messages: list[CapturedVerificationMail] = field(default_factory=list, repr=False)

    def send_code(self, to: str, code: str, purpose: str) -> SendResult:
        self.messages.append(CapturedVerificationMail(to=to, code=code, purpose=purpose))
        return SendResult(ok=True, provider_message_id=f"capture-{len(self.messages)}")


@dataclass
class SmtpVerificationMailProvider:
    """Use the shared SMTP transport while keeping verification's small contract."""

    provider: object = field(repr=False)

    def send_code(self, to: str, code: str, purpose: str) -> SendResult:
        labels = {"bind": "绑定邮箱", "verify": "验证邮箱", "change": "更换邮箱"}
        label = labels.get(purpose, "验证邮箱")
        subject = f"[ForgeSight] {label}验证码"
        text_body = f"您的验证码是：{code}\n\n验证码将在短时间内失效。如非本人操作，请忽略本邮件。"
        html_body = (
            '<div style="font-family:Arial,Microsoft YaHei,sans-serif;color:#17212b">'
            f"<p>您的验证码是：</p><p style=\"font-size:28px;font-weight:700;letter-spacing:4px\">{code}</p>"
            "<p>验证码将在短时间内失效。如非本人操作，请忽略本邮件。</p></div>"
        )
        try:
            result = self.provider.send(
                OutgoingMail(
                    to=to,
                    subject=subject,
                    idempotency_key=f"verification:{to}:{purpose}:{code}",
                    text_body=text_body,
                    html_body=html_body,
                )
            )
            return SendResult(ok=True, provider_message_id=result.provider_message_id)
        except ProviderFailure as exc:
            return SendResult(
                ok=False,
                error_category=exc.category,
                error_summary="邮件服务暂时不可用",
            )
        except Exception:
            return SendResult(
                ok=False,
                error_category="provider_error",
                error_summary="邮件服务暂时不可用",
            )


def get_verification_mail_provider() -> VerificationMailProvider:
    provider_name = settings.mail_provider.strip().lower()
    if provider_name == "capture":
        return CaptureMailProvider()
    if provider_name == "smtp":
        return SmtpVerificationMailProvider(provider=provider_from_settings(settings))
    raise RuntimeError(f"Unsupported MAIL_PROVIDER: {provider_name or '<empty>'}")
