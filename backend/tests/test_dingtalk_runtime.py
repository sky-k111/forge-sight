"""钉钉主动消息 API 适配测试，不访问真实钉钉。"""

import json
from types import SimpleNamespace

from app.integrations import dingtalk_runtime


class _Response:
    def __init__(self, payload):
        self._payload = payload
        self.content = b"{}"

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_open_api_sends_private_and_group_markdown(monkeypatch):
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        if url.endswith("/oauth2/accessToken"):
            return _Response({"accessToken": "token-1", "expireIn": 7200})
        return _Response({"processQueryKey": f"delivery-{len(calls)}"})

    monkeypatch.setattr(dingtalk_runtime.settings, "dingtalk_client_id", "ding-app")
    monkeypatch.setattr(dingtalk_runtime.settings, "dingtalk_client_secret", "secret")
    monkeypatch.setattr(dingtalk_runtime.settings, "dingtalk_robot_code", "robot-code")
    monkeypatch.setattr(dingtalk_runtime.requests, "post", fake_post)
    api = dingtalk_runtime.DingTalkOpenApi()

    api.send_markdown(
        {"conversationType": "1", "senderStaffId": "staff-1"},
        "单聊结果",
    )
    api.send_markdown(
        {"conversationType": "2", "conversationId": "cid-group"},
        "群聊结果",
    )

    private_url, private_call = calls[1]
    group_url, group_call = calls[2]
    assert private_url.endswith("/robot/oToMessages/batchSend")
    assert private_call["json"]["userIds"] == ["staff-1"]
    assert group_url.endswith("/robot/groupMessages/send")
    assert group_call["json"]["openConversationId"] == "cid-group"
    assert json.loads(group_call["json"]["msgParam"])["text"] == "群聊结果"
    assert private_call["headers"]["x-acs-dingtalk-access-token"] == "token-1"


def test_open_api_sends_native_image_message_with_uploaded_media_id(monkeypatch):
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        if url.endswith("/oauth2/accessToken"):
            return _Response({"accessToken": "token-1", "expireIn": 7200})
        return _Response({"processQueryKey": "image-delivery-1"})

    monkeypatch.setattr(dingtalk_runtime.settings, "dingtalk_client_id", "ding-app")
    monkeypatch.setattr(dingtalk_runtime.settings, "dingtalk_client_secret", "secret")
    monkeypatch.setattr(dingtalk_runtime.settings, "dingtalk_robot_code", "robot-code")
    monkeypatch.setattr(dingtalk_runtime.requests, "post", fake_post)
    api = dingtalk_runtime.DingTalkOpenApi()

    message_id = api.send_image(
        {"conversationType": "2", "conversationId": "cid-group"},
        "@media-chart-1",
    )

    assert message_id == "image-delivery-1"
    _url, image_call = calls[1]
    assert image_call["json"]["msgKey"] == "sampleImageMsg"
    assert json.loads(image_call["json"]["msgParam"]) == {
        "photoURL": "@media-chart-1"
    }


def test_successful_result_uploads_png_and_sends_native_image(monkeypatch):
    uploads = []

    class _StreamClient:
        def upload_to_dingtalk(self, payload, **kwargs):
            uploads.append((payload, kwargs))
            return "@media-chart-1"

    spec = SimpleNamespace(title="逐周产量趋势")
    monkeypatch.setattr(dingtalk_runtime, "build_chart_specs", lambda _snapshot: [spec])
    monkeypatch.setattr(dingtalk_runtime, "render_chart_png", lambda _spec: b"\x89PNG-chart")
    api = dingtalk_runtime.DingTalkOpenApi(_StreamClient())
    markdown_calls = []
    image_calls = []
    monkeypatch.setattr(
        api,
        "send_markdown",
        lambda target, text: markdown_calls.append((target, text)) or "markdown-1",
    )
    monkeypatch.setattr(
        api,
        "send_image",
        lambda target, media_id: image_calls.append((target, media_id)) or "image-1",
    )

    message_id = api.send_result(
        {"conversationType": "2", "conversationId": "cid-group"},
        """分析正文

以下为逐周产量柱状图：

```json
{"type":"bar","data":{"labels":["第23周"],"datasets":[{"label":"产量","data":[385160]}]}}
```""",
        {"status": "succeeded"},
    )

    assert message_id == "image-1"
    assert uploads == [
        (
            b"\x89PNG-chart",
            {
                "filetype": "image",
                "filename": "forgesight-chart-1.png",
                "mimetype": "image/png",
            },
        )
    ]
    assert markdown_calls == [
        ({"conversationType": "2", "conversationId": "cid-group"}, "分析正文")
    ]
    assert image_calls == [
        (
            {"conversationType": "2", "conversationId": "cid-group"},
            "@media-chart-1",
        )
    ]


def test_chart_failure_falls_back_to_markdown(monkeypatch):
    class _BrokenStreamClient:
        def upload_to_dingtalk(self, *_args, **_kwargs):
            raise RuntimeError("upload failed")

    monkeypatch.setattr(
        dingtalk_runtime,
        "build_chart_specs",
        lambda _snapshot: [SimpleNamespace(title="趋势")],
    )
    monkeypatch.setattr(dingtalk_runtime, "render_chart_png", lambda _spec: b"png")
    api = dingtalk_runtime.DingTalkOpenApi(_BrokenStreamClient())
    markdown_calls = []
    monkeypatch.setattr(
        api,
        "send_markdown",
        lambda target, text: markdown_calls.append((target, text)) or "markdown-1",
    )

    message_id = api.send_result(
        {"conversationType": "1", "senderStaffId": "staff-1"},
        "保底文本",
        {"status": "succeeded"},
    )

    assert message_id == "markdown-1"
    assert markdown_calls == [
        ({"conversationType": "1", "senderStaffId": "staff-1"}, "保底文本")
    ]


def test_result_without_chart_artifact_keeps_markdown(monkeypatch):
    monkeypatch.setattr(dingtalk_runtime, "build_chart_specs", lambda _snapshot: [])
    api = dingtalk_runtime.DingTalkOpenApi()
    markdown_calls = []
    monkeypatch.setattr(
        api,
        "send_markdown",
        lambda target, text: markdown_calls.append((target, text)) or "markdown-only",
    )

    message_id = api.send_result(
        {"conversationType": "2", "conversationId": "cid-group"},
        "无可视化数据",
        {"status": "succeeded"},
    )

    assert message_id == "markdown-only"
    assert len(markdown_calls) == 1


def test_chart_markdown_sanitizer_keeps_unrelated_code_blocks():
    text = """查询完成。

```json
{"type":"bar","data":{"labels":["A"],"datasets":[{"data":[1]}]},"options":{}}
```

排查 SQL：

```sql
SELECT * FROM production_daily;
```

状态：succeeded"""

    cleaned = dingtalk_runtime.sanitize_chart_markdown(text)

    assert '"datasets"' not in cleaned
    assert "SELECT * FROM production_daily" in cleaned
    assert "查询完成。" in cleaned
    assert "状态：succeeded" in cleaned
