"""安全审计接口，对齐前端 api/security.js 契约。"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.audit.service import list_audit_logs, review_audit_log, review_audit_logs_batch
from app.auth.deps import optional_user
from app.tenancy.service import TenantContext, require_tenant_admin

router = APIRouter(prefix="/api/security", tags=["security"])


class ReviewBatchPayload(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=200)


@router.get("/audit-logs")
def get_audit_logs(
    result: str | None = Query(default=None, max_length=10),
    keyword: str = Query(default="", max_length=80),
    user: str | None = Query(default=None, max_length=50),
    reviewed: str | None = Query(default=None, max_length=10),
    context: TenantContext = Depends(require_tenant_admin),
):
    return list_audit_logs(
        result=result,
        keyword=keyword,
        user=user,
        reviewed=reviewed,
        tenant_id=context.tenant_id,
    )


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
