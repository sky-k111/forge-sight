"""Native PDF-file providers for Feishu and DingTalk report delivery."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Protocol

import httpx

from app.delivery.provider import ProviderFailure, ProviderResult


@dataclass(frozen=True)
class OutgoingPdf:
    channel: str
    recipient_id: str
    filename: str
    content: bytes = field(repr=False)
    title: str = ""
    idempotency_key: str = ""


class PdfProvider(Protocol):
    channel: str

    def send(self, message: OutgoingPdf) -> ProviderResult: ...


@dataclass
class CapturePdfProvider:
    """Deterministic provider used by tests and local delivery verification."""

    channel: str
    messages: list[OutgoingPdf] = field(default_factory=list, repr=False)

    def send(self, message: OutgoingPdf) -> ProviderResult:
        if message.channel != self.channel:
            raise ProviderFailure(
                "invalid_message", "PDF channel mismatch", retryable=False
            )
        self.messages.append(message)
        return ProviderResult(
            provider_message_id=f"capture-{self.channel}-{len(self.messages)}"
        )


def _http_failure(exc: Exception, *, stage: str) -> ProviderFailure:
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status == 429:
            return ProviderFailure("rate_limited", f"HTTP {status}", retryable=True)
        if status in {401, 403}:
            return ProviderFailure("configuration", f"HTTP {status}", retryable=False)
        return ProviderFailure(
            "provider_error", f"HTTP {status}", retryable=status >= 500
        )
    if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError)):
        return ProviderFailure(
            "network",
            type(exc).__name__,
            retryable=stage != "send",
            uncertain=stage == "send",
        )
    return ProviderFailure(
        "provider_error", type(exc).__name__, retryable=False, uncertain=stage == "send"
    )


class FeishuPdfProvider:
    channel = "feishu"

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        *,
        client: httpx.Client | None = None,
        timeout_seconds: float = 20,
    ):
        self._app_id = app_id.strip()
        self._app_secret = app_secret.strip()
        self._client = client or httpx.Client(timeout=timeout_seconds)
        self._tenant_token: str | None = None

    def _token(self) -> str:
        if not self._app_id or not self._app_secret:
            raise ProviderFailure(
                "configuration", "Feishu app credentials are missing", retryable=False
            )
        if self._tenant_token:
            return self._tenant_token
        response = self._client.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": self._app_id, "app_secret": self._app_secret},
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") != 0 or not payload.get("tenant_access_token"):
            raise ProviderFailure(
                "configuration", "Feishu access token was rejected", retryable=False
            )
        self._tenant_token = str(payload["tenant_access_token"])
        return self._tenant_token

    def send(self, message: OutgoingPdf) -> ProviderResult:
        stage = "upload"
        try:
            token = self._token()
            headers = {"Authorization": f"Bearer {token}"}
            upload = self._client.post(
                "https://open.feishu.cn/open-apis/im/v1/files",
                headers=headers,
                data={"file_type": "stream", "file_name": message.filename},
                files={
                    "file": (message.filename, message.content, "application/pdf")
                },
            )
            upload.raise_for_status()
            upload_payload = upload.json()
            file_key = (upload_payload.get("data") or {}).get("file_key")
            if upload_payload.get("code") != 0 or not file_key:
                raise ProviderFailure(
                    "provider_error", "Feishu PDF upload failed", retryable=False
                )
            stage = "send"
            sent = self._client.post(
                "https://open.feishu.cn/open-apis/im/v1/messages",
                headers=headers,
                params={"receive_id_type": "open_id"},
                json={
                    "receive_id": message.recipient_id,
                    "msg_type": "file",
                    "content": json.dumps({"file_key": file_key}),
                    "uuid": (
                        message.idempotency_key
                        if len(message.idempotency_key.encode("utf-8")) <= 50
                        else hashlib.sha256(
                            message.idempotency_key.encode("utf-8")
                        ).hexdigest()[:32]
                    ),
                },
            )
            sent.raise_for_status()
            sent_payload = sent.json()
            message_id = (sent_payload.get("data") or {}).get("message_id")
            if sent_payload.get("code") != 0 or not message_id:
                raise ProviderFailure(
                    "provider_error", "Feishu PDF message failed", retryable=False
                )
            return ProviderResult(provider_message_id=str(message_id))
        except ProviderFailure:
            raise
        except Exception as exc:
            raise _http_failure(exc, stage=stage) from exc


class DingTalkPdfProvider:
    channel = "dingtalk"

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        agent_id: str,
        *,
        client: httpx.Client | None = None,
        timeout_seconds: float = 20,
    ):
        self._client_id = client_id.strip()
        self._client_secret = client_secret.strip()
        self._agent_id = agent_id.strip()
        self._client = client or httpx.Client(timeout=timeout_seconds)
        self._access_token: str | None = None

    def _token(self) -> str:
        if not self._client_id or not self._client_secret or not self._agent_id:
            raise ProviderFailure(
                "configuration", "DingTalk app credentials or AgentId are missing", retryable=False
            )
        if self._access_token:
            return self._access_token
        response = self._client.post(
            "https://api.dingtalk.com/v1.0/oauth2/accessToken",
            json={"appKey": self._client_id, "appSecret": self._client_secret},
        )
        response.raise_for_status()
        payload = response.json()
        token = payload.get("accessToken")
        if not token:
            raise ProviderFailure(
                "configuration", "DingTalk access token was rejected", retryable=False
            )
        self._access_token = str(token)
        return self._access_token

    @staticmethod
    def _legacy_result(payload: dict, field: str, operation: str):
        if payload.get("errcode") not in {None, 0}:
            raise ProviderFailure(
                "provider_error", f"DingTalk {operation} failed", retryable=False
            )
        result = payload.get("result") or payload
        value = result.get(field) if isinstance(result, dict) else None
        if value in {None, ""}:
            raise ProviderFailure(
                "provider_error", f"DingTalk {operation} returned no {field}", retryable=False
            )
        return value

    def send(self, message: OutgoingPdf) -> ProviderResult:
        stage = "resolve"
        try:
            token = self._token()
            resolved = self._client.post(
                "https://oapi.dingtalk.com/topapi/user/getbyunionid",
                params={"access_token": token},
                json={"unionid": message.recipient_id},
            )
            resolved.raise_for_status()
            user_id = str(
                self._legacy_result(resolved.json(), "userid", "identity resolution")
            )
            stage = "upload"
            upload = self._client.post(
                "https://oapi.dingtalk.com/media/upload",
                params={"access_token": token},
                data={"type": "file"},
                files={
                    "media": (message.filename, message.content, "application/pdf")
                },
            )
            upload.raise_for_status()
            media_id = str(self._legacy_result(upload.json(), "media_id", "PDF upload"))
            stage = "send"
            sent = self._client.post(
                "https://oapi.dingtalk.com/topapi/message/corpconversation/asyncsend_v2",
                params={"access_token": token},
                json={
                    "agent_id": int(self._agent_id)
                    if self._agent_id.isdigit()
                    else self._agent_id,
                    "userid_list": user_id,
                    "to_all_user": False,
                    "msg": {
                        "msgtype": "file",
                        "file": {"media_id": media_id},
                    },
                },
            )
            sent.raise_for_status()
            task_id = self._legacy_result(sent.json(), "task_id", "PDF message")
            return ProviderResult(provider_message_id=str(task_id))
        except ProviderFailure:
            raise
        except Exception as exc:
            raise _http_failure(exc, stage=stage) from exc


__all__ = [
    "CapturePdfProvider",
    "DingTalkPdfProvider",
    "FeishuPdfProvider",
    "OutgoingPdf",
    "PdfProvider",
    "ProviderFailure",
    "ProviderResult",
]
