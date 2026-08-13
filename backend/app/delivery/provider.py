"""Provider-independent mail values and SMTP implementation."""

from __future__ import annotations

import hashlib
import smtplib
import ssl
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from typing import Protocol


@dataclass(frozen=True)
class MailAttachment:
    filename: str
    content_type: str
    content: bytes = field(repr=False)


@dataclass(frozen=True)
class OutgoingMail:
    to: str
    subject: str
    text_body: str = field(repr=False)
    html_body: str = field(repr=False)
    idempotency_key: str = ""
    attachments: tuple[MailAttachment, ...] = field(default=(), repr=False)


@dataclass(frozen=True)
class ProviderResult:
    provider_message_id: str


class ProviderFailure(RuntimeError):
    def __init__(self, category: str, summary: str, *, retryable: bool):
        super().__init__(summary)
        self.category = category
        self.summary = summary
        self.retryable = retryable


class MailProvider(Protocol):
    def send(self, message: OutgoingMail) -> ProviderResult: ...


@dataclass
class CaptureMailProvider:
    messages: list[OutgoingMail] = field(default_factory=list, repr=False)

    def send(self, message: OutgoingMail) -> ProviderResult:
        self.messages.append(message)
        return ProviderResult(provider_message_id=f"capture-{len(self.messages)}")


def _safe_header(value: str, name: str) -> str:
    if "\r" in value or "\n" in value:
        raise ProviderFailure("invalid_message", f"{name} contains a line break", retryable=False)
    return value


@dataclass(frozen=True)
class SmtpConfig:
    host: str
    port: int
    username: str
    password: str = field(repr=False)
    from_address: str
    from_name: str = "ForgeSight"
    use_tls: bool = True
    timeout_seconds: int = 10

    def validate(self) -> None:
        if not self.host or not self.from_address:
            raise ProviderFailure("configuration", "SMTP host and sender are required", retryable=False)
        if not 1 <= self.port <= 65535:
            raise ProviderFailure("configuration", "SMTP port is invalid", retryable=False)
        _safe_header(self.from_address, "from address")
        _safe_header(self.from_name, "from name")


class SmtpMailProvider:
    """SMTP with implicit TLS on 465 and STARTTLS on other TLS ports."""

    def __init__(self, config: SmtpConfig):
        config.validate()
        self._config = config

    def _mime(self, mail: OutgoingMail) -> EmailMessage:
        message = EmailMessage()
        message["To"] = _safe_header(mail.to, "recipient")
        message["From"] = formataddr(
            (_safe_header(self._config.from_name, "from name"), self._config.from_address)
        )
        message["Subject"] = _safe_header(mail.subject, "subject")
        domain = self._config.from_address.partition("@")[2] or None
        if mail.idempotency_key:
            stable = hashlib.sha256(mail.idempotency_key.encode()).hexdigest()
            message["Message-ID"] = f"<{stable}@{domain or 'forgesight.local'}>"
        else:
            message["Message-ID"] = make_msgid(domain=domain)
        message.set_content(mail.text_body)
        message.add_alternative(mail.html_body, subtype="html")
        for item in mail.attachments:
            maintype, separator, subtype = item.content_type.partition("/")
            if not separator:
                raise ProviderFailure("attachment", "attachment content type is invalid", retryable=False)
            message.add_attachment(
                item.content,
                maintype=maintype,
                subtype=subtype,
                filename=_safe_header(item.filename, "attachment filename"),
            )
        return message

    def send(self, mail: OutgoingMail) -> ProviderResult:
        message = self._mime(mail)
        config = self._config
        context = ssl.create_default_context()
        try:
            smtp_class = smtplib.SMTP_SSL if config.use_tls and config.port == 465 else smtplib.SMTP
            kwargs = {"host": config.host, "port": config.port, "timeout": config.timeout_seconds}
            if smtp_class is smtplib.SMTP_SSL:
                kwargs["context"] = context
            with smtp_class(**kwargs) as client:
                if config.use_tls and config.port != 465:
                    client.starttls(context=context)
                if config.username:
                    client.login(config.username, config.password)
                client.send_message(message)
            return ProviderResult(provider_message_id=str(message["Message-ID"]))
        except smtplib.SMTPRecipientsRefused as exc:
            raise ProviderFailure("invalid_address", "SMTP rejected recipient", retryable=False) from exc
        except smtplib.SMTPAuthenticationError as exc:
            raise ProviderFailure("configuration", "SMTP authentication failed", retryable=False) from exc
        except smtplib.SMTPResponseException as exc:
            retryable = exc.smtp_code in {421, 450, 451, 452} or exc.smtp_code >= 500
            category = "rate_limited" if exc.smtp_code in {421, 450, 451, 452} else "provider_error"
            raise ProviderFailure(category, f"SMTP response {exc.smtp_code}", retryable=retryable) from exc
        except (TimeoutError, OSError, smtplib.SMTPException) as exc:
            raise ProviderFailure("network", type(exc).__name__, retryable=True) from exc


def provider_from_settings(settings) -> MailProvider:
    name = settings.mail_provider.strip().lower()
    if name == "capture":
        return CaptureMailProvider()
    if name == "smtp":
        return SmtpMailProvider(
            SmtpConfig(
                host=settings.smtp_host,
                port=settings.smtp_port,
                username=settings.smtp_username,
                password=settings.smtp_password,
                from_address=settings.mail_from_address,
                from_name=settings.mail_from_name,
                use_tls=settings.smtp_use_tls,
                timeout_seconds=settings.mail_timeout_seconds,
            )
        )
    raise ProviderFailure("configuration", f"Unsupported MAIL_PROVIDER: {name or '<empty>'}", retryable=False)
