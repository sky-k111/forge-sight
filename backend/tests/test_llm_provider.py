from types import SimpleNamespace

import httpx
from openai import BadRequestError

from app.config import settings
from app.llm import provider


class _FakeOpenAI:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.closed = False
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=lambda **_: None))
        self.__class__.instances.append(self)

    def close(self):
        self.closed = True


def test_client_is_pooled_and_reset_after_profile_change(monkeypatch):
    provider.reset_client()
    _FakeOpenAI.instances = []
    monkeypatch.setattr(provider, "OpenAI", _FakeOpenAI)
    monkeypatch.setattr(settings, "llm_api_key", "test-key", raising=False)
    monkeypatch.setattr(settings, "llm_base_url", "https://provider.example/v1", raising=False)
    monkeypatch.setattr(settings, "llm_timeout_seconds", 12, raising=False)
    monkeypatch.setattr(settings, "llm_proxy_url", "", raising=False)

    first = provider._client()
    assert provider._client() is first
    assert len(_FakeOpenAI.instances) == 1

    provider.reset_client()
    assert first.closed is True
    assert provider._client() is not first
    provider.reset_client()


def test_structured_output_negotiates_provider_capability(monkeypatch):
    provider._structured_support.clear()
    monkeypatch.setattr(settings, "llm_base_url", "https://provider.example/v1", raising=False)
    monkeypatch.setattr(settings, "llm_model", "model-a", raising=False)
    request = httpx.Request("POST", "https://provider.example/v1/chat/completions")
    response = httpx.Response(400, request=request)
    unsupported = BadRequestError(
        "response_format json_object unsupported",
        response=response,
        body={"error": {"message": "response_format is unsupported"}},
    )
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise unsupported
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok": true}'))],
            usage=None,
            model="model-a",
            id="req-1",
        )

    fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(provider, "_client", lambda: fake)

    result = provider.chat_completion("system", "user", structured=True)

    assert result.content == '{"ok": true}'
    assert calls[0]["response_format"] == {"type": "json_object"}
    assert "response_format" not in calls[1]
    assert provider._structured_support[(settings.llm_base_url, settings.llm_model)] is False


def test_per_call_timeout_is_forwarded_to_provider(monkeypatch):
    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
            usage=None, model="model-a", id="req-2",
        )
    fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(provider, "_client", lambda: fake)

    provider.chat_completion("system", "user", timeout_seconds=3.5)

    assert calls[0]["timeout"] == 3.5
