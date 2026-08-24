"""钉钉问析的持久会话、任务租约与 Agent 恢复边界。"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

from sqlalchemy import and_, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import aliased

from app.agent import service as analysis_service
from app.agent.ids import new_id
from app.config import settings
from app.db.models import (
    AnalysisInteraction,
    AnalysisRun,
    AnalysisSession,
    DingTalkBinding,
    ExternalChatConversation,
    ExternalChatJob,
    PlatformUser,
)
from app.db.platform import PlatformSession
from app.integrations.access import allowed_source_ids as shared_allowed_source_ids
from app.integrations.access import source_exists

CHANNEL = "dingtalk"
STABLE_RUN_STATUSES = {"waiting_user", "succeeded", "failed", "blocked", "cancelled"}


class DingTalkBindingRequired(PermissionError):
    """钉钉身份尚未绑定平台用户。"""


class DingTalkSourceRequired(PermissionError):
    """绑定用户尚未选择获准的数据源。"""


class UnsupportedDingTalkMessage(ValueError):
    """消息缺少安全执行所需的确定字段。"""


class JobStillRunning(TimeoutError):
    """Agent 仍在后台运行，任务应稍后重新认领。"""


@dataclass(frozen=True)
class InboundMessage:
    message_id: str
    conversation_id: str
    conversation_type: str
    corp_id: str
    sender_union_id: str = ""
    sender_open_id: str = ""
    sender_staff_id: str = ""
    question: str = ""


@dataclass(frozen=True)
class AcceptedJob:
    id: str
    analysis_session_id: str
    created: bool


@dataclass(frozen=True)
class ClaimedJob:
    id: str
    external_chat_id: str
    external_message_id: str
    reply_target: dict
    attempt_count: int


def _binding_for_message(session, message: InboundMessage) -> DingTalkBinding | None:
    identities = []
    if message.sender_union_id:
        identities.append(DingTalkBinding.union_id == message.sender_union_id)
    if message.sender_open_id:
        identities.append(DingTalkBinding.open_id == message.sender_open_id)
    if not identities:
        return None
    query = session.query(DingTalkBinding).filter(
        DingTalkBinding.bind_status == "bound",
        or_(*identities),
    )
    if message.corp_id:
        query = query.filter(
            or_(DingTalkBinding.corp_id == message.corp_id, DingTalkBinding.corp_id == "")
        )
    return query.first()


def _validate_binding(session, binding: DingTalkBinding) -> None:
    user = session.get(PlatformUser, binding.user_id)
    if user is None or not user.enabled or user.role != "business":
        raise DingTalkBindingRequired("绑定的平台账号不存在、已停用或不是业务人员")
    if binding.source_id is None:
        raise DingTalkSourceRequired("请先在 ForgeSight 为钉钉账号选择可访问的数据源")
    if not source_exists(session, binding.source_id):
        raise DingTalkSourceRequired("绑定的数据源不存在或已被移除")
    if binding.source_id not in allowed_source_ids(session, binding.user_id):
        raise DingTalkSourceRequired("绑定的数据源已不在当前账号授权范围内")


def allowed_source_ids(session, user_id: str, now: datetime | None = None) -> set[int]:
    """兼容既有 API；权限规则由所有外部 IM 入口共用。"""

    return shared_allowed_source_ids(session, user_id, now)


def _external_user_id(message: InboundMessage) -> str:
    return message.sender_union_id or message.sender_open_id or message.sender_staff_id


def _context_chat_id(message: InboundMessage, external_user_id: str) -> str:
    # 群里不同用户不能共享 Agent 历史，避免跨账号上下文和数据范围泄漏。
    if message.conversation_type == "2":
        digest = hashlib.sha256(
            f"{message.conversation_id}\n{external_user_id}".encode("utf-8")
        ).hexdigest()
        return f"group-user:{digest}"
    return message.conversation_id


def accept_message(
    message: InboundMessage,
    *,
    session_factory: Callable = PlatformSession,
    _retry_on_conflict: bool = True,
) -> AcceptedJob:
    """验证绑定并幂等落库；这里不等待 Agent。"""

    question = message.question.strip()
    external_user_id = _external_user_id(message)
    if not message.message_id or not message.conversation_id or not external_user_id:
        raise UnsupportedDingTalkMessage("消息缺少 msgId、conversationId 或发送人标识")
    if message.conversation_type not in {"1", "2"}:
        raise UnsupportedDingTalkMessage("不支持的钉钉会话类型")
    if not question:
        raise UnsupportedDingTalkMessage("请发送非空文本问题")
    if len(question) > 2000:
        raise UnsupportedDingTalkMessage("问题不能超过 2000 个字符")

    with session_factory() as session:
        binding = _binding_for_message(session, message)
        if binding is None:
            raise DingTalkBindingRequired(external_user_id)
        _validate_binding(session, binding)

        existing = session.query(ExternalChatJob).filter_by(
            channel=CHANNEL,
            external_message_id=message.message_id,
        ).first()
        if existing is not None:
            return AcceptedJob(existing.id, existing.analysis_session_id or "", False)

        context_chat_id = _context_chat_id(message, external_user_id)
        conversation = session.query(ExternalChatConversation).filter_by(
            channel=CHANNEL,
            tenant_key=message.corp_id,
            external_chat_id=context_chat_id,
        ).first()
        binding_changed = conversation is not None and (
            conversation.platform_user_id != binding.user_id
            or conversation.source_id != binding.source_id
        )
        if conversation is None or conversation.status != "active" or binding_changed:
            analysis_session = AnalysisSession(
                id=new_id("ses"),
                title="钉钉业务问析",
                user_id=binding.user_id,
                source_id=binding.source_id,
            )
            session.add(analysis_session)
            if conversation is None:
                conversation = ExternalChatConversation(
                    id=new_id("ecc"),
                    channel=CHANNEL,
                    tenant_key=message.corp_id,
                    external_chat_id=context_chat_id,
                    external_user_id=external_user_id,
                    platform_user_id=binding.user_id,
                    source_id=binding.source_id,
                    analysis_session_id=analysis_session.id,
                )
                session.add(conversation)
            else:
                conversation.external_user_id = external_user_id
                conversation.platform_user_id = binding.user_id
                conversation.source_id = binding.source_id
                conversation.analysis_session_id = analysis_session.id
                conversation.status = "active"
        else:
            conversation.external_user_id = external_user_id
            conversation.updated_at = datetime.now()

        job = ExternalChatJob(
            id=new_id("ecj"),
            channel=CHANNEL,
            tenant_key=message.corp_id,
            external_message_id=message.message_id,
            external_chat_id=context_chat_id,
            external_user_id=external_user_id,
            question=question,
            reply_target_json={
                "conversationId": message.conversation_id,
                "conversationType": message.conversation_type,
                "senderStaffId": message.sender_staff_id,
            },
            conversation_id=conversation.id,
            analysis_session_id=conversation.analysis_session_id,
        )
        session.add(job)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            existing = session.query(ExternalChatJob).filter_by(
                channel=CHANNEL,
                external_message_id=message.message_id,
            ).first()
            if existing is not None:
                return AcceptedJob(existing.id, existing.analysis_session_id or "", False)
            if _retry_on_conflict:
                return accept_message(
                    message,
                    session_factory=session_factory,
                    _retry_on_conflict=False,
                )
            raise
        return AcceptedJob(job.id, conversation.analysis_session_id, True)


def claim_job(
    worker_id: str,
    *,
    session_factory: Callable = PlatformSession,
    lease_seconds: int | None = None,
    max_attempts: int | None = None,
) -> ClaimedJob | None:
    """按会话顺序认领任务，并恢复租约过期的 worker。"""

    now = datetime.now()
    lease_seconds = lease_seconds or settings.dingtalk_bot_lease_seconds
    max_attempts = max_attempts or settings.dingtalk_bot_max_attempts
    with session_factory() as session:
        # worker 在最后一次租约中崩溃时直接终止该任务，避免永久阻塞同会话后续消息。
        exhausted = session.query(ExternalChatJob).filter(
            ExternalChatJob.channel == CHANNEL,
            ExternalChatJob.status == "processing",
            ExternalChatJob.attempt_count >= max_attempts,
            ExternalChatJob.lease_expires_at.is_not(None),
            ExternalChatJob.lease_expires_at < now,
        ).all()
        for item in exhausted:
            item.status = "failed"
            item.error_summary = item.error_summary or "worker lease expired after maximum attempts"
            item.completed_at = now
            item.lease_owner = None
            item.lease_expires_at = None
        if exhausted:
            session.flush()
        prior = aliased(ExternalChatJob)
        earlier_open = session.query(prior.id).filter(
            prior.channel == CHANNEL,
            prior.external_chat_id == ExternalChatJob.external_chat_id,
            prior.status.in_(["pending", "processing", "retry_wait"]),
            or_(
                prior.created_at < ExternalChatJob.created_at,
                and_(prior.created_at == ExternalChatJob.created_at, prior.id < ExternalChatJob.id),
            ),
        ).exists()
        query = (
            session.query(ExternalChatJob)
            .filter(
                ExternalChatJob.channel == CHANNEL,
                ExternalChatJob.attempt_count < max_attempts,
                ~earlier_open,
                or_(
                    and_(
                        ExternalChatJob.status.in_(["pending", "retry_wait"]),
                        ExternalChatJob.next_attempt_at <= now,
                    ),
                    and_(
                        ExternalChatJob.status == "processing",
                        or_(
                            ExternalChatJob.lease_expires_at.is_(None),
                            ExternalChatJob.lease_expires_at < now,
                        ),
                    ),
                ),
            )
            .order_by(ExternalChatJob.created_at, ExternalChatJob.id)
        )
        if session.bind and session.bind.dialect.name == "postgresql":
            query = query.with_for_update(skip_locked=True)
        row = query.first()
        if row is None:
            return None
        row.status = "processing"
        row.attempt_count += 1
        row.lease_owner = worker_id
        row.lease_expires_at = now + timedelta(seconds=max(30, lease_seconds))
        session.commit()
        return ClaimedJob(
            id=row.id,
            external_chat_id=row.external_chat_id,
            external_message_id=row.external_message_id,
            reply_target=dict(row.reply_target_json or {}),
            attempt_count=row.attempt_count,
        )


def _pending_interaction(session, analysis_session_id: str) -> AnalysisInteraction | None:
    return (
        session.query(AnalysisInteraction)
        .join(AnalysisRun, AnalysisRun.id == AnalysisInteraction.run_id)
        .filter(
            AnalysisRun.session_id == analysis_session_id,
            AnalysisInteraction.status == "pending",
        )
        .order_by(AnalysisInteraction.created_at.desc())
        .first()
    )


def _normalize_answer(interaction: AnalysisInteraction, answer: str) -> str:
    normalized = answer.strip()
    options = (interaction.prompt_json or {}).get("options", [])
    if normalized.isdigit():
        index = int(normalized) - 1
        if 0 <= index < len(options):
            option = options[index]
            if isinstance(option, dict):
                return str(option.get("value") or option.get("label") or normalized).strip()
    for option in options:
        if not isinstance(option, dict):
            continue
        label = str(option.get("label") or "").strip()
        value = str(option.get("value") or "").strip()
        if normalized in {label, value}:
            return value or normalized
    return normalized


def execute_job(
    job_id: str,
    *,
    session_factory: Callable = PlatformSession,
    wait_seconds: int | None = None,
) -> dict:
    """创建或恢复 Agent Run，返回成功、失败或待澄清的稳定快照。"""

    with session_factory() as session:
        row = session.get(ExternalChatJob, job_id)
        if row is None:
            raise KeyError(f"外部消息任务 {job_id} 不存在")
        run_id = row.run_id
        analysis_session_id = row.analysis_session_id
        question = row.question
        conversation = session.get(ExternalChatConversation, row.conversation_id)
        if conversation is None or conversation.status != "active":
            raise DingTalkBindingRequired("钉钉会话已解绑或归档")
        binding = session.query(DingTalkBinding).filter_by(
            user_id=conversation.platform_user_id,
            bind_status="bound",
        ).first()
        if binding is None:
            raise DingTalkBindingRequired("钉钉账号已解绑或停用")
        _validate_binding(session, binding)
        if binding.source_id != conversation.source_id:
            raise DingTalkSourceRequired("钉钉会话的数据源绑定已变化，请重新发送问题")
        interaction = _pending_interaction(session, analysis_session_id) if analysis_session_id else None
    if not analysis_session_id:
        raise RuntimeError("外部消息任务没有关联 Analysis Session")

    if run_id is None:
        if interaction is not None:
            accepted = analysis_service.respond_to_interaction(
                interaction.id,
                _normalize_answer(interaction, question),
            )
        else:
            accepted = analysis_service.create_run(
                analysis_session_id,
                question,
                idempotency_key=f"dingtalk:{job_id}",
            )
        run_id = accepted["runId"]
        with session_factory() as session:
            row = session.get(ExternalChatJob, job_id)
            row.run_id = run_id
            session.commit()

    wait_seconds = wait_seconds or settings.dingtalk_bot_result_wait_seconds
    deadline = time.monotonic() + max(1, wait_seconds)
    while True:
        snapshot = analysis_service.get_run(run_id)
        if snapshot.get("status") in STABLE_RUN_STATUSES:
            return snapshot
        if time.monotonic() >= deadline:
            raise JobStillRunning("Agent 仍在后台执行")
        time.sleep(max(0.2, settings.dingtalk_bot_poll_seconds))


def complete_job(
    job_id: str,
    result_text: str,
    result_message_id: str | None,
    *,
    session_factory: Callable = PlatformSession,
) -> None:
    with session_factory() as session:
        row = session.get(ExternalChatJob, job_id)
        if row is None:
            return
        row.status = "completed"
        row.result_text = result_text
        row.result_message_id = result_message_id or "sent"
        row.error_summary = None
        row.lease_owner = None
        row.lease_expires_at = None
        row.completed_at = datetime.now()
        session.commit()


def fail_job(
    job_id: str,
    error: Exception,
    *,
    session_factory: Callable = PlatformSession,
    max_attempts: int | None = None,
) -> None:
    max_attempts = max_attempts or settings.dingtalk_bot_max_attempts
    with session_factory() as session:
        row = session.get(ExternalChatJob, job_id)
        if row is None:
            return
        row.error_summary = f"{type(error).__name__}: {error}"[:500]
        row.lease_owner = None
        row.lease_expires_at = None
        if row.attempt_count >= max_attempts:
            row.status = "failed"
            row.completed_at = datetime.now()
        else:
            row.status = "retry_wait"
            row.next_attempt_at = datetime.now() + timedelta(
                seconds=min(60, 2 ** max(0, row.attempt_count - 1))
            )
        session.commit()


def reject_job(
    job_id: str,
    error: Exception,
    *,
    session_factory: Callable = PlatformSession,
) -> None:
    """权限或绑定已撤销时终止任务，不进行无意义重试。"""

    with session_factory() as session:
        row = session.get(ExternalChatJob, job_id)
        if row is None:
            return
        row.status = "failed"
        row.error_summary = f"{type(error).__name__}: {error}"[:500]
        row.lease_owner = None
        row.lease_expires_at = None
        row.completed_at = datetime.now()
        session.commit()


def defer_job(
    job_id: str,
    *,
    session_factory: Callable = PlatformSession,
    delay_seconds: int = 2,
) -> None:
    """Agent 仍运行时释放租约；等待轮询不消耗失败重试次数。"""

    with session_factory() as session:
        row = session.get(ExternalChatJob, job_id)
        if row is None:
            return
        row.status = "retry_wait"
        row.attempt_count = max(0, row.attempt_count - 1)
        row.next_attempt_at = datetime.now() + timedelta(seconds=max(1, delay_seconds))
        row.lease_owner = None
        row.lease_expires_at = None
        session.commit()


def format_run_reply(snapshot: dict) -> str:
    status = str(snapshot.get("status") or "unknown")
    if status == "waiting_user":
        prompt = (snapshot.get("interaction") or {}).get("prompt") or {}
        lines = [str(prompt.get("question") or prompt.get("message") or "需要补充分析条件")]
        for index, option in enumerate(prompt.get("options") or [], 1):
            label = option.get("label") if isinstance(option, dict) else option
            if label:
                lines.append(f"{index}. {label}")
        lines.append("请直接回复选项编号、选项文字或自定义答案，我会继续当前分析。")
        body = "\n".join(lines)
    elif status in {"failed", "blocked", "cancelled"}:
        error = snapshot.get("error") or {}
        body = str(error.get("userMessage") or error.get("message") or "本次分析未能完成")
    else:
        assistant = snapshot.get("assistantMessage") or {}
        body = str(assistant.get("content") or "分析已完成，请在 ForgeSight 查看完整证据链。")
    text = f"{body.strip()}\n\n状态：{status}"
    return text if len(text) <= 7000 else text[:6800] + "\n\n内容较长，已截断。"
