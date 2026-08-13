"""Plain-text and safely escaped HTML mail templates."""

from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class MailBody:
    subject: str
    text_body: str
    html_body: str


def _date(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M") if value else "无"


def _summary_lines(content: dict, fallback: str) -> list[str]:
    findings = (content.get("managementSummary") or {}).get("findings") or []
    lines = [str(item.get("text") or "").strip() for item in findings if isinstance(item, dict)]
    lines = [line for line in lines if line]
    if len(lines) < 3:
        lines.extend(line.strip() for line in fallback.splitlines() if line.strip())
    unique = list(dict.fromkeys(lines))
    return unique[:5] or ["本期周报已发布，请进入系统查看完整内容。"]


def build_weekly_report_mail(
    *,
    title: str,
    period_start: datetime,
    period_end: datetime,
    version: int,
    content: dict,
    fallback_summary: str,
    link: str,
    attachment_omitted_reason: str | None = None,
) -> MailBody:
    lines = _summary_lines(content, fallback_summary)
    anomalies = content.get("anomalies") or []
    serious = next((str(item.get("title") or "").strip() for item in anomalies if isinstance(item, dict)), "未发现需重点关注的异常")
    quality = content.get("dataQuality") or {}
    coverage = quality.get("coverage")
    quality_text = f"数据覆盖率 {coverage}%" if isinstance(coverage, (int, float)) else "数据质量信息见完整周报"
    period = f"{period_start:%Y-%m-%d} 至 {period_end:%Y-%m-%d}"
    subject = f"[ForgeSight] {title}｜{period}"
    bullets = "\n".join(f"- {line}" for line in lines)
    omitted = f"\n附件说明：{attachment_omitted_reason}\n" if attachment_omitted_reason else ""
    text = (
        f"{title}\n周期：{period}\n版本：v{version}\n\n本期摘要\n{bullets}\n\n"
        f"重点异常：{serious}\n数据质量：{quality_text}\n{omitted}\n在线查看：{link}\n"
    )
    html_lines = "".join(f"<li>{html.escape(line)}</li>" for line in lines)
    omitted_html = f"<p><strong>附件说明：</strong>{html.escape(attachment_omitted_reason)}</p>" if attachment_omitted_reason else ""
    body = (
        '<div style="font-family:Arial,Microsoft YaHei,sans-serif;color:#17212b;line-height:1.65;max-width:680px">'
        f"<h1>{html.escape(title)}</h1><p>周期：{html.escape(period)} · 版本：v{version}</p>"
        f"<h2>本期摘要</h2><ul>{html_lines}</ul>"
        f"<p><strong>重点异常：</strong>{html.escape(serious)}</p>"
        f"<p><strong>数据质量：</strong>{html.escape(quality_text)}</p>{omitted_html}"
        f'<p><a href="{html.escape(link, quote=True)}">登录 ForgeSight 查看完整周报</a></p></div>'
    )
    return MailBody(subject=subject, text_body=text, html_body=body)


def build_announcement_mail(
    *,
    title: str,
    body: str,
    publisher: str,
    published_at: datetime,
    expires_at: datetime | None,
    link: str,
) -> MailBody:
    subject = f"[ForgeSight 公告] {title}"
    text = (
        f"{title}\n\n{body}\n\n发布人：{publisher}\n发布时间：{_date(published_at)}\n"
        f"有效期至：{_date(expires_at)}\n\n站内查看：{link}\n"
    )
    safe_body = html.escape(body).replace("\n", "<br>")
    html_body = (
        '<div style="font-family:Arial,Microsoft YaHei,sans-serif;color:#17212b;line-height:1.65;max-width:680px">'
        f"<h1>{html.escape(title)}</h1><p>{safe_body}</p>"
        f"<p>发布人：{html.escape(publisher)}<br>发布时间：{html.escape(_date(published_at))}<br>"
        f"有效期至：{html.escape(_date(expires_at))}</p>"
        f'<p><a href="{html.escape(link, quote=True)}">登录 ForgeSight 查看公告</a></p></div>'
    )
    return MailBody(subject=subject, text_body=text, html_body=html_body)
