"""PostgreSQL 持久队列 Worker。Run 不依赖 FastAPI BackgroundTasks。"""

import logging
import socket
import threading
from datetime import datetime, timedelta

from sqlalchemy import or_

from app.agent.checkpointer import get_checkpointer
from app.agent.runner import execute_run
from app.config import settings
from app.db.models import AnalysisRun
from app.db.platform import PlatformSession

log = logging.getLogger(__name__)
_stop = threading.Event()
_thread: threading.Thread | None = None
_worker_id = f"{socket.gethostname()}:{id(_stop)}"
_monitoring_providers: dict | None = None


def _claim_run() -> str | None:
    now = datetime.now()
    with PlatformSession() as session:
        query = (
            session.query(AnalysisRun)
            .filter(
                or_(
                    AnalysisRun.status == "queued",
                    (AnalysisRun.status == "running")
                    & or_(AnalysisRun.lease_expires_at.is_(None), AnalysisRun.lease_expires_at < now),
                )
            )
            .order_by(AnalysisRun.created_at)
        )
        if session.bind and session.bind.dialect.name == "postgresql":
            query = query.with_for_update(skip_locked=True)
        run = query.first()
        if run is None:
            return None
        # claim 时立即转 running，避免其他实例在本次事务提交后再次认领同一个 queued Run。
        run.status = "running"
        run.lease_owner = _worker_id
        run.lease_expires_at = now + timedelta(
            seconds=max(settings.agent_worker_lease_seconds, settings.agent_max_duration_seconds + 30)
        )
        session.commit()
        return run.id


def _run_report_scheduler_once() -> None:
    from app.reports.automation import scan_due_subscriptions

    scan_due_subscriptions()


def _run_file_worker_once() -> None:
    from app.reports.files import run_file_worker_once

    run_file_worker_once(
        PlatformSession,
        _worker_id + ":report-file",
        settings.report_storage_dir,
    )


def _run_message_worker_once() -> None:
    from app.messages.service import expire_announcements, run_message_worker_once

    run_message_worker_once(
        session_factory=PlatformSession,
        worker_id=f"message:{_worker_id}",
    )
    with PlatformSession() as session:
        expire_announcements(session)
        session.commit()


def _run_delivery_event_worker_once() -> None:
    from app.delivery.service import run_delivery_event_worker_once

    run_delivery_event_worker_once(
        PlatformSession,
        f"delivery-event:{_worker_id}",
    )


def _run_mail_workers_once() -> None:
    from app.delivery.provider import (
        ProviderFailure,
        mail_configuration_fingerprint,
        provider_from_settings,
    )
    from app.delivery.service import (
        block_deliveries_for_configuration,
        delivery_work_pending,
        resume_configuration_blocked_deliveries,
        run_delivery_worker_once,
    )
    configuration_fingerprint = mail_configuration_fingerprint(settings)
    with PlatformSession() as session:
        pending = delivery_work_pending(
            session,
            configuration_fingerprint=configuration_fingerprint,
        )
    mail_provider = None
    if pending:
        try:
            mail_provider = provider_from_settings(settings)
            mail_provider.verify_configuration()
        except ProviderFailure as exc:
            if exc.category not in {"configuration", "network"}:
                raise
            with PlatformSession() as session:
                blocked = block_deliveries_for_configuration(
                    session,
                    configuration_fingerprint=configuration_fingerprint,
                )
                session.commit()
            if blocked:
                log.error("邮件服务配置无效，已暂停 %s 条投递", blocked)
    if mail_provider is not None:
        with PlatformSession() as session:
            resumed = resume_configuration_blocked_deliveries(
                session,
                configuration_verified=True,
                configuration_fingerprint=configuration_fingerprint,
            )
            session.commit()
        if resumed:
            log.info("邮件服务配置已恢复，重新排队 %s 条投递", resumed)
        run_delivery_worker_once(
            PlatformSession,
            f"mail-send:{_worker_id}",
            mail_provider,
            storage_root=settings.report_storage_dir,
            public_app_url=settings.public_app_url,
            attachment_max_bytes=settings.mail_attachment_max_bytes,
            max_attempts=settings.mail_max_attempts,
            lease_seconds=max(
                settings.mail_worker_lease_seconds,
                settings.mail_timeout_seconds + 30,
            ),
            configuration_fingerprint=configuration_fingerprint,
        )


def _run_im_pdf_worker_once() -> None:
    from app.delivery.im_provider import DingTalkPdfProvider, FeishuPdfProvider
    from app.delivery.im_service import run_pdf_delivery_worker_once

    providers = {
        "feishu": FeishuPdfProvider(
            settings.feishu_app_id,
            settings.feishu_app_secret,
            timeout_seconds=settings.mail_timeout_seconds,
        ),
        "dingtalk": DingTalkPdfProvider(
            settings.dingtalk_client_id,
            settings.dingtalk_client_secret,
            settings.dingtalk_agent_id,
            timeout_seconds=settings.mail_timeout_seconds,
        ),
    }
    run_pdf_delivery_worker_once(
        PlatformSession,
        f"im-pdf:{_worker_id}",
        providers,
        storage_root=settings.report_storage_dir,
        max_attempts=settings.im_pdf_max_attempts,
        lease_seconds=max(
            settings.im_pdf_worker_lease_seconds,
            settings.mail_timeout_seconds * 3 + 30,
        ),
    )


def _run_monitoring_workers_once() -> None:
    """Run one durable monitoring task and one notification delivery cycle."""
    from app.monitoring.delivery import run_delivery_worker_once, run_event_worker_once
    from app.monitoring.provider import DingTalkAlertProvider, FeishuAlertProvider
    from app.monitoring.worker import run_worker_once

    run_worker_once(
        PlatformSession,
        worker_id=f"monitoring-query:{_worker_id}",
        lease_seconds=settings.monitoring_worker_lease_seconds,
    )
    run_event_worker_once(
        PlatformSession,
        worker_id=f"monitoring-event:{_worker_id}",
        lease_seconds=settings.monitoring_delivery_lease_seconds,
    )
    global _monitoring_providers
    if _monitoring_providers is None:
        _monitoring_providers = {}
        if settings.feishu_app_id and settings.feishu_app_secret:
            _monitoring_providers["feishu"] = FeishuAlertProvider(
                settings.feishu_app_id,
                settings.feishu_app_secret,
            )
        if settings.dingtalk_client_id and settings.dingtalk_client_secret and settings.dingtalk_agent_id:
            _monitoring_providers["dingtalk"] = DingTalkAlertProvider(
                settings.dingtalk_client_id,
                settings.dingtalk_client_secret,
                settings.dingtalk_agent_id,
            )
    providers = _monitoring_providers
    if providers:
        run_delivery_worker_once(
            PlatformSession,
            worker_id=f"monitoring-send:{_worker_id}",
            providers=providers,
            public_app_url=settings.public_app_url,
            lease_seconds=settings.monitoring_delivery_lease_seconds,
        )


def _run_maintenance_cycle() -> None:
    if settings.monitoring_worker_enabled:
        try:
            _run_monitoring_workers_once()
        except Exception:  # noqa: BLE001 - 监控故障不能阻塞问析、报告或既有告警读取
            log.exception("智能异常监测任务执行失败")
    if settings.report_scheduler_enabled:
        try:
            _run_report_scheduler_once()
        except Exception:  # noqa: BLE001 - 单个调度故障不能阻塞既有 Agent 队列
            log.exception("周期周报到期扫描失败")
    try:
        _run_file_worker_once()
    except Exception:  # noqa: BLE001 - PDF 与 Excel 失败互相隔离并由任务状态重试
        log.exception("周报文件生成失败")
    try:
        _run_message_worker_once()
    except Exception:  # noqa: BLE001 - 消息故障不能阻塞 Agent Run
        log.exception("站内消息事件处理失败")
    if settings.mail_worker_enabled or settings.im_pdf_worker_enabled:
        try:
            _run_delivery_event_worker_once()
        except Exception:  # noqa: BLE001 - 单个发布事件故障由 Outbox 重试处理
            log.exception("外部投递事件展开失败")
    if settings.mail_worker_enabled:
        try:
            _run_mail_workers_once()
        except Exception:  # noqa: BLE001 - 邮件失败不能影响站内发布或 Agent Run
            log.exception("邮件投递处理失败")
    if settings.im_pdf_worker_enabled:
        try:
            _run_im_pdf_worker_once()
        except Exception:  # noqa: BLE001 - IM 平台故障不能影响报告发布与其他渠道
            log.exception("飞书/钉钉周报 PDF 投递处理失败")


def _loop() -> None:
    while not _stop.is_set():
        _run_maintenance_cycle()
        run_id = _claim_run()
        if run_id is None:
            _stop.wait(settings.agent_worker_poll_seconds)
            continue
        try:
            execute_run(run_id, checkpointer=get_checkpointer())
        except Exception:  # noqa: BLE001 - runner 已持久化结构化失败，这里记录堆栈
            log.exception("Agent Run 执行失败: %s", run_id)


def start_worker() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="forge-sight-agent-worker", daemon=True)
    _thread.start()


def stop_worker() -> None:
    _stop.set()
    if _thread and _thread.is_alive():
        _thread.join(timeout=5)
