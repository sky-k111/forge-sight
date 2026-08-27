"""Standalone alert event and external delivery worker."""

import argparse
import socket
import time

from app.config import settings
from app.db.platform import PlatformSession
from app.monitoring.delivery import run_delivery_worker_once, run_event_worker_once
from app.monitoring.provider import DingTalkAlertProvider, FeishuAlertProvider


def _providers():
    providers = {}
    if settings.feishu_app_id and settings.feishu_app_secret:
        providers["feishu"] = FeishuAlertProvider(
            settings.feishu_app_id, settings.feishu_app_secret
        )
    if settings.dingtalk_client_id and settings.dingtalk_client_secret and settings.dingtalk_agent_id:
        providers["dingtalk"] = DingTalkAlertProvider(
            settings.dingtalk_client_id,
            settings.dingtalk_client_secret,
            settings.dingtalk_agent_id,
        )
    return providers


def main() -> None:
    parser = argparse.ArgumentParser(description="ForgeSight monitoring delivery worker")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    args = parser.parse_args()
    worker_id = f"monitoring-delivery:{socket.gethostname()}"
    providers = _providers()
    while True:
        worked = run_event_worker_once(PlatformSession, worker_id=worker_id)
        if providers:
            worked = run_delivery_worker_once(
                PlatformSession,
                worker_id=worker_id,
                providers=providers,
                public_app_url=settings.public_app_url,
            ) or worked
        if args.once:
            return
        if not worked:
            time.sleep(max(0.2, args.poll_seconds))


if __name__ == "__main__":
    main()
