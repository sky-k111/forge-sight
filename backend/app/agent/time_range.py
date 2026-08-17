"""Natural-language time references shared by the Agent's intent layers.

The Agent must understand the way people actually write dates in Chinese.  This
module keeps that language normalization in one place so report creation,
follow-up clarification, and ordinary analysis do not each grow a different
regular-expression dialect.  It only extracts what the user said; resolving a
relative phrase against a data source still happens at execution time.
"""

from __future__ import annotations

import re


_CHINESE_DIGITS = {
    "零": 0,
    "〇": 0,
    "○": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_CHINESE_UNITS = {"十": 10, "百": 100, "千": 1000}
_CHINESE_NUMBER_CHARS = "零〇○一二两三四五六七八九十百千"


def chinese_number_to_int(value: str) -> int | None:
    """Convert common Chinese numerals, including ``二〇二六`` and ``十一``."""

    token = str(value or "").strip()
    if not token:
        return None
    if token.isdigit():
        return int(token)
    if any(char not in _CHINESE_NUMBER_CHARS for char in token):
        return None
    if all(char in _CHINESE_DIGITS for char in token):
        return int("".join(str(_CHINESE_DIGITS[char]) for char in token))

    total = 0
    section = 0
    number = 0
    for char in token:
        if char in _CHINESE_DIGITS:
            number = _CHINESE_DIGITS[char]
            continue
        unit = _CHINESE_UNITS[char]
        if number == 0:
            number = 1
        section += number * unit
        number = 0
    return total + section + number


_YEAR_MONTH_RE = re.compile(
    rf"(?P<year>20\d{{2}}|[{_CHINESE_NUMBER_CHARS}]{{4}})\s*(?:年|[-/])\s*"
    rf"(?P<month>\d{{1,2}}|[{_CHINESE_NUMBER_CHARS}]{{1,3}})\s*(?:月|[-/])?"
)


def extract_year_month(text: str) -> tuple[int, int] | None:
    """Extract a calendar month such as ``2026年七月`` or ``二〇二六年7月``."""

    match = _YEAR_MONTH_RE.search(str(text or ""))
    if not match:
        return None
    year = chinese_number_to_int(match.group("year"))
    month = chinese_number_to_int(match.group("month"))
    if year is None or month is None or not 1 <= month <= 12:
        return None
    return year, month


_RELATIVE_PERIOD_RE = re.compile(
    rf"(?:最近|近)\s*(?:\d+|[{_CHINESE_NUMBER_CHARS}]+)\s*(?:天|日|周|星期|礼拜|个月|月)"
    r"|(?:最后|最新|过去|上一|上个)\s*(?:一|1)?\s*(?:个)?\s*(?:有数据的)?\s*(?:周|星期|礼拜|月|季度)"
    r"|(?:本周|这周|这个星期|这个礼拜|这个月|本月|这个季度|本季度|今年|本年|本年至今|今天|昨天|上周|上月|上个月|上个季度|去年|上半年|下半年)"
)


def has_time_reference(text: str) -> bool:
    """Return whether a user turn contains a usable calendar/relative period."""

    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    return bool(_RELATIVE_PERIOD_RE.search(normalized) or extract_year_month(normalized))
