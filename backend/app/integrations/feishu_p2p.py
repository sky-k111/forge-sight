"""飞书企业自建应用机器人：单聊消息 ↔ ForgeSight Agent。

这不是飞书群自定义 Webhook。事件处理器只负责校验、绑定与持久化收件，
真正的分析和回复由独立 worker 完成，避免超过飞书事件确认时限。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import socket
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable
from urllib.parse import urlparse

from sqlalchemy import and_, or_
from sqlalchemy.orm import aliased

# lark-channel-sdk 的 WebSocket 客户端会在导入阶段创建其事件循环。
# 必须在 asyncio.run() 建立业务事件循环之前导入，否则会把正在运行的
# asyncio loop 交给另一个线程并触发 "This event loop is already running"。
try:
    from lark_channel import (
        DedupConfig,
        FeishuChannel,
        InboundConfig,
        MediaSource,
        PolicyConfig,
        SafetyConfig,
        SecurityConfig,
        TransportConfig,
        new_card,
    )
except ImportError:  # pragma: no cover - 启动时转换为可读配置错误
    DedupConfig = FeishuChannel = InboundConfig = MediaSource = PolicyConfig = SafetyConfig = SecurityConfig = TransportConfig = new_card = None

from app.agent import service as analysis_service
from app.agent.ids import new_id
from app.config import Settings, settings
from app.db.models import (
    AnalysisInteraction,
    AnalysisRun,
    AnalysisSession,
    ExternalChatConversation,
    ExternalChatJob,
    FeishuBinding,
)
from app.db.platform import PlatformSession
from app.integrations.access import allowed_source_ids, resolve_business_user, source_exists, tenant_id_for_source
from app.integrations.feishu_charts import build_chart_specs, render_chart_png

log = logging.getLogger(__name__)
CHANNEL = "feishu"
TERMINAL_STATUSES = {
    "succeeded",
    "failed",
    "blocked",
    "cancelled",
    "waiting_user",
    "modeling-proposal",
}


class FeishuBotConfigurationError(RuntimeError):
    pass


class FeishuBindingRequired(PermissionError):
    def __init__(self, external_user_id: str):
        self.external_user_id = external_user_id
        super().__init__(f"飞书用户 {external_user_id} 尚未绑定 ForgeSight 账号")


class FeishuSourceRequired(PermissionError):
    pass


class UnsupportedFeishuMessage(ValueError):
    pass


@dataclass(frozen=True)
class Binding:
    user_id: str
    source_id: int


@dataclass(frozen=True)
class AcceptedJob:
    id: str
    external_chat_id: str
    external_message_id: str
    created: bool


@dataclass(frozen=True)
class ClaimedJob:
    id: str
    external_chat_id: str
    external_message_id: str
    ack_message_id: str | None
    attempt_count: int


def configured(config: Settings = settings) -> bool:
    return bool(
        config.feishu_bot_enabled
        and config.feishu_bot_app_id.strip()
        and config.feishu_bot_app_secret.strip()
    )


def validate_configuration(config: Settings = settings) -> None:
    if not config.feishu_bot_enabled:
        raise FeishuBotConfigurationError("FEISHU_BOT_ENABLED 尚未开启")
    if not config.feishu_bot_app_id.strip() or not config.feishu_bot_app_secret.strip():
        raise FeishuBotConfigurationError("请配置 FEISHU_BOT_APP_ID 与 FEISHU_BOT_APP_SECRET")
    # 允许先以空绑定启动：未知用户只会收到自己的 open_id，不会进入分析链路，
    # 管理员据此完成显式绑定后再重启机器人。
    parse_bindings(config.feishu_bot_bindings_json)


def parse_bindings(raw: str) -> dict[str, Binding]:
    try:
        payload = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise FeishuBotConfigurationError("FEISHU_BOT_BINDINGS_JSON 不是合法 JSON") from exc
    if not isinstance(payload, dict):
        raise FeishuBotConfigurationError("FEISHU_BOT_BINDINGS_JSON 必须是对象")
    result: dict[str, Binding] = {}
    for open_id, value in payload.items():
        if not isinstance(open_id, str) or not isinstance(value, dict):
            raise FeishuBotConfigurationError("飞书绑定必须使用 open_id -> 配置对象")
        user_id = str(value.get("userId") or "").strip()
        try:
            source_id = int(value.get("sourceId"))
        except (TypeError, ValueError) as exc:
            raise FeishuBotConfigurationError(f"{open_id} 的 sourceId 必须是正整数") from exc
        if not user_id or source_id < 1:
            raise FeishuBotConfigurationError(f"{open_id} 缺少 userId 或有效 sourceId")
        result[open_id] = Binding(user_id=user_id, source_id=source_id)
    return result


def _chat_type(message: Any) -> str:
    return str(
        getattr(message, "chat_type", "")
        or getattr(getattr(message, "conversation", None), "chat_type", "")
    ).lower()


def _chat_id(message: Any) -> str:
    return str(
        getattr(message, "chat_id", "")
        or getattr(getattr(message, "conversation", None), "chat_id", "")
    ).strip()


def _sender_id(message: Any) -> str:
    sender = getattr(message, "sender", None)
    return str(
        getattr(message, "sender_id", "")
        or getattr(sender, "open_id", "")
        or getattr(sender, "user_id", "")
    ).strip()


def _tenant_key(message: Any, config: Settings) -> str:
    raw = getattr(message, "raw", {}) or {}
    if isinstance(raw, dict):
        header = raw.get("header") or {}
        event = raw.get("event") or {}
        for value in (
            header.get("tenant_key") if isinstance(header, dict) else None,
            event.get("tenant_key") if isinstance(event, dict) else None,
            raw.get("tenant_key"),
        ):
            if value:
                return str(value)
    # 企业自建应用通常服务单租户；没有 tenant_key 时用 app_id 隔离命名空间。
    return config.feishu_bot_app_id.strip()


def _question(message: Any) -> str:
    raw_type = str(getattr(message, "raw_content_type", "") or "text").lower()
    if raw_type not in {"text", "post"}:
        raise UnsupportedFeishuMessage("当前仅支持文本或富文本消息")
    text = str(
        getattr(message, "safe_content_text", "")
        or getattr(message, "content_text", "")
        or ""
    ).strip()
    if not text:
        raise UnsupportedFeishuMessage("没有读取到可分析的文字内容")
    if len(text) > 2000:
        raise UnsupportedFeishuMessage("问题不能超过 2000 个字符")
    return text


def _validate_binding(session, binding: Binding) -> Binding:
    if not source_exists(session, binding.source_id):
        raise FeishuBotConfigurationError(f"绑定的数据源 {binding.source_id} 不存在")
    if binding.user_id == "local-test-user":
        return binding
    user = resolve_business_user(session, binding.user_id)
    if user is None or not user.enabled or user.role != "business":
        raise FeishuBotConfigurationError(
            f"绑定账号 {binding.user_id} 不存在、已停用或不是业务人员"
        )
    # 授权只看绑定源所属企业，避免用户在其它企业的策略影响本渠道判断。
    bound_tenant = tenant_id_for_source(session, binding.source_id)
    if binding.source_id not in allowed_source_ids(session, user.id, tenant_id=bound_tenant):
        raise FeishuBotConfigurationError(
            f"绑定的数据源 {binding.source_id} 不在账号 {user.username} 的授权范围内"
        )
    return Binding(user_id=user.id, source_id=binding.source_id)


def _binding_for_message(
    session,
    external_user_id: str,
    tenant_key: str,
    config: Settings,
) -> Binding | None:
    """优先使用 OAuth 数据库绑定，保留静态 JSON 作为旧部署退化通道。"""

    identity_query = session.query(FeishuBinding).filter(
        FeishuBinding.bind_status == "bound",
        or_(
            FeishuBinding.open_id == external_user_id,
            FeishuBinding.union_id == external_user_id,
            FeishuBinding.employee_id == external_user_id,
        ),
    )
    database_binding = None
    if tenant_key:
        database_binding = identity_query.filter(
            or_(FeishuBinding.tenant_key == tenant_key, FeishuBinding.tenant_key == "")
        ).first()
    if database_binding is None:
        # lark-channel-sdk 的规范化消息可能不保留事件 tenant_key；此时
        # _tenant_key() 会回退为 app_id。仅当身份在全库唯一时允许回退，
        # 避免多租户下相同外部标识被错误关联。
        candidates = identity_query.limit(2).all()
        if len(candidates) == 1:
            database_binding = candidates[0]
    if database_binding is not None:
        if database_binding.source_id is None:
            raise FeishuSourceRequired("请先在 ForgeSight 为飞书账号选择可访问的数据源")
        return Binding(
            user_id=database_binding.user_id,
            source_id=database_binding.source_id,
        )

    return parse_bindings(config.feishu_bot_bindings_json).get(external_user_id)


def accept_message(
    message: Any,
    *,
    config: Settings = settings,
    session_factory: Callable = PlatformSession,
) -> AcceptedJob:
    """快速持久化一条飞书单聊消息；不得在这里执行 Agent。"""

    if _chat_type(message) != "p2p":
        raise UnsupportedFeishuMessage("仅接受与机器人的单聊消息")
    external_message_id = str(getattr(message, "message_id", "") or getattr(message, "id", "")).strip()
    external_chat_id = _chat_id(message)
    external_user_id = _sender_id(message)
    if not external_message_id or not external_chat_id or not external_user_id:
        raise UnsupportedFeishuMessage("飞书消息缺少 message_id、chat_id 或发送人标识")
    question = _question(message)
    tenant_key = _tenant_key(message, config)

    with session_factory() as session:
        binding = _binding_for_message(session, external_user_id, tenant_key, config)
        if binding is None:
            raise FeishuBindingRequired(external_user_id)
        binding = _validate_binding(session, binding)
        existing_job = session.query(ExternalChatJob).filter_by(
            channel=CHANNEL,
            external_message_id=external_message_id,
        ).first()
        if existing_job is not None:
            return AcceptedJob(
                id=existing_job.id,
                external_chat_id=existing_job.external_chat_id,
                external_message_id=existing_job.external_message_id,
                created=False,
            )

        conversation = session.query(ExternalChatConversation).filter_by(
            channel=CHANNEL,
            tenant_key=tenant_key,
            external_chat_id=external_chat_id,
        ).first()
        binding_changed = conversation is not None and (
            conversation.platform_user_id != binding.user_id
            or conversation.source_id != binding.source_id
        )
        if conversation is None or conversation.status != "active" or binding_changed:
            if conversation is not None:
                conversation.status = "archived"
            analysis_session = AnalysisSession(
                id=new_id("ses"),
                title="飞书业务问析",
                user_id=binding.user_id,
                source_id=binding.source_id,
                tenant_id=tenant_id_for_source(session, binding.source_id),
            )
            session.add(analysis_session)
            if conversation is None:
                conversation = ExternalChatConversation(
                    id=new_id("ecc"),
                    channel=CHANNEL,
                    tenant_key=tenant_key,
                    external_chat_id=external_chat_id,
                    external_user_id=external_user_id,
                    platform_user_id=binding.user_id,
                    source_id=binding.source_id,
                    analysis_session_id=analysis_session.id,
                )
                session.add(conversation)
            else:
                conversation.status = "active"
                conversation.external_user_id = external_user_id
                conversation.platform_user_id = binding.user_id
                conversation.source_id = binding.source_id
                conversation.analysis_session_id = analysis_session.id
        else:
            conversation.external_user_id = external_user_id
            conversation.updated_at = datetime.now()

        job = ExternalChatJob(
            id=new_id("ecj"),
            channel=CHANNEL,
            tenant_key=tenant_key,
            external_message_id=external_message_id,
            external_chat_id=external_chat_id,
            external_user_id=external_user_id,
            question=question,
            conversation_id=conversation.id,
            analysis_session_id=conversation.analysis_session_id,
        )
        session.add(job)
        session.commit()
        return AcceptedJob(
            id=job.id,
            external_chat_id=job.external_chat_id,
            external_message_id=job.external_message_id,
            created=True,
        )


def claim_job(
    worker_id: str,
    *,
    config: Settings = settings,
    session_factory: Callable = PlatformSession,
) -> ClaimedJob | None:
    now = datetime.now()
    with session_factory() as session:
        prior = aliased(ExternalChatJob)
        has_earlier_open_job = session.query(prior.id).filter(
            prior.channel == CHANNEL,
            prior.external_chat_id == ExternalChatJob.external_chat_id,
            prior.status.in_(["pending", "processing"]),
            or_(
                prior.created_at < ExternalChatJob.created_at,
                and_(
                    prior.created_at == ExternalChatJob.created_at,
                    prior.id < ExternalChatJob.id,
                ),
            ),
        ).exists()
        query = (
            session.query(ExternalChatJob)
            .filter(
                ExternalChatJob.channel == CHANNEL,
                ~has_earlier_open_job,
                or_(
                    ExternalChatJob.status == "pending",
                    (ExternalChatJob.status == "processing")
                    & or_(
                        ExternalChatJob.lease_expires_at.is_(None),
                        ExternalChatJob.lease_expires_at < now,
                    ),
                ),
            )
            .order_by(ExternalChatJob.created_at)
        )
        if session.bind and session.bind.dialect.name == "postgresql":
            query = query.with_for_update(skip_locked=True)
        row = query.first()
        if row is None:
            return None
        row.status = "processing"
        row.attempt_count += 1
        row.lease_owner = worker_id
        row.lease_expires_at = now + timedelta(seconds=max(30, config.feishu_bot_lease_seconds))
        session.commit()
        return ClaimedJob(
            id=row.id,
            external_chat_id=row.external_chat_id,
            external_message_id=row.external_message_id,
            ack_message_id=row.ack_message_id,
            attempt_count=row.attempt_count,
        )


def mark_ack(job_id: str, message_id: str | None, *, session_factory: Callable = PlatformSession) -> None:
    with session_factory() as session:
        row = session.query(ExternalChatJob).filter_by(id=job_id).first()
        if row is not None and not row.ack_message_id:
            row.ack_message_id = message_id or "sent"
            session.commit()


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


def _normalize_interaction_answer(interaction: AnalysisInteraction, answer: str) -> str:
    normalized = answer.strip()
    for option in (interaction.prompt_json or {}).get("options", []):
        if not isinstance(option, dict):
            continue
        if normalized in {str(option.get("label") or "").strip(), str(option.get("value") or "").strip()}:
            return str(option.get("value") or normalized).strip()
    return normalized


def execute_job(
    job_id: str,
    *,
    config: Settings = settings,
    session_factory: Callable = PlatformSession,
) -> str:
    """创建或恢复 Agent Run，等待一个对用户可回复的稳定状态。"""

    with session_factory() as session:
        row = session.query(ExternalChatJob).filter_by(id=job_id).first()
        if row is None:
            raise KeyError(f"外部消息任务 {job_id} 不存在")
        conversation = session.get(ExternalChatConversation, row.conversation_id)
        if conversation is None or conversation.status != "active":
            raise FeishuBotConfigurationError("飞书会话已解绑或归档")
        current_binding = _binding_for_message(
            session,
            row.external_user_id,
            row.tenant_key,
            config,
        )
        if current_binding is None:
            raise FeishuBindingRequired(row.external_user_id)
        current_binding = _validate_binding(session, current_binding)
        if (
            current_binding.user_id != conversation.platform_user_id
            or current_binding.source_id != conversation.source_id
        ):
            raise FeishuBotConfigurationError("飞书账号或数据源绑定已变化，请重新发送问题")
        tenant_id = tenant_id_for_source(session, conversation.source_id)
        run_id = row.run_id
        analysis_session_id = row.analysis_session_id
        question = row.question
        interaction = _pending_interaction(session, analysis_session_id) if analysis_session_id else None

    if not analysis_session_id:
        raise RuntimeError("外部消息任务没有关联分析会话")
    if run_id is None:
        if interaction is not None:
            accepted = analysis_service.respond_to_interaction(
                interaction.id,
                _normalize_interaction_answer(interaction, question),
            )
        else:
            accepted = analysis_service.create_run(
                analysis_session_id,
                question,
                idempotency_key=f"feishu:{job_id}",
            )
        run_id = accepted["runId"]
        with session_factory() as session:
            row = session.query(ExternalChatJob).filter_by(id=job_id).one()
            row.run_id = run_id
            session.commit()

    deadline = time.monotonic() + max(config.agent_max_duration_seconds + 30, 90)
    while True:
        snapshot = analysis_service.get_run(run_id)
        if snapshot["status"] in TERMINAL_STATUSES:
            return format_run_reply(snapshot, config=config)
        if time.monotonic() >= deadline:
            return (
                "分析仍在后台执行，已保留任务状态。你可以稍后在 ForgeSight 网页端查看。\n\n"
                f"[打开智能问析]({config.public_app_url.rstrip('/')}/workspace/analysis)"
            )
        time.sleep(max(0.2, config.feishu_bot_poll_seconds))


def _clarification_text(snapshot: dict) -> str:
    prompt = (snapshot.get("interaction") or {}).get("prompt") or {}
    question = str(prompt.get("question") or prompt.get("message") or "需要补充分析条件")
    options = prompt.get("options") or []
    lines = ["**需要你补充一个条件**", question]
    for index, option in enumerate(options, 1):
        if isinstance(option, dict):
            label = option.get("label") or option.get("value")
        else:
            label = option
        if label:
            lines.append(f"{index}. {label}")
    lines.append("请直接回复选项文字或自定义答案，我会从当前步骤继续。")
    return "\n\n".join(lines)


def _failure_text(snapshot: dict) -> str:
    error = snapshot.get("error") or {}
    detail = (
        error.get("userMessage")
        or error.get("message")
        or error.get("detail")
        or snapshot.get("errorCode")
        or "本次分析未能完成"
    )
    return f"**分析未完成**\n\n{detail}"


def format_run_reply(snapshot: dict, *, config: Settings = settings) -> str:
    status = snapshot.get("status")
    if status == "waiting_user":
        body = _clarification_text(snapshot)
    elif status in {"failed", "blocked", "cancelled"}:
        body = _failure_text(snapshot)
    else:
        assistant = snapshot.get("assistantMessage") or {}
        body = str(assistant.get("content") or "分析已完成，请在网页端查看完整结果与证据链。")

    usage = snapshot.get("usage") or {}
    footer = [f"状态：{status or 'unknown'}"]
    if usage.get("totalTokens"):
        footer.append(f"模型用量：{usage['totalTokens']} Token")
    footer.append(
        f"[查看完整图表与证据链]({config.public_app_url.rstrip('/')}/workspace/analysis)"
    )
    text = f"{body.strip()}\n\n---\n" + " · ".join(footer)
    # 消息正文留出协议与富文本转换余量；完整内容仍可在站内查看。
    return text if len(text) <= 7000 else text[:6800] + "\n\n内容较长，已截断，请打开网页查看完整结果。"


def _card_body_text(snapshot: dict) -> str:
    status = snapshot.get("status")
    if status == "waiting_user":
        return _clarification_text(snapshot)
    if status in {"failed", "blocked", "cancelled"}:
        return _failure_text(snapshot)
    assistant = snapshot.get("assistantMessage") or {}
    return str(assistant.get("content") or "分析已完成。")


def _has_public_app_url(config: Settings) -> bool:
    hostname = (urlparse(config.public_app_url).hostname or "").lower()
    return bool(hostname and hostname not in {"127.0.0.1", "localhost", "::1"})


async def send_run_reply(
    channel,
    job: ClaimedJob,
    result_text: str,
    snapshot: dict,
    *,
    config: Settings = settings,
):
    """优先发送图表卡片；图片权限或上传异常时降级为原有文字回复。"""

    specs = build_chart_specs(snapshot)
    if not specs or snapshot.get("status") != "succeeded":
        return await channel.send(
            job.external_chat_id,
            {"markdown": result_text},
            {"reply_to": job.external_message_id, "uuid": _message_uuid(job.id, "result")},
        )
    try:
        image_keys = []
        for index, spec in enumerate(specs):
            png = await asyncio.to_thread(render_chart_png, spec)
            image_keys.append(
                await channel.upload_media(
                    MediaSource(kind="buffer", buffer=png),
                    kind="image",
                    file_name=f"forgesight-{job.id}-{index + 1}.png",
                )
            )

        body = _card_body_text(snapshot).strip()
        if len(body) > 4200:
            body = body[:4200] + "\n\n内容较长，已截断。"
        usage = snapshot.get("usage") or {}
        card = (
            new_card()
            .config(wide_screen_mode=True)
            .header("ForgeSight 分析结果", subtitle=f"共生成 {len(image_keys)} 张图表", template="green")
            .markdown(body)
            .divider()
        )
        for spec, image_key in zip(specs, image_keys):
            card.image(image_key, alt=spec.title, title=spec.title)
        footer = [f"状态：{snapshot.get('status') or 'unknown'}"]
        if usage.get("totalTokens"):
            footer.append(f"模型用量：{usage['totalTokens']} Token")
        card.footer(" · ".join(footer))
        if _has_public_app_url(config):
            card.button(
                "查看完整图表与证据链",
                style="primary",
                url=f"{config.public_app_url.rstrip('/')}/workspace/analysis",
            )
        else:
            card.footer("完整证据链暂请在本机 ForgeSight 网页端查看")
        return await channel.send(
            job.external_chat_id,
            {"card": card.to_dict()},
            {"reply_to": job.external_message_id, "uuid": _message_uuid(job.id, "result-card")},
        )
    except Exception:  # noqa: BLE001 - 可视化失败不能把已经成功的分析标记为失败
        log.exception("飞书图表卡片发送失败，降级为文字回复 job=%s", job.id)
        return await channel.send(
            job.external_chat_id,
            {"markdown": result_text},
            {"reply_to": job.external_message_id, "uuid": _message_uuid(job.id, "result-fallback")},
        )


def complete_job(
    job_id: str,
    result_text: str,
    message_id: str | None,
    *,
    session_factory: Callable = PlatformSession,
) -> None:
    with session_factory() as session:
        row = session.query(ExternalChatJob).filter_by(id=job_id).one()
        row.status = "completed"
        row.result_text = result_text
        row.result_message_id = message_id or "sent"
        row.lease_owner = None
        row.lease_expires_at = None
        row.completed_at = datetime.now()
        session.commit()


def fail_job(
    job_id: str,
    error: Exception,
    *,
    config: Settings = settings,
    session_factory: Callable = PlatformSession,
) -> bool:
    """记录失败。返回 True 表示已进入终态，不再自动重试。"""

    with session_factory() as session:
        row = session.query(ExternalChatJob).filter_by(id=job_id).one()
        terminal = row.attempt_count >= max(1, config.feishu_bot_max_attempts)
        row.status = "failed" if terminal else "pending"
        row.error_summary = f"{type(error).__name__}: {str(error)[:420]}"
        row.lease_owner = None
        row.lease_expires_at = None
        if terminal:
            row.completed_at = datetime.now()
        session.commit()
        return terminal


def _message_uuid(job_id: str, phase: str) -> str:
    return hashlib.sha256(f"{job_id}:{phase}".encode("utf-8")).hexdigest()[:32]


async def run_bot(config: Settings = settings) -> None:
    """以前台长连接运行飞书机器人；由系统服务负责进程保活。"""

    validate_configuration(config)
    if FeishuChannel is None:  # pragma: no cover - 部署依赖缺失时给出明确错误
        raise FeishuBotConfigurationError(
            "缺少 lark-channel-sdk，请先安装 backend/requirements.txt"
        )

    channel = FeishuChannel(
        app_id=config.feishu_bot_app_id.strip(),
        app_secret=config.feishu_bot_app_secret.strip(),
        # 本机存在供其他工具使用的代理环境变量；飞书长连接默认直连，避免
        # 无效/过期代理让 WebSocket 在握手前直接失败。
        transport=TransportConfig(kind="ws", trust_env_proxy=False),
        inbound=InboundConfig(emit_raw_events=True),
        policy=PolicyConfig(dm_policy="open", group_policy="disabled"),
        safety=SafetyConfig(dedup=DedupConfig(ttl_seconds=12 * 3600)),
        security=SecurityConfig(mode="audit"),
    )
    stop = asyncio.Event()
    worker_id = f"feishu:{socket.gethostname()}:{id(stop)}"
    chat_locks: dict[str, asyncio.Lock] = {}
    notification_tasks: set[asyncio.Task] = set()

    async def send_system_reply(message: Any, text: str, phase: str) -> None:
        message_id = str(getattr(message, "message_id", "") or getattr(message, "id", ""))
        chat_id = _chat_id(message)
        if not chat_id:
            return
        await channel.send(
            chat_id,
            {"markdown": text},
            {"reply_to": message_id or None, "uuid": _message_uuid(message_id or chat_id, phase)},
        )

    def spawn_notification(message: Any, text: str, phase: str) -> None:
        task = asyncio.create_task(send_system_reply(message, text, phase))
        notification_tasks.add(task)
        task.add_done_callback(notification_tasks.discard)

    async def on_message(message: Any) -> None:
        log.info(
            "收到飞书规范化消息 message_id=%s chat_type=%s sender_id=%s",
            str(getattr(message, "message_id", "") or getattr(message, "id", "")),
            _chat_type(message),
            _sender_id(message),
        )
        try:
            await asyncio.to_thread(accept_message, message, config=config)
        except FeishuBindingRequired as exc:
            log.info("收到未绑定飞书用户消息 open_id=%s", exc.external_user_id)
            spawn_notification(
                message,
                "该飞书账号尚未绑定 ForgeSight 业务账号与数据源。\n\n"
                f"请打开 {config.public_app_url.rstrip('/')}/login 登录并完成飞书 OAuth 绑定。",
                "unbound",
            )
        except FeishuSourceRequired as exc:
            spawn_notification(message, str(exc), "source-required")
        except UnsupportedFeishuMessage as exc:
            spawn_notification(message, str(exc), "unsupported")
        except Exception:
            log.exception("飞书消息入队失败")
            spawn_notification(message, "消息暂时无法进入分析队列，请稍后重试。", "accept-failed")

    async def on_error(error: Exception) -> None:
        log.error("飞书 Channel 错误：%s", error)

    async def on_raw(payload: Any) -> None:
        header = payload.get("header", {}) if isinstance(payload, dict) else {}
        log.info(
            "收到飞书原始事件 event_type=%s event_id=%s",
            header.get("event_type", "unknown"),
            header.get("event_id", "unknown"),
        )

    async def on_reject(event: Any) -> None:
        log.warning(
            "飞书消息被策略层拒绝 message_id=%s reason=%s",
            getattr(event, "message_id", "unknown"),
            getattr(event, "reason", "unknown"),
        )

    async def process_claimed(job: ClaimedJob) -> None:
        lock = chat_locks.setdefault(job.external_chat_id, asyncio.Lock())
        async with lock:
            try:
                if not job.ack_message_id:
                    ack = await channel.send(
                        job.external_chat_id,
                        {"markdown": "已收到，正在调用 ForgeSight 进行分析……"},
                        {
                            "reply_to": job.external_message_id,
                            "uuid": _message_uuid(job.id, "ack"),
                        },
                    )
                    if not ack.success:
                        raise RuntimeError(f"飞书确认消息发送失败：{ack.error}")
                    await asyncio.to_thread(mark_ack, job.id, ack.message_id)
                result_text = await asyncio.to_thread(execute_job, job.id, config=config)
                with PlatformSession() as session:
                    persisted = session.query(ExternalChatJob).filter_by(id=job.id).one()
                    run_id = persisted.run_id
                snapshot = await asyncio.to_thread(analysis_service.get_run, run_id) if run_id else {}
                result = await send_run_reply(channel, job, result_text, snapshot, config=config)
                if not result.success:
                    raise RuntimeError(f"飞书分析结果发送失败：{result.error}")
                await asyncio.to_thread(complete_job, job.id, result_text, result.message_id)
            except Exception as exc:  # noqa: BLE001 - 失败需要进入持久重试/死信状态
                terminal = await asyncio.to_thread(fail_job, job.id, exc, config=config)
                log.exception("飞书任务处理失败 job=%s terminal=%s", job.id, terminal)
                if terminal:
                    try:
                        await channel.send(
                            job.external_chat_id,
                            {"markdown": "本次分析连续失败，执行记录已经保留，请到网页端查看证据链或稍后重试。"},
                            {
                                "reply_to": job.external_message_id,
                                "uuid": _message_uuid(job.id, "terminal-error"),
                            },
                        )
                    except Exception:  # noqa: BLE001 - 已记录原始故障，通知失败只写日志
                        log.exception("飞书终态错误通知失败 job=%s", job.id)

    async def worker_loop(index: int) -> None:
        log.info("飞书任务 worker 已启动 index=%s", index)
        while not stop.is_set():
            try:
                claimed = await asyncio.to_thread(
                    claim_job,
                    f"{worker_id}:{index}",
                    config=config,
                )
            except Exception:  # noqa: BLE001 - 基础设施短暂异常不能永久杀死 worker
                log.exception("飞书任务认领失败 index=%s，稍后自动重试", index)
                try:
                    await asyncio.wait_for(
                        stop.wait(),
                        timeout=max(1.0, config.feishu_bot_poll_seconds),
                    )
                except TimeoutError:
                    pass
                continue
            if claimed is None:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=max(0.2, config.feishu_bot_poll_seconds))
                except TimeoutError:
                    pass
                continue
            await process_claimed(claimed)

    channel.on("message", on_message)
    channel.on("raw", on_raw)
    channel.on("reject", on_reject)
    channel.on("error", on_error)
    workers = [
        asyncio.create_task(worker_loop(index), name=f"feishu-job-worker-{index}")
        for index in range(max(1, config.feishu_bot_max_concurrency))
    ]
    try:
        await channel.connect()
    finally:
        stop.set()
        await asyncio.gather(*workers, return_exceptions=True)
        if notification_tasks:
            await asyncio.gather(*notification_tasks, return_exceptions=True)
        await channel.disconnect()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(run_bot())


if __name__ == "__main__":  # pragma: no cover
    main()
