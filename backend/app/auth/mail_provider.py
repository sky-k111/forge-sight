"""Replaceable verification-mail provider boundary."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from app.config import settings


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


def get_verification_mail_provider() -> VerificationMailProvider:
    provider_name = settings.mail_provider.strip().lower()
    if provider_name == "capture":
        return CaptureMailProvider()
    raise RuntimeError(f"Unsupported MAIL_PROVIDER: {provider_name or '<empty>'}")
