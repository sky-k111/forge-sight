"""Provider-neutral text alert delivery for Feishu and DingTalk."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Protocol

import httpx

from app.delivery.provider import ProviderFailure, ProviderResult


@dataclass(frozen=True)
class OutgoingAlert:
    channel: str
    recipient_id: str
    title: str
    text: str
    detail_url: str
    idempotency_key: str


class AlertProvider(Protocol):
    channel: str

    def send(self, message: OutgoingAlert) -> ProviderResult: ...


@dataclass
class CaptureAlertProvider:
    channel: str
    messages: list[OutgoingAlert] = field(default_factory=list, repr=False)

    def send(self, message: OutgoingAlert) -> ProviderResult:
        if message.channel != self.channel:
            raise ProviderFailure("invalid_message", "alert channel mismatch", retryable=False)
        self.messages.append(message)
        return ProviderResult(provider_message_id=f"capture-{self.channel}-{len(self.messages)}")


def _failure(exc: Exception, *, uncertain: bool = False) -> ProviderFailure:
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return ProviderFailure(
            "rate_limited" if status == 429 else "provider_error",
            f"HTTP {status}",
            retryable=status == 429 or status >= 500,
            uncertain=uncertain,
        )
    if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError)):
        return ProviderFailure("network", type(exc).__name__, retryable=not uncertain, uncertain=uncertain)
    return ProviderFailure("provider_error", type(exc).__name__, retryable=False, uncertain=uncertain)


class FeishuAlertProvider:
    channel = "feishu"

    def __init__(self, app_id: str, app_secret: str, *, client: httpx.Client | None = None):
        self.app_id = app_id.strip()
        self.app_secret = app_secret.strip()
        self.client = client or httpx.Client(timeout=20)
        self._access_token: str | None = None

    def _token(self) -> str:
        if not self.app_id or not self.app_secret:
            raise ProviderFailure("configuration", "Feishu credentials missing", retryable=False)
        if self._access_token:
            return self._access_token
        response = self.client.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": self.app_id, "app_secret": self.app_secret},
        )
        response.raise_for_status()
        payload = response.json()
        token = payload.get("tenant_access_token")
        if payload.get("code") != 0 or not token:
            raise ProviderFailure("configuration", "Feishu token rejected", retryable=False)
        self._access_token = str(token)
        return self._access_token

    def send(self, message: OutgoingAlert) -> ProviderResult:
        try:
            token = self._token()
            response = self.client.post(
                "https://open.feishu.cn/open-apis/im/v1/messages",
                headers={"Authorization": f"Bearer {token}"},
                params={"receive_id_type": "open_id"},
                json={
                    "receive_id": message.recipient_id,
                    "msg_type": "interactive",
                    "content": json.dumps(
                        {
                            "config": {"wide_screen_mode": True},
                            "header": {"title": {"tag": "plain_text", "content": message.title}},
                            "elements": [
                                {"tag": "markdown", "content": message.text},
                                {
                                    "tag": "action",
                                    "actions": [
                                        {
                                            "tag": "button",
                                            "text": {"tag": "plain_text", "content": "查看详情"},
                                            "url": message.detail_url,
                                            "type": "primary",
                                        }
                                    ],
                                },
                            ],
                        },
                        ensure_ascii=False,
                    ),
                    "uuid": hashlib.sha256(message.idempotency_key.encode()).hexdigest()[:32],
                },
            )
            response.raise_for_status()
            payload = response.json()
            message_id = (payload.get("data") or {}).get("message_id")
            if payload.get("code") != 0 or not message_id:
                raise ProviderFailure("provider_error", "Feishu alert rejected", retryable=False)
            return ProviderResult(provider_message_id=str(message_id))
        except ProviderFailure:
            raise
        except Exception as exc:
            raise _failure(exc, uncertain=True) from exc


class DingTalkAlertProvider:
    channel = "dingtalk"

    def __init__(self, client_id: str, client_secret: str, agent_id: str, *, client: httpx.Client | None = None):
        self.client_id = client_id.strip()
        self.client_secret = client_secret.strip()
        self.agent_id = agent_id.strip()
        self.client = client or httpx.Client(timeout=20)
        self._access_token: str | None = None

    def _token(self) -> str:
        if not self.client_id or not self.client_secret or not self.agent_id:
            raise ProviderFailure("configuration", "DingTalk credentials missing", retryable=False)
        if self._access_token:
            return self._access_token
        response = self.client.post(
            "https://api.dingtalk.com/v1.0/oauth2/accessToken",
            json={"appKey": self.client_id, "appSecret": self.client_secret},
        )
        response.raise_for_status()
        token = response.json().get("accessToken")
        if not token:
            raise ProviderFailure("configuration", "DingTalk token rejected", retryable=False)
        self._access_token = str(token)
        return self._access_token

    def send(self, message: OutgoingAlert) -> ProviderResult:
        try:
            token = self._token()
            resolved = self.client.post(
                "https://oapi.dingtalk.com/topapi/user/getbyunionid",
                params={"access_token": token},
                json={"unionid": message.recipient_id},
            )
            resolved.raise_for_status()
            resolved_payload = resolved.json()
            user_id = (resolved_payload.get("result") or {}).get("userid")
            if resolved_payload.get("errcode") not in {None, 0} or not user_id:
                raise ProviderFailure("invalid_address", "DingTalk identity unresolved", retryable=False)
            response = self.client.post(
                "https://oapi.dingtalk.com/topapi/message/corpconversation/asyncsend_v2",
                params={"access_token": token},
                json={
                    "agent_id": int(self.agent_id) if self.agent_id.isdigit() else self.agent_id,
                    "userid_list": str(user_id),
                    "to_all_user": False,
                    "msg": {
                        "msgtype": "action_card",
                        "action_card": {
                            "title": message.title,
                            "markdown": f"### {message.title}\n\n{message.text}",
                            "single_title": "查看详情",
                            "single_url": message.detail_url,
                        },
                    },
                },
            )
            response.raise_for_status()
            payload = response.json()
            task_id = (payload.get("result") or payload).get("task_id")
            if payload.get("errcode") not in {None, 0} or task_id in {None, ""}:
                raise ProviderFailure("provider_error", "DingTalk alert rejected", retryable=False)
            return ProviderResult(provider_message_id=str(task_id))
        except ProviderFailure:
            raise
        except Exception as exc:
            raise _failure(exc, uncertain=True) from exc


__all__ = [
    "AlertProvider",
    "CaptureAlertProvider",
    "DingTalkAlertProvider",
    "FeishuAlertProvider",
    "OutgoingAlert",
]
