"""钉钉 Stream 接收器与持久任务 worker 运行时。"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import uuid

import dingtalk_stream
import requests
from dingtalk_stream import AckMessage

from app.config import settings
from app.integrations import dingtalk_chat
from app.integrations.feishu_charts import build_chart_specs, render_chart_png

LOG = logging.getLogger("forge_sight_dingtalk_bot")

_FENCED_CODE_RE = re.compile(
    r"```[ \t]*(?P<language>[^\r\n`]*)\r?\n(?P<body>.*?)\r?\n?```",
    re.DOTALL,
)
_ORPHAN_CHART_LEAD_RE = re.compile(
    r"(?im)^[^\r\n]{0,120}(?:以下为|图表配置|可视化配置|配置如下)"
    r"[^\r\n]*(?:图|配置)[：:]?[ \t]*(?:\r?\n|$)"
)


def _is_chart_configuration(value: str) -> bool:
    """Return whether a fenced JSON value is a render-only chart payload."""

    try:
        payload = json.loads(value.strip())
    except (TypeError, ValueError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict):
        return False
    data = payload.get("data")
    if isinstance(data, dict) and isinstance(data.get("datasets"), list):
        return bool(data.get("labels") is not None or data.get("datasets"))
    return isinstance(payload.get("series"), list) and any(
        key in payload for key in ("xAxis", "yAxis", "dataset", "visualMap")
    )


def sanitize_chart_markdown(text: str) -> str:
    """Remove unusable Chart.js/ECharts payloads while preserving real code."""

    removed = False

    def replace(match: re.Match) -> str:
        nonlocal removed
        if not _is_chart_configuration(match.group("body")):
            return match.group(0)
        removed = True
        return ""

    cleaned = _FENCED_CODE_RE.sub(replace, str(text or ""))
    if removed:
        cleaned = _ORPHAN_CHART_LEAD_RE.sub("", cleaned)
    cleaned = re.sub(r"[ \t]+\r?\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("content", "text", "message", "title"):
            if value.get(key):
                return str(value[key])
    return str(value)


def _walk_values(value, keys: set[str]) -> list[str]:
    result: list[str] = []

    def walk(item):
        if isinstance(item, dict):
            for key, child in item.items():
                if key in keys and child and str(child) not in result:
                    result.append(str(child))
                walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)

    walk(value)
    return result


class DingTalkOpenApi:
    """缓存应用 token，并使用主动消息 API 回复持久任务。"""

    def __init__(self, stream_client=None):
        self.client_id = settings.dingtalk_client_id.strip()
        self.client_secret = settings.dingtalk_client_secret.strip()
        self.robot_code = settings.dingtalk_robot_code.strip() or self.client_id
        self.stream_client = stream_client
        self._token = ""
        self._token_expires_at = 0.0
        self._lock = threading.Lock()

    def _access_token(self) -> str:
        with self._lock:
            now = time.time()
            if self._token and now < self._token_expires_at - 300:
                return self._token
            response = requests.post(
                "https://api.dingtalk.com/v1.0/oauth2/accessToken",
                json={"appKey": self.client_id, "appSecret": self.client_secret},
                timeout=20,
            )
            response.raise_for_status()
            payload = response.json()
            token = str(payload.get("accessToken") or "")
            if not token:
                raise RuntimeError("钉钉未返回应用 accessToken")
            self._token = token
            self._token_expires_at = now + int(payload.get("expireIn") or 7200)
            return token

    def user_by_staff_id(self, staff_id: str) -> dict:
        response = requests.post(
            "https://oapi.dingtalk.com/topapi/v2/user/get",
            params={"access_token": self._access_token()},
            json={"userid": staff_id, "language": "zh_CN"},
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        if int(payload.get("errcode") or 0) != 0:
            raise RuntimeError(f"查询钉钉用户失败: {payload.get('errmsg') or payload}")
        return payload.get("result") or {}

    def _send_robot_message(self, target: dict, msg_key: str, msg_param: dict) -> str:
        message = {
            "robotCode": self.robot_code,
            "msgKey": msg_key,
            "msgParam": json.dumps(msg_param, ensure_ascii=False),
        }
        if str(target.get("conversationType") or "") == "2":
            conversation_id = str(target.get("conversationId") or "")
            if not conversation_id:
                raise RuntimeError("钉钉群回复缺少 conversationId")
            url = "https://api.dingtalk.com/v1.0/robot/groupMessages/send"
            message["openConversationId"] = conversation_id
        else:
            staff_id = str(target.get("senderStaffId") or "")
            if not staff_id:
                raise RuntimeError("钉钉单聊回复缺少 senderStaffId")
            url = "https://api.dingtalk.com/v1.0/robot/oToMessages/batchSend"
            message["userIds"] = [staff_id]
        response = requests.post(
            url,
            headers={
                "Content-Type": "application/json",
                "x-acs-dingtalk-access-token": self._access_token(),
            },
            json=message,
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json() if response.content else {}
        error_code = payload.get("code", payload.get("errcode"))
        if error_code not in (None, 0, "0"):
            raise RuntimeError(
                f"钉钉主动消息发送失败: {payload.get('message') or payload.get('errmsg') or error_code}"
            )
        return str(
            payload.get("processQueryKey")
            or payload.get("requestId")
            or payload.get("messageId")
            or uuid.uuid4().hex
        )

    def send_markdown(self, target: dict, text: str) -> str:
        return self._send_robot_message(
            target,
            "sampleMarkdown",
            {"title": "ForgeSight 分析结果", "text": text},
        )

    def send_image(self, target: dict, media_id: str) -> str:
        return self._send_robot_message(
            target,
            "sampleImageMsg",
            {"photoURL": media_id},
        )

    def _send_chart_images(self, target: dict, specs) -> str:
        if self.stream_client is None:
            raise RuntimeError("钉钉 Stream Client 尚未注入，无法上传图表")

        message_id = ""
        for index, spec in enumerate(specs):
            png = render_chart_png(spec)
            media_id = self.stream_client.upload_to_dingtalk(
                png,
                filetype="image",
                filename=f"forgesight-chart-{index + 1}.png",
                mimetype="image/png",
            )
            if not media_id:
                raise RuntimeError(f"第 {index + 1} 张钉钉图表上传失败")
            message_id = self.send_image(target, str(media_id))
        if not message_id:
            raise RuntimeError("钉钉未返回图片消息 ID")
        return message_id

    def send_result(self, target: dict, text: str, snapshot: dict) -> str:
        """成功结果发送正文和原生图片，任何可视化异常都保留 Markdown。"""

        text = sanitize_chart_markdown(text) or "分析已完成，请在 ForgeSight 查看完整结果。"
        specs = build_chart_specs(snapshot) if snapshot.get("status") == "succeeded" else []
        if not specs:
            return self.send_markdown(target, text)
        text_message_id = self.send_markdown(target, text)
        try:
            return self._send_chart_images(target, specs)
        except Exception:  # noqa: BLE001 - 图表失败不能改变已成功的分析状态
            LOG.exception("钉钉图片消息发送失败，已保留先行发送的 Markdown")
            return text_message_id


class DurableBotHandler(dingtalk_stream.ChatbotHandler):
    def __init__(self, api: DingTalkOpenApi):
        super().__init__()
        self.api = api
        self.bind_url = os.getenv(
            "FS_BIND_URL",
            settings.public_app_url.rstrip("/") + "/login",
        )

    def _inbound(self, callback_data: dict, incoming) -> dingtalk_chat.InboundMessage:
        content = _text(getattr(getattr(incoming, "text", None), "content", "")).strip()
        union_ids = _walk_values(callback_data, {"unionId", "union_id"})
        open_ids = _walk_values(callback_data, {"openId", "open_id", "senderId", "sender_id"})
        staff_id = str(getattr(incoming, "sender_staff_id", "") or "")
        if not union_ids and staff_id:
            try:
                profile = self.api.user_by_staff_id(staff_id)
                union_id = str(profile.get("unionid") or profile.get("unionId") or "")
                if union_id:
                    union_ids.append(union_id)
            except Exception:  # noqa: BLE001 - open_id 仍可作为绑定回退
                LOG.exception("无法通过 staff_id 补全 unionId")
        return dingtalk_chat.InboundMessage(
            message_id=str(
                getattr(incoming, "message_id", "")
                or getattr(incoming, "msg_id", "")
                or callback_data.get("msgId")
                or ""
            ),
            conversation_id=str(
                getattr(incoming, "conversation_id", "")
                or callback_data.get("conversationId")
                or ""
            ),
            conversation_type=str(
                getattr(incoming, "conversation_type", "")
                or callback_data.get("conversationType")
                or ""
            ),
            corp_id=str(
                getattr(incoming, "sender_corp_id", "")
                or getattr(incoming, "corp_id", "")
                or callback_data.get("senderCorpId")
                or callback_data.get("chatbotCorpId")
                or ""
            ),
            sender_union_id=union_ids[0] if union_ids else "",
            sender_open_id=open_ids[0] if open_ids else str(getattr(incoming, "sender_id", "") or ""),
            sender_staff_id=staff_id,
            question=content,
        )

    async def process(self, callback: dingtalk_stream.CallbackMessage):
        incoming = dingtalk_stream.ChatbotMessage.from_dict(callback.data)
        try:
            accepted = dingtalk_chat.accept_message(self._inbound(callback.data or {}, incoming))
            if accepted.created:
                self.reply_text("收到，已进入分析队列。后续追问会沿用当前会话上下文。", incoming)
        except dingtalk_chat.DingTalkSourceRequired as exc:
            self.reply_text(f"{exc}\n请打开 {self.bind_url} 完成数据源选择。", incoming)
        except dingtalk_chat.DingTalkBindingRequired:
            self.reply_text(f"当前钉钉账号尚未绑定，请打开 {self.bind_url} 登录并绑定。", incoming)
        except dingtalk_chat.UnsupportedDingTalkMessage as exc:
            self.reply_text(str(exc), incoming)
        except Exception as exc:  # noqa: BLE001 - 回调保持可确认
            LOG.exception("钉钉消息入库失败")
            self.reply_text(f"消息暂未受理：{type(exc).__name__}", incoming)
        return AckMessage.STATUS_OK, "OK"


def _worker_loop(worker_id: str, api: DingTalkOpenApi, stop_event: threading.Event) -> None:
    while not stop_event.is_set():
        job = dingtalk_chat.claim_job(worker_id)
        if job is None:
            stop_event.wait(max(0.2, settings.dingtalk_bot_poll_seconds))
            continue
        try:
            snapshot = dingtalk_chat.execute_job(job.id)
            result_text = dingtalk_chat.format_run_reply(snapshot)
            message_id = api.send_result(job.reply_target, result_text, snapshot)
            dingtalk_chat.complete_job(job.id, result_text, message_id)
        except dingtalk_chat.JobStillRunning:
            dingtalk_chat.defer_job(job.id)
        except (dingtalk_chat.DingTalkBindingRequired, dingtalk_chat.DingTalkSourceRequired) as exc:
            LOG.warning("钉钉任务因权限变化终止 job=%s error=%s", job.id, exc)
            dingtalk_chat.reject_job(job.id, exc)
        except Exception as exc:  # noqa: BLE001 - 持久任务按策略重试
            LOG.exception("钉钉任务执行失败 job=%s", job.id)
            dingtalk_chat.fail_job(job.id, exc)


def main() -> None:
    if not settings.dingtalk_client_id or not settings.dingtalk_client_secret:
        raise RuntimeError("DINGTALK_CLIENT_ID / DINGTALK_CLIENT_SECRET 未配置")
    credential = dingtalk_stream.Credential(
        settings.dingtalk_client_id,
        settings.dingtalk_client_secret,
    )
    client = dingtalk_stream.DingTalkStreamClient(credential)
    api = DingTalkOpenApi(client)
    stop_event = threading.Event()
    workers = [
        threading.Thread(
            target=_worker_loop,
            args=(f"dingtalk:{os.getpid()}:{index}", api, stop_event),
            name=f"dingtalk-worker-{index}",
            daemon=False,
        )
        for index in range(max(1, settings.dingtalk_bot_max_concurrency))
    ]
    for worker in workers:
        worker.start()

    client.register_callback_handler(
        dingtalk_stream.chatbot.ChatbotMessage.TOPIC,
        DurableBotHandler(api),
    )
    try:
        client.start_forever()
    finally:
        stop_event.set()
        for worker in workers:
            worker.join(timeout=10)
