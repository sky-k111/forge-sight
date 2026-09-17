"""Bounded Streamable HTTP MCP client for approved enterprise inference tools."""

import json
import time
from urllib.parse import urlsplit

import httpx

from app.config import settings


class RemoteModelError(ValueError):
    pass


def check_endpoint(endpoint: str) -> None:
    try:
        url = urlsplit(endpoint)
        _ = url.port  # Validate malformed port syntax before opening a connection.
    except ValueError as exc:
        raise RemoteModelError("MCP 服务地址格式无效") from exc
    origin = f"{url.scheme}://{url.netloc}"
    if (url.scheme not in {"http", "https"} or not url.hostname or url.username
            or url.password or url.query or url.fragment
            or origin not in settings.mcp_model_allowed_origins):
        raise RemoteModelError("服务地址未获允许，请管理员配置 MCP_MODEL_ALLOWED_ORIGINS")


class ModelMCPClient:
    def __init__(self, endpoint: str, credential_ref: str = ""):
        check_endpoint(endpoint)
        self.endpoint = endpoint
        self.sequence = 0
        self.headers = {"Accept": "application/json, text/event-stream"}
        if credential_ref:
            token = settings.mcp_model_credentials.get(credential_ref)
            if not token:
                raise RemoteModelError("认证配置不存在，请检查服务端凭据名称")
            self.headers["Authorization"] = f"Bearer {token}"
        self.client = httpx.Client(timeout=30, follow_redirects=False, trust_env=False)

    def __enter__(self):
        try:
            result = self.request("initialize", {
                "protocolVersion": "2025-11-25", "capabilities": {},
                "clientInfo": {"name": "forgesight-models", "version": "1.0"},
            })
            if result.get("protocolVersion") not in {"2025-11-25", "2025-06-18", "2025-03-26"}:
                raise RemoteModelError("服务返回不支持的 MCP 协议版本")
            if not isinstance(result.get("capabilities"), dict) or "tools" not in result["capabilities"]:
                raise RemoteModelError("MCP 服务未提供工具调用能力")
            self.headers["MCP-Protocol-Version"] = result["protocolVersion"]
            self.request("notifications/initialized", notification=True)
            return self
        except Exception:
            self.client.close()
            raise

    def __exit__(self, *_):
        self.client.close()

    def request(self, method, params=None, notification=False):
        started = time.monotonic()
        self.sequence += 1
        payload = {"jsonrpc": "2.0", "method": method}
        if not notification:
            payload["id"] = self.sequence
        if params is not None:
            payload["params"] = params
        try:
            with self.client.stream("POST", self.endpoint, headers=self.headers, json=payload) as response:
                response.raise_for_status()
                if response.headers.get("Mcp-Session-Id"):
                    self.headers["Mcp-Session-Id"] = response.headers["Mcp-Session-Id"]
                if notification:
                    return {}
                size, chunks = 0, []
                if "text/event-stream" in response.headers.get("content-type", ""):
                    event = []
                    for line in response.iter_lines():
                        size += len(line.encode())
                        if size > 2_000_000 or time.monotonic() - started > 30:
                            raise RemoteModelError("模型响应超过大小限制")
                        if line.startswith("data:"):
                            event.append(line[5:].strip())
                        elif not line and event:
                            item = json.loads("\n".join(event))
                            event = []
                            if isinstance(item, dict) and item.get("id") == self.sequence:
                                return self.result(item)
                    raise RemoteModelError("MCP 流未返回调用结果")
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > 2_000_000 or time.monotonic() - started > 30:
                        raise RemoteModelError("模型响应超过大小限制")
                    chunks.append(chunk)
                item = json.loads(b"".join(chunks))
                if not isinstance(item, dict) or item.get("id") != self.sequence:
                    raise RemoteModelError("MCP 响应编号不匹配")
                return self.result(item)
        except (httpx.HTTPError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise RemoteModelError("远程模型连接或响应失败，请检查服务、认证和返回格式") from exc

    @staticmethod
    def result(item):
        if "error" in item or not isinstance(item.get("result"), dict):
            raise RemoteModelError("MCP 服务拒绝调用或返回无效结果")
        return item["result"]

    def tools(self):
        items, cursor = [], None
        for _ in range(10):
            result = self.request("tools/list", {"cursor": cursor} if cursor else {})
            page = result.get("tools", [])
            if not isinstance(page, list) or any(not isinstance(item, dict) for item in page):
                raise RemoteModelError("MCP 工具列表格式无效")
            items.extend(page)
            cursor = result.get("nextCursor")
            if not cursor:
                return items
        raise RemoteModelError("工具列表超过分页限制")

    def call(self, name, arguments):
        result = self.request("tools/call", {"name": name, "arguments": arguments})
        if result.get("isError"):
            raise RemoteModelError("远程模型报告执行失败")
        structured = result.get("structuredContent")
        if not isinstance(structured, dict):
            raise RemoteModelError("模型必须返回 structuredContent 结构化结果")
        return structured
