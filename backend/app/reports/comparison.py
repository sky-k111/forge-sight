"""Calendar-bucket alignment shared by previews and frozen report charts."""
from datetime import date, datetime, timedelta
import math


def period_window(start: datetime, end: datetime):
    current = start.date()
    exclusive = end.date() + (timedelta(days=1) if end.time() != datetime.min.time() else timedelta())
    return current, exclusive, current - (exclusive - current)


def _bucket(value, grain):
    if isinstance(value, (datetime, date)):
        day = value.date() if isinstance(value, datetime) else value
    else:
        text = str(value)
        if grain == "week" and len(text) == 7:
            day = date.fromisocalendar(int(text[:4]), int(text[5:]), 1)
        elif grain == "month" and len(text) == 7:
            day = date.fromisoformat(text + "-01")
        else:
            day = datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    if grain == "month":
        return day.year * 12 + day.month
    if grain == "week":
        return (day.toordinal() - day.weekday()) // 7
    return day.toordinal()


def align_comparison(rows, previous_rows, grain, period_start, period_end, category_index=0, value_index=-1):
    start, end, previous_start = period_window(period_start, period_end)
    label = f"上一周期 {previous_start.isoformat()}—{(start - timedelta(days=1)).isoformat()}"
    points = []
    previous_by_offset = {}
    state = "ready" if previous_rows else "empty"
    try:
        for row in previous_rows or []:
            value = None if row[value_index] is None else float(row[value_index])
            previous_by_offset[_bucket(row[category_index], grain) - _bucket(previous_start, grain)] = (str(row[category_index]), value if value is not None and math.isfinite(value) else None)
        for row in rows:
            offset = _bucket(row[category_index], grain) - _bucket(start, grain)
            previous_label, value = previous_by_offset.get(offset, (None, None))
            points.append({"label": str(row[category_index]), "value": float(row[value_index]), "previousLabel": previous_label, "previousValue": value})
        points.sort(key=lambda point: _bucket(point["label"], grain))
    except (ValueError, TypeError, IndexError, OverflowError):
        # No positional fallback: unknown labels cannot prove period correspondence.
        state = "unavailable"
        points = [{"label": str(row[category_index]), "value": float(row[value_index]), "previousValue": None, "previousLabel": None} for row in rows]
    return {"points": points, "state": state, "label": label, "alignment": "calendar-bucket-offset", "range": {"currentStart": start.isoformat(), "currentEndExclusive": end.isoformat(), "previousStart": previous_start.isoformat(), "previousEndExclusive": start.isoformat()}}
