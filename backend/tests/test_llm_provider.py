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


def test_runtime_profile_selects_model_without_mutating_global_settings(monkeypatch):
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
            usage=None, model=kwargs["model"], id="req-profile",
        )

    fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    captured_runtime = {}

    def fake_client(runtime=None):
        captured_runtime.update(runtime or {})
        return fake

    monkeypatch.setattr(provider, "_client", fake_client)
    monkeypatch.setattr(settings, "llm_model", "global-default", raising=False)

    result = provider.chat_completion(
        "system",
        "user",
        runtime={
            "baseUrl": "https://second-provider.example/v1",
            "apiKey": "secret-second",
            "model": "second-model",
        },
    )

    assert result.model == "second-model"
    assert calls[0]["model"] == "second-model"
    assert captured_runtime["apiKey"] == "secret-second"
    assert settings.llm_model == "global-default"


def test_anthropic_messages_adapter_normalizes_content_usage_and_headers(monkeypatch):
    calls = []

    class FakeAnthropicClient:
        def post(self, url, **kwargs):
            calls.append((url, kwargs))
            request = httpx.Request("POST", url)
            return httpx.Response(
                200,
                request=request,
                headers={"request-id": "req-anthropic"},
                json={
                    "id": "msg-1",
                    "model": "claude-sonnet-test",
                    "content": [
                        {"type": "text", "text": '{"status":"ok"}'},
                        {"type": "thinking", "thinking": "hidden"},
                    ],
                    "usage": {
                        "input_tokens": 120,
                        "cache_read_input_tokens": 40,
                        "output_tokens": 18,
                    },
                },
            )

    monkeypatch.setattr(provider, "_anthropic_client", lambda runtime=None: FakeAnthropicClient())
    result = provider.chat_completion(
        "system rules",
        "user question",
        structured=True,
        timeout_seconds=7.5,
        max_tokens=900,
        runtime={
            "protocol": "anthropic_messages",
            "authMode": "auto",
            "baseUrl": "https://api.anthropic.com",
            "apiKey": "anthropic-secret",
            "model": "claude-sonnet-test",
        },
    )

    url, request = calls[0]
    assert url == "https://api.anthropic.com/v1/messages"
    assert request["headers"]["x-api-key"] == "anthropic-secret"
    assert request["headers"]["anthropic-version"] == "2023-06-01"
    assert "authorization" not in request["headers"]
    assert request["json"]["model"] == "claude-sonnet-test"
    assert request["json"]["max_tokens"] == 900
    assert request["json"]["temperature"] == 0.1
    assert "JSON" in request["json"]["system"]
    assert request["timeout"] == 7.5
    assert result.content == '{"status":"ok"}'
    assert result.provider_request_id == "req-anthropic"
    assert result.usage.input_tokens == 120
    assert result.usage.cached_input_tokens == 40
    assert result.usage.output_tokens == 18
    assert result.usage.total_tokens == 138


def test_anthropic_messages_supports_bearer_gateways():
    headers = provider._anthropic_headers("gateway-secret", "bearer")

    assert headers["authorization"] == "Bearer gateway-secret"
    assert "x-api-key" not in headers


def test_unknown_protocol_is_rejected_before_network_access():
    try:
        provider.chat_completion(
            "system",
            "user",
            runtime={"protocol": "unknown", "apiKey": "secret", "model": "model"},
        )
    except provider.LLMError as exc:
        assert "不支持的 LLM 协议" in str(exc)
    else:
        raise AssertionError("unknown protocol must be rejected")
