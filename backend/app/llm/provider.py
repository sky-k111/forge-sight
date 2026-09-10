"""LLM provider 封装（OpenAI 兼容协议，默认 DeepSeek）。

红线：API Key 只存服务端 .env，不进入前端与对话上下文；
所有调用带超时，失败抛 LLMError 由上层降级处理，不在此处重试放大成本。
"""

from dataclasses import dataclass, field
from threading import Lock
from typing import Any

import httpx
from openai import OpenAI, OpenAIError

from app.config import settings


class LLMError(Exception):
    """LLM 调用失败（超时、限流、服务异常），上层应给出可理解的降级反馈。"""


@dataclass(frozen=True)
class LLMUsage:
    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    total_tokens: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LLMResponse:
    content: str
    model: str
    provider_request_id: str | None
    usage: LLMUsage


_client_lock = Lock()
_client_instance: OpenAI | None = None
_client_fingerprint: tuple[Any, ...] | None = None
_structured_support: dict[tuple[str, str], bool] = {}


def reset_client() -> None:
    """Close the pooled transport after a runtime profile switch."""
    global _client_instance, _client_fingerprint
    with _client_lock:
        if _client_instance is not None:
            try:
                _client_instance.close()
            except Exception:  # noqa: BLE001 - profile activation must not fail on transport cleanup
                pass
        _client_instance = None
        _client_fingerprint = None
        _structured_support.clear()


def _client() -> OpenAI:
    global _client_instance, _client_fingerprint
    if not settings.llm_api_key:
        raise LLMError("未配置 LLM_API_KEY")
    fingerprint = (settings.llm_base_url, settings.llm_api_key, settings.llm_timeout_seconds,
                   settings.llm_proxy_url or "")
    with _client_lock:
        if _client_instance is not None and _client_fingerprint == fingerprint:
            return _client_instance
        if _client_instance is not None:
            _client_instance.close()
        http_client = httpx.Client(proxy=settings.llm_proxy_url or None, trust_env=False,
                                   timeout=settings.llm_timeout_seconds)
        _client_instance = OpenAI(base_url=settings.llm_base_url, api_key=settings.llm_api_key,
                                  timeout=settings.llm_timeout_seconds, http_client=http_client,
                                  max_retries=0)
        _client_fingerprint = fingerprint
        return _client_instance


def _structured_format_unsupported(exc: Exception) -> bool:
    text = str(exc).lower()
    status = getattr(exc, "status_code", None)
    return status in {400, 404, 422} and any(
        token in text for token in ("response_format", "json_object", "structured output", "unsupported")
    )


def chat(system: str, user: str, *, temperature: float = 0.1, max_tokens: int = 2000) -> str:
    """单轮对话，返回文本。低温度默认：分析类任务要稳定不要发散。"""
    return chat_completion(system, user, temperature=temperature, max_tokens=max_tokens).content


def chat_completion(
    system: str,
    user: str,
    *,
    temperature: float = 0.1,
    max_tokens: int = 2000,
    structured: bool = False,
    timeout_seconds: float | None = None,
) -> LLMResponse:
    """返回内容及供应商确认的 usage；缺失分项保持 None，不做猜测。"""
    try:
        client = _client()
        capability_key = (settings.llm_base_url, settings.llm_model)
        use_json_mode = structured and _structured_support.get(capability_key, True)
        request = dict(
            model=settings.llm_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
            **({"timeout": max(0.1, float(timeout_seconds))} if timeout_seconds is not None else {}),
            **({"response_format": {"type": "json_object"}} if use_json_mode else {}),
        )
        try:
            response = client.chat.completions.create(**request)
            if use_json_mode:
                _structured_support[capability_key] = True
        except OpenAIError as exc:
            if not use_json_mode or not _structured_format_unsupported(exc):
                raise
            # An OpenAI-compatible endpoint may not implement response_format.
            # Cache the negotiated capability and retry once without that option;
            # the caller's schema validation remains authoritative.
            _structured_support[capability_key] = False
            request.pop("response_format", None)
            response = client.chat.completions.create(**request)
    except OpenAIError as exc:
        raise LLMError(f"LLM 调用失败：{type(exc).__name__}") from exc
    content = response.choices[0].message.content
    if not content:
        raise LLMError("LLM 返回空内容")
    usage = response.usage
    prompt_details = getattr(usage, "prompt_tokens_details", None) if usage else None
    completion_details = getattr(usage, "completion_tokens_details", None) if usage else None
    raw_usage = usage.model_dump(exclude_none=True) if usage and hasattr(usage, "model_dump") else {}
    return LLMResponse(
        content=content,
        model=getattr(response, "model", None) or settings.llm_model,
        provider_request_id=getattr(response, "id", None),
        usage=LLMUsage(
            input_tokens=getattr(usage, "prompt_tokens", None) if usage else None,
            cached_input_tokens=getattr(prompt_details, "cached_tokens", None),
            output_tokens=getattr(usage, "completion_tokens", None) if usage else None,
            reasoning_tokens=getattr(completion_details, "reasoning_tokens", None),
            total_tokens=getattr(usage, "total_tokens", None) if usage else None,
            raw=raw_usage,
        ),
    )
