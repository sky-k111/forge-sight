"""飞书群自定义机器人适配器。

Webhook 和签名密钥仅从服务端配置读取。调用方只传报告内容，避免秘密进入 API、
数据库和浏览器。飞书自定义机器人成功响应在不同版本中可能使用 code 或
StatusCode，本适配器同时兼容。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import settings


@dataclass(frozen=True)
class FeishuResult:
    ok: bool
    response_id: str | None = None
    error: str | None = None
    raw: dict[str, Any] | None = None


def configured() -> bool:
    return bool(settings.feishu_webhook_url.strip())


def generate_sign(timestamp: int, secret: str) -> str:
    """生成飞书自定义机器人安全密钥签名。"""
    string_to_sign = f"{timestamp}\n{secret}"
    digest = hmac.new(
        string_to_sign.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).digest()
    return base64.b64encode(digest).decode("utf-8")


def build_report_card(report: dict) -> dict[str, Any]:
    status = report.get("statusLabel") or report.get("status") or "已发布"
    summary = (report.get("summary") or "报告已生成，可打开 ForgeSight 查看完整章节与证据链。").strip()
    if len(summary) > 420:
        summary = f"{summary[:417]}..."
    period = f"{report.get('periodStart', '')[:10]} 至 {report.get('periodEnd', '')[:10]}"
    confidence = report.get("confidence")
    facts = [
        f"**统计周期**：{period}",
        f"**数据源**：{report.get('sourceName') or report.get('sourceId')}",
        f"**状态**：{status}",
    ]
    if confidence is not None:
        facts.append(f"**综合可信度**：{confidence}%")
    elements: list[dict[str, Any]] = [
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": "\n".join(facts),
            },
        },
        {"tag": "hr"},
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"**管理摘要**\n{summary}",
            },
        },
    ]
    report_url = report.get("reportUrl")
    if report_url:
        elements.append(
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "查看完整报告"},
                        "type": "primary",
                        "url": report_url,
                    }
                ],
            }
        )
    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "turquoise",
                "title": {"tag": "plain_text", "content": report.get("title") or "ForgeSight 分析报告"},
            },
            "elements": elements,
        },
    }


def send_report(report: dict, *, client: httpx.Client | None = None) -> FeishuResult:
    webhook = settings.feishu_webhook_url.strip()
    if not webhook:
        return FeishuResult(ok=False, error="飞书群机器人尚未配置")

    payload = build_report_card(report)
    secret = settings.feishu_webhook_secret.strip()
    if secret:
        timestamp = int(time.time())
        payload["timestamp"] = timestamp
        payload["sign"] = generate_sign(timestamp, secret)

    owns_client = client is None
    http = client or httpx.Client(timeout=10.0)
    try:
        response = http.post(webhook, json=payload)
        response.raise_for_status()
        body = response.json()
        code = body.get("code", body.get("StatusCode", 0))
        ok = code in (0, "0", None)
        error = None if ok else str(body.get("msg") or body.get("StatusMessage") or f"飞书返回错误码 {code}")
        response_id = body.get("request_id") or body.get("RequestId")
        return FeishuResult(ok=ok, response_id=response_id, error=error, raw=body)
    except (httpx.HTTPError, ValueError) as exc:
        return FeishuResult(ok=False, error=f"飞书推送失败：{type(exc).__name__}", raw=None)
    finally:
        if owns_client:
            http.close()

