from datetime import datetime

from app.delivery.templates import build_weekly_report_mail


def test_weekly_report_mail_hides_markdown_and_physical_data_identifiers():
    mail = build_weekly_report_mail(
        title="制造周报",
        period_start=datetime(2026, 7, 9),
        period_end=datetime(2026, 7, 15),
        version=1,
        content={},
        fallback_summary="**无数据**，请检查 `mes_process_output` 的 stat_date 字段。",
        link="http://localhost:5173/workspace/reports/rpt-1",
    )

    assert "**" not in mail.text_body
    assert "mes_process_output" not in mail.text_body
    assert "stat_date" not in mail.html_body
