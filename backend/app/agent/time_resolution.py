"""相对时间约束的确定性解析。

把 intent 层产出的相对 timeConstraint（例如 kind=previous_month, reference=data_horizon）
解析为可写进 SQL 提示的绝对 periods。默认锚定到当前系统日期；若传入数据地平线
（数据源 stat_date 的最大值），则优先锚定到数据地平线——否则“上个月”会落到当前系统
日期的上月，而真实数据可能不到那里，导致结果为空。

纯函数，不触库；地平线由调用方（retrieval）探测后传入。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo


def _to_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        text = value.strip()[:10]
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(text, fmt).date()
            except (ValueError, IndexError):
                continue
    return None


def reference_date(horizon: Any = None) -> date:
    """解析相对时间的参考日期：优先数据地平线，否则取当前系统日期。"""
    if horizon:
        resolved = _to_date(horizon)
        if resolved:
            return resolved
    return datetime.now(ZoneInfo("Asia/Shanghai")).date()


def resolve_time_constraint(constraint: dict[str, Any] | None, horizon: Any = None) -> dict[str, Any] | None:
    """若约束为相对时间，返回带绝对 periods 的副本；否则原样返回。"""
    if not constraint:
        return None
    kind = constraint.get("kind")
    if constraint.get("reference") == "data_horizon" or kind in {
        "previous_month", "previous_week", "previous_quarter", "previous_year",
        "current_month", "current_week", "current_quarter", "current_year", "recent",
        "specific_month",
    }:
        return _resolve_relative(constraint, reference_date(horizon))
    # 已有 periods（如 current_vs_previous_month 由两段组成）直接放行
    return dict(constraint)


def _month_start(d: date) -> date:
    return d.replace(day=1)


def _resolve_relative(constraint: dict[str, Any], ref: date) -> dict[str, Any]:
    kind = constraint.get("kind")
    out: dict[str, Any] = dict(constraint)
    out.pop("periods", None)
    today = ref

    if kind == "previous_month":
        month_start = _month_start(today)
        prev_month_start = (month_start - timedelta(days=1)).replace(day=1)
        prev_month_label = f"{prev_month_start:%Y-%m}"
        out["periods"] = [{
            "key": "previous",
            "label": f"{prev_month_label}（上月）",
            "startInclusive": prev_month_start.isoformat(),
            "endExclusive": month_start.isoformat(),
        }]
    elif kind == "previous_week":
        week_start = today - timedelta(days=today.weekday())
        prev_week_start = week_start - timedelta(days=7)
        out["periods"] = [{
            "key": "previous",
            "label": "上周",
            "startInclusive": prev_week_start.isoformat(),
            "endExclusive": week_start.isoformat(),
        }]
    elif kind == "previous_quarter":
        quarter = (today.month - 1) // 3
        prev_q_start = date(today.year, quarter * 3 + 1, 1)
        prev_q_label = f"{prev_q_start:%Y-Q{(quarter + 1)}}"
        out["periods"] = [{
            "key": "previous",
            "label": prev_q_label,
            "startInclusive": prev_q_start.isoformat(),
            "endExclusive": date(today.year, (quarter + 1) * 3 + 1, 1).isoformat(),
        }]
    elif kind == "previous_year":
        prev_year = today.year - 1
        out["periods"] = [{
            "key": "previous",
            "label": f"{prev_year}（去年）",
            "startInclusive": f"{prev_year}-01-01",
            "endExclusive": f"{today.year}-01-01",
        }]
    elif kind == "current_month":
        out["periods"] = [{
            "key": "current",
            "label": f"{_month_start(today):%Y-%m}（本月至今）",
            "startInclusive": _month_start(today).isoformat(),
            "endExclusive": (today + timedelta(days=1)).isoformat(),
        }]
    elif kind == "current_week":
        week_start = today - timedelta(days=today.weekday())
        out["periods"] = [{
            "key": "current",
            "label": "本周",
            "startInclusive": week_start.isoformat(),
            "endExclusive": (week_start + timedelta(days=7)).isoformat(),
        }]
    elif kind == "current_quarter":
        quarter = (today.month - 1) // 3
        q_start = date(today.year, quarter * 3 + 1, 1)
        out["periods"] = [{
            "key": "current",
            "label": f"{q_start:%Y-Q{quarter + 1}}",
            "startInclusive": q_start.isoformat(),
            "endExclusive": date(today.year, (quarter + 1) * 3 + 1, 1).isoformat(),
        }]
    elif kind == "current_year":
        out["periods"] = [{
            "key": "current",
            "label": str(today.year),
            "startInclusive": f"{today.year}-01-01",
            "endExclusive": f"{today.year + 1}-01-01",
        }]
    elif kind == "specific_month":
        # 裸“6月份”锚定到参考日期（数据地平线优先）。规则：目标月份不晚于参考月用参考年，
        # 否则用参考年 - 1（即最近一个出现过该月的数据年份），从而覆盖跨年边界。
        month = int(constraint.get("month") or 1)
        month = max(1, min(12, month))
        year = today.year if month <= today.month else (today.year - 1)
        start = date(year, month, 1)
        end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
        out["periods"] = [{
            "key": "specific",
            "label": f"{year}-{month:02d}",
            "startInclusive": start.isoformat(),
            "endExclusive": end.isoformat(),
        }]
    elif kind == "recent":
        amount = int(constraint.get("amount") or 7)
        unit = constraint.get("unit") or "天"
        days = amount * (1 if unit in {"天", "日"} else 30 if unit in {"月", "个月"} else 7)
        out["periods"] = [{
            "key": "recent",
            "label": constraint.get("label", f"最近 {amount}{unit}"),
            "startInclusive": (today - timedelta(days=days)).isoformat(),
            "endExclusive": (today + timedelta(days=1)).isoformat(),
        }]
    return out
