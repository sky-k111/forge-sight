"""安全审计接口，对齐前端 api/security.js 契约。"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from fastapi.responses import StreamingResponse

from app.audit.service import export_audit_csv, list_audit_logs, review_audit_log, review_audit_logs_batch
from app.auth.deps import optional_user
from app.tenancy.service import TenantContext, require_tenant_admin

router = APIRouter(prefix="/api/security", tags=["security"])


class ReviewBatchPayload(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=200)


@router.get("/runtime")
def get_runtime_configuration(context: TenantContext = Depends(require_tenant_admin)):
    from app.config import settings
    from app.api.model_settings import _payload
    from app.metadata.datasources import list_sources

    model = _payload()
    dependencies = [
        {"icon": "database", "name": source["name"], "type": source["typeLabel"], "detail": "最近连接检查：" + source["status"], "status": "已登记", "tone": "gray"}
        for source in list_sources(context.tenant_id)
    ]
    dependencies.append({"icon": "cpu", "name": "当前模型配置", "type": model["protocol"], "detail": model["model"], "status": "已配置 · 未检测" if model["configured"] else "未配置", "tone": "gray"})
    policies = [
        {"icon": "lock", "title": "数据库只读", "description": "SQL AST 校验与白名单为强制安全边界，不允许在页面关闭", "value": "强制"},
        {"icon": "clock", "title": "查询超时", "description": "当前 API 进程的单次查询上限", "value": f"{settings.sql_timeout_seconds}s"},
        {"icon": "database", "title": "最大返回行数", "description": "超量结果需聚合或分页", "value": f"{settings.sql_max_rows:,}"},
        {"icon": "shield", "title": "查询成本预检", "description": "配置启用后执行 EXPLAIN 成本门禁", "value": "启用" if settings.sql_explain_enabled else "未启用"},
    ]
    return {"dependencies": dependencies, "policies": policies, "scope": "当前 API 进程配置快照；不代表服务连通性或实时健康状态"}


@router.get("/audit-logs")
def get_audit_logs(
    result: str | None = Query(default=None, max_length=10),
    keyword: str = Query(default="", max_length=80),
    user: str | None = Query(default=None, max_length=50),
    reviewed: str | None = Query(default=None, max_length=10),
    limit: int = Query(default=200, ge=1, le=200),
    cursor: int | None = Query(default=None, ge=1),
    snapshot_id: int | None = Query(default=None, ge=0),
    context: TenantContext = Depends(require_tenant_admin),
):
    return list_audit_logs(
        result=result,
        keyword=keyword,
        user=user,
        reviewed=reviewed,
        tenant_id=context.tenant_id,
        limit=limit, cursor=cursor, snapshot_id=snapshot_id,
    )


@router.get("/audit-logs/export")
def download_audit_logs(
    result: str | None = Query(default=None, max_length=10),
    keyword: str = Query(default="", max_length=80),
    user: str | None = Query(default=None, max_length=50),
    reviewed: str | None = Query(default=None, max_length=10),
    snapshot_id: int | None = Query(default=None, ge=0),
    context: TenantContext = Depends(require_tenant_admin),
):
    return StreamingResponse(export_audit_csv(context.tenant_id, result, keyword, user, reviewed, snapshot_id), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": 'attachment; filename="audit-filtered.csv"', "Cache-Control": "no-store"})


@router.post("/audit-logs/{log_id}/review")
def review_intercepted_log(
    log_id: int,
    user: dict | None = Depends(optional_user),
    context: TenantContext = Depends(require_tenant_admin),
):
    reviewer = (user or {}).get("username") or "管理员"
    reviewed = review_audit_log(log_id, tenant_id=context.tenant_id, reviewer=reviewer)
    if reviewed is None:
        raise HTTPException(status_code=404, detail="拦截记录不存在或无需复核")
    return reviewed


@router.post("/audit-logs/review-batch")
def review_intercepted_logs_batch(
    payload: ReviewBatchPayload,
    user: dict | None = Depends(optional_user),
    context: TenantContext = Depends(require_tenant_admin),
):
    reviewer = (user or {}).get("username") or "管理员"
    return review_audit_logs_batch(payload.ids, tenant_id=context.tenant_id, reviewer=reviewer)
