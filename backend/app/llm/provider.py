"""LLM provider 封装（OpenAI 兼容协议，默认 DeepSeek）。

红线：API Key 只存服务端 .env，不进入前端与对话上下文；
所有调用带超时，失败抛 LLMError 由上层降级处理，不在此处重试放大成本。
"""

from dataclasses import dataclass, field
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


def _client() -> OpenAI:
    if not settings.llm_api_key:
        raise LLMError("未配置 LLM_API_KEY")
    http_client = httpx.Client(
        proxy=settings.llm_proxy_url or None,
        trust_env=False,
        timeout=settings.llm_timeout_seconds,
    )
    return OpenAI(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        timeout=settings.llm_timeout_seconds,
        http_client=http_client,
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
) -> LLMResponse:
    """返回内容及供应商确认的 usage；缺失分项保持 None，不做猜测。"""
    try:
        response = _client().chat.completions.create(
            model=settings.llm_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        )
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
