"""相对时间约束的确定性解析。

把 intent 层产出的相对 timeConstraint（例如 kind=previous_month, reference=data_horizon）
解析为可写进 SQL 提示的绝对 periods。默认锚定到当前系统日期；若传入数据地平线
（数据源 stat_date 的最大值），则优先锚定到数据地平线——否则“上个月”会落到当前系统
日期的上月，而真实数据可能不到那里，导致结果为空。

纯函数，不触库；地平线由调用方（retrieval）探测后传入。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
import re
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
    # A month label is a semantic calendar period. Canonicalize its boundaries
    # here so an LLM-provided inclusive month-end (06-30) cannot leak into an
    # end-exclusive SQL contract. Explicit day ranges remain authoritative.
    if kind == "absolute":
        label = str(constraint.get("label") or "")
        periods = constraint.get("periods") or []
        if periods and not label:
            label = str(periods[0].get("label") or "")
        # An explicit range may span several calendar months. Do not collapse
        # it to the first month merely because the label begins with YYYY-MM.
        # Normalize a month-end supplied as an inclusive date to our exclusive
        # first-day boundary, while preserving the complete range.
        if len(periods) == 1:
            supplied = periods[0]
            start = _to_date(supplied.get("startInclusive"))
            end = _to_date(supplied.get("endExclusive"))
            if start and end:
                next_month = date(start.year + 1, 1, 1) if start.month == 12 else date(start.year, start.month + 1, 1)
                if end > next_month:
                    if end.day != 1:
                        end = date(end.year + 1, 1, 1) if end.month == 12 else date(end.year, end.month + 1, 1)
                    out = dict(constraint)
                    out["periods"] = [{
                        **supplied,
                        "key": supplied.get("key") or "range",
                        "startInclusive": start.isoformat(),
                        "endExclusive": end.isoformat(),
                    }]
                    return out
        year_month = _calendar_month(label, constraint, horizon)
        if year_month:
            year, month = year_month
            month_start = date(year, month, 1)
            month_end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
            ordinal = _week_of_month(label)
            if ordinal is not None:
                start = month_start + timedelta(days=(ordinal - 1) * 7)
                end = min(start + timedelta(days=7), month_end)
                if start >= month_end:
                    return dict(constraint)
                key = f"month-week-{ordinal}"
                resolved_label = label or f"{year}-{month:02d}第{ordinal}周"
            else:
                start, end = month_start, month_end
                key = "month"
                resolved_label = f"{year}-{month:02d}"
            out = dict(constraint)
            out["periods"] = [{
                "key": key, "label": resolved_label,
                "startInclusive": start.isoformat(), "endExclusive": end.isoformat(),
            }]
            return out
    # Fully specified non-month boundaries are authoritative.
    if kind == "absolute" and constraint.get("periods"):
        return dict(constraint)
    resolvable = {
        "previous_month", "previous_week", "previous_quarter", "previous_year",
        "current_month", "current_week", "current_quarter", "current_year", "recent",
        "specific_month", "specific_year", "comparison",
    }
    if kind in resolvable:
        ref = reference_date() if constraint.get("reference") == "now" else reference_date(horizon)
        return _resolve_relative(constraint, ref, horizon=horizon)
    return dict(constraint)


_CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "半": 1}


def _amount(value: Any) -> int:
    """支持 "最近一个月/两周/三天" 里的中文数字（一~十/两/半），否则回退默认 7。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        pass
    text = str(value or "").strip()
    if text in _CN_NUM:
        return _CN_NUM[text]
    return 7


def _month_start(d: date) -> date:
    return d.replace(day=1)


def _shift_months(value: date, months: int) -> date:
    index = value.year * 12 + value.month - 1 + months
    year, month_index = divmod(index, 12)
    month = month_index + 1
    next_month = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    last_day = (next_month - timedelta(days=1)).day
    return date(year, month, min(value.day, last_day))


def _calendar_month(label: str, constraint: dict[str, Any], horizon: Any = None) -> tuple[int, int] | None:
    """Resolve a recognized calendar-month label without interpreting user intent."""
    # A day label also begins with a year/month pair.  Do not widen an explicit
    # day into its whole month merely because a provider emitted a stale
    # ``timeGrain=month`` alongside otherwise authoritative day boundaries.
    if re.search(r"\d{4}\s*(?:年|[-/])\s*\d{1,2}\s*(?:月|[-/])\s*\d{1,2}\s*(?:日)?", label):
        return None
    explicit = re.search(r"(?<!\d)(\d{4})\s*(?:年|[-/])\s*(\d{1,2})(?:\s*月)?", label)
    if explicit:
        year, month = int(explicit.group(1)), int(explicit.group(2))
        return (year, month) if 1 <= month <= 12 else None
    bare = re.search(r"(?<!\d)(\d{1,2})\s*月份?(?!\d)", label)
    if not bare:
        return None
    month = int(bare.group(1))
    if not 1 <= month <= 12:
        return None
    use_now = constraint.get("reference") == "now"
    ref = reference_date() if use_now else reference_date(horizon)
    # "now" describes the current calendar, so a bare future month is allowed.
    # A data-horizon reference means "the most recent occurrence in the loaded
    # data" and therefore rolls a future month back one year.
    year = ref.year if use_now or month <= ref.month else ref.year - 1
    return year, month


def _week_of_month(label: str) -> int | None:
    """Return a 1-based seven-day bucket for an explicit month-week label.

    This is calendar normalization, not business-intent guessing.  The accepted
    intent already says the range is an absolute week; this function only turns
    labels such as ``6月第一周`` into executable half-open boundaries.
    """
    match = re.search(r"第\s*([1-5一二三四五])\s*周", str(label or ""))
    if not match:
        return None
    token = match.group(1)
    return int(token) if token.isdigit() else {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5}.get(token)


def _resolve_relative(constraint: dict[str, Any], ref: date, horizon: Any = None) -> dict[str, Any]:
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
        q_start = date(today.year, quarter * 3 + 1, 1)
        prev_q_start = date(today.year, (quarter - 1) * 3 + 1, 1) if quarter else date(today.year - 1, 10, 1)
        prev_q_label = f"{prev_q_start.year}-Q{(prev_q_start.month - 1) // 3 + 1}"
        out["periods"] = [{
            "key": "previous",
            "label": prev_q_label,
            "startInclusive": prev_q_start.isoformat(),
            "endExclusive": q_start.isoformat(),
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
            "endExclusive": (date(today.year + 1, 1, 1) if quarter == 3 else date(today.year, (quarter + 1) * 3 + 1, 1)).isoformat(),
        }]
    elif kind == "current_year":
        out["periods"] = [{
            "key": "current",
            "label": str(today.year),
            "startInclusive": f"{today.year}-01-01",
            "endExclusive": f"{today.year + 1}-01-01",
        }]
    elif kind == "specific_month":
        # 显式年份必须优先于参考日期；只有裸“6月份”才锚定数据地平线。
        # provider 即便只把 month 放进结构化字段，label 仍保留用户给出的年份。
        calendar_month = _calendar_month(str(constraint.get("label") or ""), constraint, horizon)
        if calendar_month:
            year, month = calendar_month
        else:
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
    elif kind == "comparison":
        # 「5月 vs 6月」等对比：把各周期 label 的月份锚定到数据地平线（不采用
        # LLM 猜测的绝对年份），并合成一个连续区间，避免 compile_query 把两个
        # 周期做 AND 而互相矛盾；月度拆分由 group_by 的时间粒度维度负责。
        members = constraint.get("periods") or []
        if not members and constraint.get("label"):
            members = [{"label": constraint.get("label")}]
        resolved: list[dict[str, Any]] = []
        for p in members:
            label = str(p.get("label") or "") if isinstance(p, dict) else str(p)
            member_reference = str(p.get("reference") or constraint.get("reference") or "data_horizon") if isinstance(p, dict) else str(constraint.get("reference") or "data_horizon")
            if isinstance(p, dict):
                member = dict(p)
                member["kind"] = str(member.get("kind") or ("recent" if member.get("amount") else "absolute"))
                member["reference"] = member_reference
                member.setdefault("timeGrain", member.get("grain") or constraint.get("timeGrain") or "day")
                if member.get("startInclusive") or member.get("endExclusive"):
                    member["periods"] = [{
                        "label": label,
                        **({"startInclusive": member["startInclusive"]} if member.get("startInclusive") else {}),
                        **({"endExclusive": member["endExclusive"]} if member.get("endExclusive") else {}),
                    }]
                nested = resolve_time_constraint(member, horizon)
                for period in (nested or {}).get("periods") or []:
                    resolved.append({**period, "label": label or period.get("label", ""), "reference": member_reference})
        if resolved:
            mstart = min(d["startInclusive"] for d in resolved)
            mend = max(d["endExclusive"] for d in resolved)
            out["periods"] = resolved
            out["periodBreakdown"] = resolved
    elif kind == "recent":
        amount = _amount(constraint.get("amount"))
        raw_unit = str(constraint.get("unit") or "day").strip().lower()
        unit = {
            "day": "day", "days": "day", "天": "day", "日": "day",
            "week": "week", "weeks": "week", "周": "week", "星期": "week",
            "month": "month", "months": "month", "月": "month", "个月": "month",
            "quarter": "quarter", "quarters": "quarter", "季度": "quarter", "季": "quarter",
            "year": "year", "years": "year", "年": "year",
        }.get(raw_unit)
        if unit is None:
            raise ValueError(f"unsupported recent time unit: {raw_unit}")
        if unit == "day":
            start = today - timedelta(days=amount - 1)
        elif unit == "week":
            start = today - timedelta(days=amount * 7 - 1)
        elif unit in {"month", "quarter"}:
            start = _shift_months(today + timedelta(days=1), -amount * (3 if unit == "quarter" else 1))
        else:
            try:
                start = (today + timedelta(days=1)).replace(year=today.year + 1 - amount)
            except ValueError:  # leap day
                start = date(today.year + 1 - amount, 2, 28)
        out["periods"] = [{
            "key": "recent",
            "label": constraint.get("label", f"最近 {amount}{raw_unit}"),
            # “最近 N 天”包含锚点当天，因此 [start, end) 恰好 N 天。
            "startInclusive": start.isoformat(),
            "endExclusive": (today + timedelta(days=1)).isoformat(),
        }]
    elif kind == "absolute" or kind == "specific_year":
        # 显式的绝对年份/月份（如「2020年总产量」「2026年各月」）。解析 label 里的四位年份，
        # 生成连续区间，避免「2020年」被当作无时间过滤导致整段数据总和冒充该年。
        label = str(constraint.get("label") or "")
        grain = str(constraint.get("timeGrain") or "year")
        year = None
        m = re.search(r"(?<!\d)(20\d{2}|19\d{2})(?:年|[-/])", label)
        if m:
            year = int(m.group(1))
        if year is None:
            # 兜底：从任意四位数提取
            m = re.search(r"(?<!\d)(\d{4})", label)
            if m:
                year = int(m.group(1))
        if year:
            if grain == "year":
                out["periods"] = [{
                    "key": "year",
                    "label": str(year),
                    "startInclusive": f"{year}-01-01",
                    "endExclusive": f"{year + 1}-01-01",
                }]
            elif grain == "month":
                mm = re.search(r"(\d{1,2})\s*月", label)
                month = int(mm.group(1)) if mm else 1
                start = date(year, month, 1)
                end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
                out["periods"] = [{
                    "key": "month",
                    "label": f"{start:%Y-%m}",
                    "startInclusive": start.isoformat(),
                    "endExclusive": end.isoformat(),
                }]
    return out
