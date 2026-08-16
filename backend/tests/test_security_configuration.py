"""验证 fail-closed 安全配置守卫（validate_security_configuration）。"""

import pytest

from app.auth.deps import validate_security_configuration
from app.config import settings


def _reset_insecure_defaults(monkeypatch):
    """非演示模式 + 全部不安全占位默认值，模拟「漏配 .env 的生产」。"""
    monkeypatch.setattr(settings, "demo_mode_enabled", False)
    monkeypatch.setattr(settings, "auth_token_secret", "dev-insecure-secret-change-me")
    monkeypatch.setattr(settings, "auth_admin_password", "admin123")
    monkeypatch.setattr(settings, "auth_email_gate_enabled", True)
    monkeypatch.setattr(settings, "email_code_secret", "")


def test_insecure_defaults_are_rejected_outside_demo(monkeypatch):
    _reset_insecure_defaults(monkeypatch)
    with pytest.raises(RuntimeError, match="AUTH_TOKEN_SECRET"):
        validate_security_configuration()


def test_change_me_placeholder_is_rejected(monkeypatch):
    _reset_insecure_defaults(monkeypatch)
    monkeypatch.setattr(settings, "auth_token_secret", "CHANGE_ME")
    monkeypatch.setattr(settings, "auth_admin_password", "CHANGE_ME")
    monkeypatch.setattr(settings, "email_code_secret", "CHANGE_ME")
    with pytest.raises(RuntimeError, match="AUTH_TOKEN_SECRET"):
        validate_security_configuration()


def test_demo_mode_exempts_insecure_defaults(monkeypatch):
    _reset_insecure_defaults(monkeypatch)
    monkeypatch.setattr(settings, "demo_mode_enabled", True)
    validate_security_configuration()  # 不应抛异常


def test_secure_configuration_passes(monkeypatch):
    _reset_insecure_defaults(monkeypatch)
    monkeypatch.setattr(settings, "auth_token_secret", "s" * 64)
    monkeypatch.setattr(settings, "auth_admin_password", "a-real-random-password")
    monkeypatch.setattr(settings, "email_code_secret", "e" * 64)
    validate_security_configuration()  # 不应抛异常


def test_empty_email_code_secret_only_rejected_when_gate_enabled(monkeypatch):
    _reset_insecure_defaults(monkeypatch)
    monkeypatch.setattr(settings, "auth_token_secret", "s" * 64)
    monkeypatch.setattr(settings, "auth_admin_password", "a-real-random-password")
    monkeypatch.setattr(settings, "email_code_secret", "")
    # 门禁开启 → 必须配置邮箱验证码密钥
    with pytest.raises(RuntimeError, match="EMAIL_CODE_SECRET"):
        validate_security_configuration()
    # 门禁关闭 → 邮箱验证码密钥非必需
    monkeypatch.setattr(settings, "auth_email_gate_enabled", False)
    validate_security_configuration()  # 不应抛异常
