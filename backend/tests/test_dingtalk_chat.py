"""钉钉持久会话、幂等收件箱和 Agent 恢复测试。"""

from datetime import datetime, timedelta

import pytest

from app.db.models import (
    AnalysisInteraction,
    AnalysisMessage,
    AnalysisRun,
    DataScopePolicy,
    DingTalkBinding,
    ExternalChatConversation,
    ExternalChatJob,
    PlatformUser,
    TenantMembership,
)
from app.integrations import dingtalk_chat
from app.api import dingtalk as dingtalk_api


def _seed_bound_user(
    session_factory,
    *,
    source_id: int | None = 1,
    with_scope: bool = True,
) -> DingTalkBinding:
    with session_factory() as session:
        user = PlatformUser(
            id="usr-dingtalk",
            username="dingtalk_user",
            password_hash="test-only",
            display_name="钉钉用户",
            role="business",
            enabled=True,
        )
        binding = DingTalkBinding(
            id="dtb-test",
            user_id=user.id,
            corp_id="corp-1",
            union_id="union-1",
            open_id="open-1",
            source_id=source_id,
            bind_status="bound",
        )
        rows = [
            user,
            # 生产环境中启动回填会给每个账号建立默认企业成员关系。
            TenantMembership(
                id="tm-dingtalk", tenant_id="tenant-default",
                user_id=user.id, member_role="member", is_default=True,
            ),
            binding,
        ]
        if with_scope:
            rows.append(
                DataScopePolicy(
                    id="scope-default",
                    subject_type="user",
                    subject_id=user.id,
                    business_key="analysis",
                    effect="allow",
                    data_source_ids_json=[1],
                )
            )
        session.add_all(rows)
        session.commit()
        return binding


def _message(message_id: str, question: str = "分析本周产量") -> dingtalk_chat.InboundMessage:
    return dingtalk_chat.InboundMessage(
        message_id=message_id,
        conversation_id="conversation-1",
        conversation_type="1",
        corp_id="corp-1",
        sender_union_id="union-1",
        sender_open_id="open-1",
        sender_staff_id="staff-1",
        question=question,
    )


def test_accept_message_reuses_session_and_is_idempotent(analysis_session):
    _seed_bound_user(analysis_session)

    first = dingtalk_chat.accept_message(_message("msg-1"), session_factory=analysis_session)
    duplicate = dingtalk_chat.accept_message(_message("msg-1"), session_factory=analysis_session)
    follow_up = dingtalk_chat.accept_message(
        _message("msg-2", "那华东区域呢？"),
        session_factory=analysis_session,
    )

    assert first.created is True
    assert duplicate.created is False
    assert follow_up.analysis_session_id == first.analysis_session_id
    with analysis_session() as session:
        assert session.query(ExternalChatConversation).count() == 1
        assert session.query(ExternalChatJob).count() == 2


def test_accept_message_fails_closed_without_bound_source(analysis_session):
    _seed_bound_user(analysis_session, source_id=None)

    with pytest.raises(dingtalk_chat.DingTalkSourceRequired):
        dingtalk_chat.accept_message(_message("msg-no-source"), session_factory=analysis_session)


def test_claim_preserves_order_and_recovers_expired_lease(analysis_session):
    _seed_bound_user(analysis_session)
    first = dingtalk_chat.accept_message(_message("msg-1"), session_factory=analysis_session)
    second = dingtalk_chat.accept_message(_message("msg-2"), session_factory=analysis_session)

    claimed = dingtalk_chat.claim_job("worker-1", session_factory=analysis_session)
    assert claimed.id == first.id
    assert dingtalk_chat.claim_job("worker-2", session_factory=analysis_session) is None

    with analysis_session() as session:
        row = session.get(ExternalChatJob, first.id)
        row.lease_expires_at = datetime.now() - timedelta(seconds=1)
        session.commit()

    reclaimed = dingtalk_chat.claim_job("worker-2", session_factory=analysis_session)
    assert reclaimed.id == first.id
    assert reclaimed.attempt_count == 2
    assert second.id != first.id


def test_execute_job_creates_run_with_stable_idempotency_key(monkeypatch, analysis_session):
    _seed_bound_user(analysis_session)
    accepted = dingtalk_chat.accept_message(_message("msg-run"), session_factory=analysis_session)
    calls = []

    def fake_create_run(session_id, question, idempotency_key=None):
        calls.append((session_id, question, idempotency_key))
        return {"runId": "run-created"}

    monkeypatch.setattr(dingtalk_chat.analysis_service, "create_run", fake_create_run)
    monkeypatch.setattr(
        dingtalk_chat.analysis_service,
        "get_run",
        lambda _run_id: {"runId": "run-created", "status": "succeeded", "assistantMessage": {"content": "完成"}},
    )

    snapshot = dingtalk_chat.execute_job(accepted.id, session_factory=analysis_session)

    assert snapshot["status"] == "succeeded"
    assert calls == [(accepted.analysis_session_id, "分析本周产量", f"dingtalk:{accepted.id}")]
    with analysis_session() as session:
        assert session.get(ExternalChatJob, accepted.id).run_id == "run-created"


def test_execute_job_resumes_pending_clarification(monkeypatch, analysis_session):
    _seed_bound_user(analysis_session)
    accepted = dingtalk_chat.accept_message(_message("msg-first"), session_factory=analysis_session)
    with analysis_session() as session:
        trigger = AnalysisMessage(
            id="msg-trigger",
            session_id=accepted.analysis_session_id,
            role="user",
            content="分析本周产量",
            sequence_no=1,
        )
        run = AnalysisRun(
            id="run-waiting",
            session_id=accepted.analysis_session_id,
            trigger_message_id=trigger.id,
            source_id=1,
            status="waiting_user",
        )
        trigger.run_id = run.id
        interaction = AnalysisInteraction(
            id="int-waiting",
            run_id=run.id,
            status="pending",
            prompt_json={"options": [{"label": "华东", "value": "east"}]},
            resume_node="clarify",
        )
        session.add_all([trigger, run, interaction])
        session.commit()

    answer = dingtalk_chat.accept_message(
        _message("msg-answer", "华东"),
        session_factory=analysis_session,
    )
    calls = []
    monkeypatch.setattr(
        dingtalk_chat.analysis_service,
        "respond_to_interaction",
        lambda interaction_id, value: calls.append((interaction_id, value)) or {"runId": "run-waiting"},
    )
    monkeypatch.setattr(
        dingtalk_chat.analysis_service,
        "get_run",
        lambda _run_id: {"runId": "run-waiting", "status": "succeeded", "assistantMessage": {"content": "已按华东分析"}},
    )

    snapshot = dingtalk_chat.execute_job(answer.id, session_factory=analysis_session)

    assert snapshot["status"] == "succeeded"
    assert calls == [("int-waiting", "east")]


def test_allowed_sources_are_explicit_and_deny_wins(analysis_session):
    _seed_bound_user(analysis_session, with_scope=False)
    with analysis_session() as session:
        session.add_all(
            [
                DataScopePolicy(
                    id="scope-allow",
                    subject_type="user",
                    subject_id="usr-dingtalk",
                    business_key="analysis",
                    effect="allow",
                    data_source_ids_json=[1, 2],
                ),
                DataScopePolicy(
                    id="scope-deny",
                    subject_type="user",
                    subject_id="usr-dingtalk",
                    business_key="weekly_report",
                    effect="deny",
                    data_source_ids_json=[2],
                ),
            ]
        )
        session.commit()
        assert dingtalk_api._allowed_source_ids(session, "usr-dingtalk") == {1}
        assert dingtalk_api._allowed_source_ids(session, "unconfigured-user") == set()


def test_oauth_callback_redirects_to_configured_frontend(monkeypatch):
    monkeypatch.setattr(
        dingtalk_api.settings,
        "public_app_url",
        "http://127.0.0.1:5173/",
    )

    response = dingtalk_api._frontend_redirect(
        "/login",
        "success",
        "钉钉账号绑定成功",
    )

    assert response.status_code == 302
    assert response.headers["location"].startswith(
        "http://127.0.0.1:5173/login?dingtalkBind=success&"
    )


def test_revoked_source_is_rejected_on_the_next_message(analysis_session):
    _seed_bound_user(analysis_session)
    with analysis_session() as session:
        policy = session.get(DataScopePolicy, "scope-default")
        policy.effect = "deny"
        session.commit()

    with pytest.raises(dingtalk_chat.DingTalkSourceRequired, match="不在当前账号授权范围"):
        dingtalk_chat.accept_message(_message("msg-revoked"), session_factory=analysis_session)


def test_queued_job_rechecks_permission_before_agent_execution(analysis_session):
    _seed_bound_user(analysis_session)
    accepted = dingtalk_chat.accept_message(_message("msg-queued"), session_factory=analysis_session)
    with analysis_session() as session:
        policy = session.get(DataScopePolicy, "scope-default")
        policy.effect = "deny"
        session.commit()

    with pytest.raises(dingtalk_chat.DingTalkSourceRequired, match="不在当前账号授权范围"):
        dingtalk_chat.execute_job(accepted.id, session_factory=analysis_session)


def test_expired_final_lease_does_not_block_the_next_message(analysis_session):
    _seed_bound_user(analysis_session)
    first = dingtalk_chat.accept_message(_message("msg-final-lease"), session_factory=analysis_session)
    second = dingtalk_chat.accept_message(_message("msg-after-final"), session_factory=analysis_session)
    with analysis_session() as session:
        row = session.get(ExternalChatJob, first.id)
        row.status = "processing"
        row.attempt_count = 3
        row.lease_expires_at = datetime.now() - timedelta(seconds=1)
        session.commit()

    claimed = dingtalk_chat.claim_job(
        "worker-next",
        session_factory=analysis_session,
        max_attempts=3,
    )

    assert claimed.id == second.id
    with analysis_session() as session:
        assert session.get(ExternalChatJob, first.id).status == "failed"
