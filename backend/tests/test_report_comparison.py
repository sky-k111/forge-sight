from datetime import datetime, timedelta
from app.reports.comparison import align_comparison, period_window


def test_alignment_uses_bucket_offset_before_truncation_and_preserves_missing():
    start = datetime(2026, 7, 1)
    rows = [[(start + timedelta(days=i)).date().isoformat(), 100 + i] for i in range(20)]
    previous = [[(datetime(2026, 6, 11) + timedelta(days=i)).date().isoformat(), i + 1] for i in range(20) if i != 10]
    result = align_comparison(rows, previous, "day", start, datetime(2026, 7, 21))
    visible = result["points"][-12:]
    assert visible[0]["previousValue"] == 9
    assert visible[2]["previousValue"] is None
    assert visible[0]["previousLabel"] == "2026-06-19"


def test_inclusive_end_uses_whole_equal_length_days():
    current, end, previous = period_window(datetime(2026, 7, 1), datetime(2026, 7, 7, 23, 59, 59))
    assert (end - current).days == 7
    assert previous.isoformat() == "2026-06-24"
