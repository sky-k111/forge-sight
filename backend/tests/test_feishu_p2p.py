"""飞书单聊机器人持久收件箱、绑定和结果适配。"""

import asyncio

from types import SimpleNamespace

import pytest

from app.db.models import (
    AnalysisSession,
    DataScopePolicy,
    ExternalChatConversation,
    ExternalChatJob,
    PlatformUser,
)
from app.integrations import feishu_p2p


def _config(bindings: str = '{"ou_user":{"userId":"local-test-user","sourceId":1}}'):
    return SimpleNamespace(
        feishu_bot_enabled=True,
        feishu_bot_app_id="cli_test",
        feishu_bot_app_secret="secret",
        feishu_bot_bindings_json=bindings,
        feishu_bot_lease_seconds=360,
        feishu_bot_max_attempts=3,
        feishu_bot_poll_seconds=0.01,
        feishu_bot_max_concurrency=1,
        agent_max_duration_seconds=1,
        public_app_url="http://127.0.0.1:3001",
    )


def _message(message_id="om_1", text="统计最近 7 天产量趋势", open_id="ou_user"):
    return SimpleNamespace(
        id=message_id,
        message_id=message_id,
        conversation=SimpleNamespace(chat_id="oc_private", chat_type="p2p"),
        sender=SimpleNamespace(open_id=open_id, user_id=None),
        raw_content_type="text",
        safe_content_text=text,
        content_text=text,
        raw={"header": {"tenant_key": "tenant_1"}},
    )


def test_parse_bindings_is_fail_closed():
    with pytest.raises(feishu_p2p.FeishuBotConfigurationError):
        feishu_p2p.parse_bindings("[]")
    with pytest.raises(feishu_p2p.FeishuBotConfigurationError):
        feishu_p2p.parse_bindings('{"ou_user":{"userId":"demo"}}')


def test_accept_message_creates_stable_conversation_and_is_idempotent(analysis_session):
    accepted = feishu_p2p.accept_message(
        _message(),
        config=_config(),
        session_factory=analysis_session,
    )
    duplicate = feishu_p2p.accept_message(
        _message(),
        config=_config(),
        session_factory=analysis_session,
    )

    assert accepted.created is True
    assert duplicate.created is False
    assert duplicate.id == accepted.id
    with analysis_session() as session:
        assert session.query(ExternalChatJob).count() == 1
        assert session.query(ExternalChatConversation).count() == 1
        conversation = session.query(ExternalChatConversation).one()
        analysis = session.query(AnalysisSession).filter_by(id=conversation.analysis_session_id).one()
        assert analysis.user_id == "local-test-user"
        assert analysis.source_id == 1


def test_accept_message_rejects_unbound_user(analysis_session):
    with pytest.raises(feishu_p2p.FeishuBindingRequired) as error:
        feishu_p2p.accept_message(
            _message(open_id="ou_unknown"),
            config=_config(),
            session_factory=analysis_session,
        )
    assert error.value.external_user_id == "ou_unknown"
    with analysis_session() as session:
        assert session.query(ExternalChatJob).count() == 0


def _seed_business_binding(session_factory, *, with_scope: bool = True) -> None:
    with session_factory() as session:
        user = PlatformUser(
            id="usr-feishu",
            username="feishu_user",
            password_hash="test-only",
            display_name="飞书用户",
            role="business",
            enabled=True,
        )
        session.add(user)
        if with_scope:
            session.add(
                DataScopePolicy(
                    id="scope-feishu",
                    subject_type="user",
                    subject_id=user.id,
                    business_key="analysis",
                    effect="allow",
                    data_source_ids_json=[1],
                )
            )
        session.commit()


def test_business_binding_uses_canonical_user_and_requires_source_scope(analysis_session):
    _seed_business_binding(analysis_session)
    config = _config('{"ou_user":{"userId":"feishu_user","sourceId":1}}')

    accepted = feishu_p2p.accept_message(
        _message(),
        config=config,
        session_factory=analysis_session,
    )

    with analysis_session() as session:
        conversation = session.get(ExternalChatConversation, session.get(ExternalChatJob, accepted.id).conversation_id)
        assert conversation.platform_user_id == "usr-feishu"
        analysis = session.get(AnalysisSession, conversation.analysis_session_id)
        assert analysis.user_id == "usr-feishu"


def test_queued_feishu_job_rechecks_revoked_scope(analysis_session):
    _seed_business_binding(analysis_session)
    config = _config('{"ou_user":{"userId":"feishu_user","sourceId":1}}')
    accepted = feishu_p2p.accept_message(
        _message(),
        config=config,
        session_factory=analysis_session,
    )
    with analysis_session() as session:
        session.get(DataScopePolicy, "scope-feishu").effect = "deny"
        session.commit()

    with pytest.raises(feishu_p2p.FeishuBotConfigurationError, match="授权范围"):
        feishu_p2p.execute_job(
            accepted.id,
            config=config,
            session_factory=analysis_session,
        )


def test_claim_and_execute_job_reuses_agent_service(monkeypatch, analysis_session):
    accepted = feishu_p2p.accept_message(
        _message(),
        config=_config(),
        session_factory=analysis_session,
    )
    claimed = feishu_p2p.claim_job(
        "test-worker",
        config=_config(),
        session_factory=analysis_session,
    )
    assert claimed and claimed.id == accepted.id

    calls = []

    def fake_create_run(session_id, question, idempotency_key=None):
        calls.append((session_id, question, idempotency_key))
        return {"runId": "run_feishu", "status": "queued"}

    monkeypatch.setattr(feishu_p2p.analysis_service, "create_run", fake_create_run)
    monkeypatch.setattr(
        feishu_p2p.analysis_service,
        "get_run",
        lambda _run_id: {
            "id": "run_feishu",
            "status": "succeeded",
            "assistantMessage": {"content": "最近 7 天产量整体上升。"},
            "usage": {"totalTokens": 128},
        },
    )

    reply = feishu_p2p.execute_job(
        accepted.id,
        config=_config(),
        session_factory=analysis_session,
    )
    assert "最近 7 天产量整体上升" in reply
    assert "查看完整图表与证据链" in reply
    assert calls[0][2] == f"feishu:{accepted.id}"
    with analysis_session() as session:
        assert session.query(ExternalChatJob).filter_by(id=accepted.id).one().run_id == "run_feishu"


def test_claim_preserves_message_order_inside_one_private_chat(analysis_session):
    first = feishu_p2p.accept_message(
        _message("om_first", "第一个问题"),
        config=_config(),
        session_factory=analysis_session,
    )
    second = feishu_p2p.accept_message(
        _message("om_second", "第二个问题"),
        config=_config(),
        session_factory=analysis_session,
    )

    claimed = feishu_p2p.claim_job("worker-1", config=_config(), session_factory=analysis_session)
    assert claimed and claimed.id == first.id
    assert feishu_p2p.claim_job("worker-2", config=_config(), session_factory=analysis_session) is None
    feishu_p2p.complete_job(first.id, "完成", "om_reply", session_factory=analysis_session)
    claimed_second = feishu_p2p.claim_job(
        "worker-2",
        config=_config(),
        session_factory=analysis_session,
    )
    assert claimed_second and claimed_second.id == second.id


def test_waiting_user_is_rendered_as_a_follow_up_question():
    reply = feishu_p2p.format_run_reply(
        {
            "status": "waiting_user",
            "interaction": {
                "prompt": {
                    "question": "最近是指哪个时间范围？",
                    "options": [
                        {"label": "最近 7 天", "value": "7d"},
                        {"label": "最近 30 天", "value": "30d"},
                    ],
                }
            },
            "usage": {},
        },
        config=_config(),
    )
    assert "最近是指哪个时间范围" in reply
    assert "最近 7 天" in reply
    assert "直接回复" in reply


def test_send_run_reply_uploads_png_and_sends_card():
    class Channel:
        def __init__(self):
            self.uploads = []
            self.messages = []

        async def upload_media(self, source, **kwargs):
            self.uploads.append((source, kwargs))
            return "img_test"

        async def send(self, chat_id, message, opts):
            self.messages.append((chat_id, message, opts))
            return SimpleNamespace(success=True, message_id="om_result", error=None)

    snapshot = {
        "status": "succeeded",
        "question": "统计产量",
        "assistantMessage": {"content": "产量整体上升。"},
        "usage": {"totalTokens": 128},
        "tasks": [{"id": "task-1", "title": "产量趋势", "analysisType": "trend"}],
        "artifacts": [{
            "type": "result",
            "taskId": "task-1",
            "content": {"ok": True, "columns": ["日期", "产量"], "rows": [["2026-07-01", 10], ["2026-07-02", 12]]},
        }],
    }
    channel = Channel()
    job = feishu_p2p.ClaimedJob("job-1", "oc-chat", "om-question", None, 1)
    result = asyncio.run(feishu_p2p.send_run_reply(channel, job, "文字回复", snapshot, config=_config()))

    assert result.success is True
    assert len(channel.uploads) == 1
    assert "card" in channel.messages[0][1]
    assert channel.messages[0][1]["card"]["body"]["elements"][2]["tag"] == "img"
    assert all(
        element.get("tag") != "button"
        for element in channel.messages[0][1]["card"]["body"]["elements"]
    )
