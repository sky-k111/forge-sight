"""LLM provider 协议适配层（OpenAI Chat Completions / Anthropic Messages）。

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
_client_instances: dict[tuple[Any, ...], OpenAI] = {}
_anthropic_client_instances: dict[tuple[Any, ...], httpx.Client] = {}
_structured_support: dict[tuple[str, str], bool] = {}


def reset_client() -> None:
    """Close all pooled transports after model-profile configuration changes."""
    with _client_lock:
        for client in _client_instances.values():
            try:
                client.close()
            except Exception:  # noqa: BLE001 - profile activation must not fail on transport cleanup
                pass
        for client in _anthropic_client_instances.values():
            try:
                client.close()
            except Exception:  # noqa: BLE001 - transport cleanup must remain best effort
                pass
        _client_instances.clear()
        _anthropic_client_instances.clear()
        _structured_support.clear()


def _runtime_values(runtime: dict[str, Any] | None = None) -> tuple[str, str, str, float, str]:
    runtime = runtime or {}
    return (
        str(runtime.get("baseUrl") or settings.llm_base_url),
        str(runtime.get("apiKey") or settings.llm_api_key),
        str(runtime.get("model") or settings.llm_model),
        float(runtime.get("timeoutSeconds") or settings.llm_timeout_seconds),
        str(runtime.get("proxyUrl") or settings.llm_proxy_url or ""),
    )


def _client(runtime: dict[str, Any] | None = None) -> OpenAI:
    base_url, api_key, _model, timeout_seconds, proxy_url = _runtime_values(runtime)
    if not api_key:
        raise LLMError("未配置 LLM_API_KEY")
    fingerprint = (base_url, api_key, timeout_seconds, proxy_url)
    with _client_lock:
        existing = _client_instances.get(fingerprint)
        if existing is not None:
            return existing
        http_client = httpx.Client(proxy=proxy_url or None, trust_env=False, timeout=timeout_seconds)
        client = OpenAI(base_url=base_url, api_key=api_key, timeout=timeout_seconds,
                        http_client=http_client, max_retries=0)
        _client_instances[fingerprint] = client
        return client


def _anthropic_client(runtime: dict[str, Any] | None = None) -> httpx.Client:
    base_url, api_key, _model, timeout_seconds, proxy_url = _runtime_values(runtime)
    if not api_key:
        raise LLMError("未配置 LLM_API_KEY")
    fingerprint = (base_url, api_key, timeout_seconds, proxy_url)
    with _client_lock:
        existing = _anthropic_client_instances.get(fingerprint)
        if existing is not None:
            return existing
        client = httpx.Client(proxy=proxy_url or None, trust_env=False, timeout=timeout_seconds)
        _anthropic_client_instances[fingerprint] = client
        return client


def _anthropic_endpoint(base_url: str, resource: str) -> str:
    normalized = base_url.rstrip("/")
    if normalized.endswith(f"/{resource}"):
        return normalized
    if normalized.endswith("/v1"):
        return f"{normalized}/{resource}"
    return f"{normalized}/v1/{resource}"


def _anthropic_headers(api_key: str, auth_mode: str) -> dict[str, str]:
    selected = auth_mode if auth_mode in {"bearer", "x_api_key"} else "x_api_key"
    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "anthropic-version": "2023-06-01",
    }
    if selected == "bearer":
        headers["authorization"] = f"Bearer {api_key}"
    else:
        headers["x-api-key"] = api_key
    return headers


def _anthropic_error(response: httpx.Response) -> LLMError:
    status = response.status_code
    if status in {401, 403}:
        return LLMError("Anthropic 模型服务拒绝了当前 Key")
    if status == 429:
        return LLMError("Anthropic 模型服务请求过于频繁")
    if status >= 500:
        return LLMError(f"Anthropic 模型服务暂时不可用（HTTP {status}）")
    return LLMError(f"Anthropic 请求未通过校验（HTTP {status}）")


def _anthropic_messages_completion(
    system: str,
    user: str,
    *,
    temperature: float,
    max_tokens: int,
    structured: bool,
    timeout_seconds: float | None,
    runtime: dict[str, Any],
) -> LLMResponse:
    base_url, api_key, model, _default_timeout, _proxy_url = _runtime_values(runtime)
    if not api_key:
        raise LLMError("未配置 LLM_API_KEY")
    protocol_system = system
    if structured:
        protocol_system = f"{system.rstrip()}\n\n请只返回一个可被标准 JSON 解析器解析的 JSON 对象，不要包含 Markdown 代码块。"
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": max(0.0, min(1.0, float(temperature))),
        "system": protocol_system,
        "messages": [{"role": "user", "content": user}],
    }
    try:
        response = _anthropic_client(runtime).post(
            _anthropic_endpoint(base_url, "messages"),
            headers=_anthropic_headers(api_key, str(runtime.get("authMode") or "auto")),
            json=payload,
            **({"timeout": max(0.1, float(timeout_seconds))} if timeout_seconds is not None else {}),
        )
    except httpx.TimeoutException as exc:
        raise LLMError("Anthropic 模型调用超时") from exc
    except httpx.RequestError as exc:
        raise LLMError("Anthropic 模型服务无法连接") from exc
    if response.status_code >= 400:
        raise _anthropic_error(response)
    try:
        body = response.json()
    except ValueError as exc:
        raise LLMError("Anthropic 模型返回了无效 JSON") from exc
    blocks = body.get("content") if isinstance(body, dict) else None
    if isinstance(blocks, str):
        content = blocks.strip()
    else:
        content = "\n".join(
            str(block.get("text") or "").strip()
            for block in blocks if isinstance(block, dict) and block.get("type") == "text"
        ).strip() if isinstance(blocks, list) else ""
    if not content:
        raise LLMError("LLM 返回空内容")
    raw_usage = body.get("usage") if isinstance(body, dict) and isinstance(body.get("usage"), dict) else {}
    input_tokens = raw_usage.get("input_tokens")
    output_tokens = raw_usage.get("output_tokens")
    total_tokens = (
        int(input_tokens) + int(output_tokens)
        if isinstance(input_tokens, int) and isinstance(output_tokens, int) else None
    )
    request_id = response.headers.get("request-id") or response.headers.get("x-request-id") or (body.get("id") if isinstance(body, dict) else None)
    return LLMResponse(
        content=content,
        model=str((body.get("model") if isinstance(body, dict) else None) or model),
        provider_request_id=str(request_id) if request_id else None,
        usage=LLMUsage(
            input_tokens=input_tokens if isinstance(input_tokens, int) else None,
            cached_input_tokens=(raw_usage.get("cache_read_input_tokens")
                                 if isinstance(raw_usage.get("cache_read_input_tokens"), int) else None),
            output_tokens=output_tokens if isinstance(output_tokens, int) else None,
            total_tokens=total_tokens,
            raw=raw_usage,
        ),
    )


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
    runtime: dict[str, Any] | None = None,
) -> LLMResponse:
    """返回内容及供应商确认的 usage；缺失分项保持 None，不做猜测。"""
    protocol = str((runtime or {}).get("protocol") or settings.llm_protocol or "openai_compatible")
    if protocol == "anthropic_messages":
        effective_runtime = runtime or {
            "baseUrl": settings.llm_base_url,
            "apiKey": settings.llm_api_key,
            "model": settings.llm_model,
            "protocol": settings.llm_protocol,
            "authMode": settings.llm_auth_mode,
        }
        return _anthropic_messages_completion(
            system,
            user,
            temperature=temperature,
            max_tokens=max_tokens,
            structured=structured,
            timeout_seconds=timeout_seconds,
            runtime=effective_runtime,
        )
    if protocol != "openai_compatible":
        raise LLMError(f"不支持的 LLM 协议：{protocol}")
    try:
        base_url, _api_key, model, _default_timeout, _proxy_url = _runtime_values(runtime)
        client = _client(runtime) if runtime else _client()
        capability_key = (base_url, model)
        use_json_mode = structured and _structured_support.get(capability_key, True)
        request = dict(
            model=model,
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
        model=getattr(response, "model", None) or model,
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
