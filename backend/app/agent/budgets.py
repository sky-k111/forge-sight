"""Run 级资源预算：调用前预检、调用后结算、软阈值预警。"""

from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any

from app.config import settings
from app.db.models import AnalysisRun


class BudgetExceeded(RuntimeError):
    def __init__(self, budget_type: str, used: float, limit: float):
        super().__init__(f"{budget_type} 预算已耗尽")
        self.budget_type = budget_type
        self.used = used
        self.limit = limit


@dataclass(frozen=True)
class BudgetLimits:
    max_llm_calls: int
    max_retry_calls: int
    max_input_tokens: int
    max_output_tokens: int
    max_total_tokens: int
    max_estimated_cost: float
    max_duration_seconds: int


def default_budget() -> dict[str, Any]:
    return asdict(
        BudgetLimits(
            max_llm_calls=settings.agent_max_llm_calls,
            max_retry_calls=settings.agent_max_retry_calls,
            max_input_tokens=settings.agent_max_input_tokens,
            max_output_tokens=settings.agent_max_output_tokens,
            max_total_tokens=settings.agent_max_total_tokens,
            max_estimated_cost=settings.agent_max_estimated_cost,
            max_duration_seconds=settings.agent_max_duration_seconds,
        )
    )


def _usage(run: AnalysisRun) -> dict[str, float]:
    return {
        "max_llm_calls": float(run.llm_call_count or 0),
        "max_retry_calls": float(run.retry_call_count or 0),
        "max_input_tokens": float(run.input_tokens or 0),
        "max_output_tokens": float(run.output_tokens or 0),
        "max_total_tokens": float(run.total_tokens or 0),
        "max_estimated_cost": float(run.estimated_cost or Decimal("0")),
    }


def budget_status(run: AnalysisRun) -> dict[str, Any]:
    limits = run.budget_snapshot_json or default_budget()
    used = _usage(run)
    items = {}
    for key, value in used.items():
        limit = float(limits.get(key, 0) or 0)
        ratio = value / limit if limit > 0 else 0
        items[key] = {"used": value, "limit": limit, "ratio": ratio}
    return {
        "limits": limits,
        "items": items,
        "warning": any(item["ratio"] >= 0.8 for item in items.values()),
        "exceeded": any(item["limit"] > 0 and item["used"] >= item["limit"] for item in items.values()),
    }


def ensure_can_call(run: AnalysisRun, *, is_retry: bool = False) -> None:
    status = budget_status(run)
    keys = ["max_llm_calls", "max_input_tokens", "max_output_tokens", "max_total_tokens", "max_estimated_cost"]
    if is_retry:
        keys.insert(1, "max_retry_calls")
    for key in keys:
        item = status["items"][key]
        if item["limit"] > 0 and item["used"] >= item["limit"]:
            raise BudgetExceeded(key, item["used"], item["limit"])

