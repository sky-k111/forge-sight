"""FastAPI 入口：路由装配、CORS、健康检查。"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api import (
    analysis,
    auth,
    datasources,
    gateway,
    knowledge,
    messages,
    model_governance,
    modeling,
    overview,
    reports,
    resources,
    retrieval,
    security,
)
from app.config import settings
from app.db import models  # noqa: F401 - 注册 ORM 模型供 create_all 使用
from app.db.platform import Base, platform_engine

log = logging.getLogger(__name__)


def create_platform_tables():
    from app.auth.deps import validate_email_gate_configuration

    validate_email_gate_configuration()
    Base.metadata.create_all(platform_engine)
    # 确保默认数据源存在；资源目录为空时自动做首次元数据扫描
    from app.db.models import DataTable
    from app.db.platform import PlatformSession
    from app.metadata.datasources import seed_default_source
    from app.metadata.scanner import scan_default_source
    from app.knowledge.seed import seed_knowledge
    from app.reports.templates import seed_report_templates

    seed_default_source()
    with PlatformSession() as session:
        if session.query(DataTable).count() == 0:
            scan_default_source(auto_register=True)
    seed_knowledge()
    seed_report_templates()
    from app.auth.service import seed_admin

    seed_admin()
    if settings.agent_worker_enabled:
        try:
            from app.agent.checkpointer import initialize_checkpointer
            from app.agent.worker import start_worker

            initialize_checkpointer()
            start_worker()
        except Exception:  # noqa: BLE001 - 核心 API 仍可启动，健康检查/日志明确暴露失败
            log.exception("阶段 5 Checkpointer/Worker 初始化失败")


def stop_agent_runtime():
    if settings.agent_worker_enabled:
        from app.agent.checkpointer import close_checkpointer
        from app.agent.worker import stop_worker

        stop_worker()
        close_checkpointer()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    create_platform_tables()
    try:
        yield
    finally:
        stop_agent_runtime()


app = FastAPI(title="ForgeSight API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def enforce_email_identity_gate(request: Request, call_next):
    if request.method == "OPTIONS":
        return await call_next(request)
    from app.auth.deps import email_gate_response

    denied = email_gate_response(request.url.path, request.headers.get("Authorization"))
    if denied is not None:
        return denied
    return await call_next(request)

app.include_router(security.router)
app.include_router(auth.router)
app.include_router(gateway.router)
app.include_router(resources.router)
app.include_router(datasources.router)
app.include_router(knowledge.router)
app.include_router(messages.router)
app.include_router(retrieval.router)
app.include_router(analysis.router)
app.include_router(overview.router)
app.include_router(reports.router)
app.include_router(modeling.router)
app.include_router(model_governance.router)


@app.get("/health")
def health():
    status = {"service": "ok", "business_db": "down", "platform_db": "down"}
    from app.db.business import business_engine
    from app.db.platform import platform_engine

    for name, engine in (("business_db", business_engine), ("platform_db", platform_engine)):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            status[name] = "ok"
        except Exception as exc:  # noqa: BLE001 - 健康检查需要暴露连接失败原因
            status[name] = f"error: {type(exc).__name__}"
    return status
