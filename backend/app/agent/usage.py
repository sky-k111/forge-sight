"""LLM Call Attempt、价格快照、Token/费用聚合。"""

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import or_

from app.agent.budgets import BudgetExceeded, budget_status, ensure_can_call
from app.agent.event_store import append_event
from app.agent.ids import new_id
from app.db.models import AnalysisEvent, AnalysisRun, LlmCallAttempt, LlmModelPrice
from app.db.platform import PlatformSession


def _active_price(session, provider: str, model: str) -> LlmModelPrice | None:
    now = datetime.now()
    return (
        session.query(LlmModelPrice)
        .filter(
            LlmModelPrice.provider == provider,
            LlmModelPrice.model == model,
            LlmModelPrice.effective_from <= now,
            or_(LlmModelPrice.effective_to.is_(None), LlmModelPrice.effective_to > now),
        )
        .order_by(LlmModelPrice.effective_from.desc())
        .first()
    )


def _price_snapshot(price: LlmModelPrice | None) -> dict[str, Any]:
    if price is None:
        return {"configured": False}
    return {
        "configured": True,
        "provider": price.provider,
        "model": price.model,
        "currency": price.currency,
        "inputPrice": float(price.input_price),
        "cachedInputPrice": float(price.cached_input_price),
        "outputPrice": float(price.output_price),
        "reasoningPrice": float(price.reasoning_price),
        "unitTokens": price.unit_tokens,
        "version": price.version,
    }


def start_call(
    run_id: str,
    *,
    provider: str,
    model: str,
    step_key: str,
    step_id: str | None = None,
    task_id: str | None = None,
    retry_of_call_id: str | None = None,
    model_profile_id: str | None = None,
) -> str:
    with PlatformSession() as session:
        run = session.query(AnalysisRun).filter_by(id=run_id).with_for_update().first()
        if run is None:
            raise KeyError(f"Run {run_id} 不存在")
        try:
            ensure_can_call(run, is_retry=retry_of_call_id is not None)
        except BudgetExceeded as exc:
            append_event(
                session,
                run,
                "run.budget.exceeded",
                {
                    "errorCode": "RUN_BUDGET_EXCEEDED",
                    "budgetType": exc.budget_type,
                    "used": exc.used,
                    "limit": exc.limit,
                },
                task_id=task_id,
                step_key=step_key,
            )
            session.commit()
            raise
        step_scope = f"{run_id}:{task_id or 'run'}:{step_key}"
        attempt_no = session.query(LlmCallAttempt).filter_by(step_scope=step_scope).count() + 1
        call = LlmCallAttempt(
            id=new_id("call"),
            run_id=run_id,
            task_id=task_id,
            step_id=step_id,
            step_scope=step_scope,
            attempt=attempt_no,
            retry_of_call_id=retry_of_call_id,
            provider=provider,
            model=model,
            model_profile_id=model_profile_id,
            price_snapshot_json=_price_snapshot(_active_price(session, provider, model)),
        )
        session.add(call)
        append_event(
            session,
            run,
            "llm.call.started",
            {"callId": call.id, "provider": provider, "model": model, "isRetry": retry_of_call_id is not None},
            task_id=task_id,
            step_key=step_key,
            attempt=attempt_no,
            visibility="admin",
        )
        session.commit()
        return call.id


def _estimate_cost(call: LlmCallAttempt) -> Decimal:
    price = call.price_snapshot_json or {}
    if not price.get("configured"):
        return Decimal("0")
    unit = Decimal(str(price.get("unitTokens") or 1_000_000))
    normal_input = max(0, (call.input_tokens or 0) - (call.cached_input_tokens or 0))
    return (
        Decimal(normal_input) * Decimal(str(price.get("inputPrice") or 0))
        + Decimal(call.cached_input_tokens or 0) * Decimal(str(price.get("cachedInputPrice") or 0))
        + Decimal(call.output_tokens or 0) * Decimal(str(price.get("outputPrice") or 0))
        + Decimal(call.reasoning_tokens or 0) * Decimal(str(price.get("reasoningPrice") or 0))
    ) / unit


def finish_call(
    call_id: str,
    *,
    status: str,
    latency_ms: int,
    input_tokens: int | None = None,
    cached_input_tokens: int | None = None,
    output_tokens: int | None = None,
    reasoning_tokens: int | None = None,
    total_tokens: int | None = None,
    context_window: int | None = None,
    provider_request_id: str | None = None,
    error_code: str | None = None,
    usage_raw: dict[str, Any] | None = None,
) -> dict[str, Any]:
    with PlatformSession() as session:
        call = session.query(LlmCallAttempt).filter_by(id=call_id).with_for_update().first()
        if call is None:
            raise KeyError(f"LLM Call {call_id} 不存在")
        if call.status != "started":
            return _serialize_call(call)
        run = session.query(AnalysisRun).filter_by(id=call.run_id).with_for_update().one()
        call.status = status
        call.latency_ms = max(0, latency_ms)
        call.input_tokens = input_tokens
        call.cached_input_tokens = cached_input_tokens
        call.output_tokens = output_tokens
        call.reasoning_tokens = reasoning_tokens
        call.total_tokens = total_tokens if total_tokens is not None else (input_tokens or 0) + (output_tokens or 0)
        call.context_window = context_window
        call.context_remaining = (
            max(0, context_window - (call.total_tokens or 0)) if context_window is not None else None
        )
        call.provider_request_id = provider_request_id
        call.error_code = error_code
        call.usage_raw_json = usage_raw or {}
        call.estimated_cost = _estimate_cost(call)
        call.currency = str((call.price_snapshot_json or {}).get("currency") or run.currency)
        call.finished_at = datetime.now()

        run.llm_call_count += 1
        if call.retry_of_call_id:
            run.retry_call_count += 1
        run.input_tokens += call.input_tokens or 0
        run.output_tokens += call.output_tokens or 0
        run.total_tokens += call.total_tokens or 0
        if call.retry_of_call_id:
            run.retry_tokens += call.total_tokens or 0
        run.estimated_cost = Decimal(str(run.estimated_cost or 0)) + Decimal(str(call.estimated_cost or 0))
        run.currency = call.currency

        payload = _serialize_call(call)
        append_event(
            session,
            run,
            "llm.usage.recorded",
            payload,
            task_id=call.task_id,
            step_key=call.step_scope.rsplit(":", 1)[-1],
            attempt=call.attempt,
            visibility="user",
        )
        budget = budget_status(run)
        if budget["warning"]:
            exists = session.query(AnalysisEvent.id).filter_by(
                run_id=run.id, event_type="run.budget.warning"
            ).first()
            if exists is None:
                append_event(session, run, "run.budget.warning", budget, visibility="user")
        session.commit()
        payload["budget"] = budget
        return payload


def _serialize_call(call: LlmCallAttempt) -> dict[str, Any]:
    return {
        "callId": call.id,
        "provider": call.provider,
        "model": call.model,
        "attempt": call.attempt,
        "isRetry": bool(call.retry_of_call_id),
        "status": call.status,
        "inputTokens": call.input_tokens,
        "cachedInputTokens": call.cached_input_tokens,
        "outputTokens": call.output_tokens,
        "reasoningTokens": call.reasoning_tokens,
        "totalTokens": call.total_tokens,
        "contextWindow": call.context_window,
        "contextRemaining": call.context_remaining,
        "latencyMs": call.latency_ms,
        "estimatedCost": float(call.estimated_cost or 0),
        "currency": call.currency,
        "priceConfigured": bool((call.price_snapshot_json or {}).get("configured")),
        "errorCode": call.error_code,
    }


def run_usage(run_id: str, *, include_attempts: bool = False) -> dict[str, Any]:
    with PlatformSession() as session:
        run = session.query(AnalysisRun).filter_by(id=run_id).first()
        if run is None:
            raise KeyError(f"Run {run_id} 不存在")
        result = {
            "runId": run.id,
            "callCount": run.llm_call_count,
            "retryCallCount": run.retry_call_count,
            "inputTokens": run.input_tokens,
            "outputTokens": run.output_tokens,
            "totalTokens": run.total_tokens,
            "retryTokens": run.retry_tokens,
            "estimatedCost": float(run.estimated_cost or 0),
            "currency": run.currency,
            "budget": budget_status(run),
        }
        if include_attempts:
            attempts = session.query(LlmCallAttempt).filter_by(run_id=run_id).order_by(LlmCallAttempt.created_at).all()
            result["attempts"] = [_serialize_call(item) for item in attempts]
        return result
