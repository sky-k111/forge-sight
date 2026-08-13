"""E01: SMTP transport and MIME behavior without real network access."""

import smtplib

import pytest

from app.delivery.provider import (
    MailAttachment,
    OutgoingMail,
    ProviderFailure,
    SmtpConfig,
    SmtpMailProvider,
)


class _FakeSmtp:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.started_tls = False
        self.login_value = None
        self.message = None
        self.__class__.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def starttls(self, *, context):
        self.started_tls = context is not None

    def login(self, username, password):
        self.login_value = (username, password)

    def send_message(self, message):
        self.message = message


def test_starttls_smtp_builds_plain_html_and_pdf_mime(monkeypatch):
    import app.delivery.provider as module

    _FakeSmtp.instances.clear()
    monkeypatch.setattr(module.smtplib, "SMTP", _FakeSmtp)
    provider = SmtpMailProvider(
        SmtpConfig(
            host="smtp.example.test",
            port=587,
            username="sender@example.test",
            password="not-logged",
            from_address="sender@example.test",
            use_tls=True,
        )
    )

    result = provider.send(
        OutgoingMail(
            to="recipient@example.test",
            subject="周报",
            idempotency_key="email:message-1:user-1",
            text_body="纯文本",
            html_body="<p>HTML</p>",
            attachments=(MailAttachment("周报.pdf", "application/pdf", b"pdf"),),
        )
    )

    smtp = _FakeSmtp.instances[0]
    assert smtp.started_tls is True
    assert smtp.login_value == ("sender@example.test", "not-logged")
    assert smtp.message.get_body(preferencelist=("plain",)).get_content().strip() == "纯文本"
    assert smtp.message.get_body(preferencelist=("html",)).get_content().strip() == "<p>HTML</p>"
    attachment = next(smtp.message.iter_attachments())
    assert attachment.get_content_type() == "application/pdf"
    assert attachment.get_payload(decode=True) == b"pdf"
    assert result.provider_message_id.startswith("<")
    assert "@example.test>" in result.provider_message_id


@pytest.mark.parametrize(
    ("code", "retryable", "category"),
    [(421, True, "rate_limited"), (450, True, "rate_limited"), (550, False, "invalid_address"), (553, False, "invalid_address")],
)
def test_smtp_response_codes_classify_temporary_and_permanent_failures(
    monkeypatch, code, retryable, category
):
    import app.delivery.provider as module

    class RejectingSmtp(_FakeSmtp):
        def send_message(self, _message):
            raise smtplib.SMTPResponseException(code, b"provider detail")

    monkeypatch.setattr(module.smtplib, "SMTP", RejectingSmtp)
    provider = SmtpMailProvider(
        SmtpConfig(
            host="smtp.example.test",
            port=587,
            username="sender@example.test",
            password="secret",
            from_address="sender@example.test",
        )
    )

    with pytest.raises(ProviderFailure) as caught:
        provider.send(
            OutgoingMail(
                to="recipient@example.test",
                subject="test",
                text_body="text",
                html_body="<p>text</p>",
            )
        )

    assert caught.value.retryable is retryable
    assert caught.value.category == category


@pytest.mark.parametrize(
    ("code", "retryable", "category"),
    [(450, True, "rate_limited"), (550, False, "invalid_address")],
)
def test_recipient_refusal_uses_recipient_response_code(
    monkeypatch, code, retryable, category
):
    import app.delivery.provider as module

    class RejectingRecipientSmtp(_FakeSmtp):
        def send_message(self, _message):
            raise smtplib.SMTPRecipientsRefused(
                {"recipient@example.test": (code, b"provider detail")}
            )

    monkeypatch.setattr(module.smtplib, "SMTP", RejectingRecipientSmtp)
    provider = SmtpMailProvider(
        SmtpConfig(
            host="smtp.example.test",
            port=587,
            username="sender@example.test",
            password="secret",
            from_address="sender@example.test",
        )
    )

    with pytest.raises(ProviderFailure) as caught:
        provider.send(
            OutgoingMail(
                to="recipient@example.test",
                subject="test",
                text_body="text",
                html_body="<p>text</p>",
            )
        )

    assert caught.value.retryable is retryable
    assert caught.value.category == category


def test_smtp_health_check_classifies_authentication_failure(monkeypatch):
    import app.delivery.provider as module

    class AuthenticationFailureSmtp(_FakeSmtp):
        def login(self, _username, _password):
            raise smtplib.SMTPAuthenticationError(535, b"credentials rejected")

    monkeypatch.setattr(module.smtplib, "SMTP", AuthenticationFailureSmtp)
    provider = SmtpMailProvider(
        SmtpConfig(
            host="smtp.example.test",
            port=587,
            username="sender@example.test",
            password="secret",
            from_address="sender@example.test",
        )
    )

    with pytest.raises(ProviderFailure) as caught:
        provider.verify_configuration()

    assert caught.value.category == "configuration"
    assert caught.value.retryable is False
