"""安全审计接口，对齐前端 api/security.js 契约。"""

from fastapi import APIRouter, Depends, Query

from app.audit.service import list_audit_logs
from app.tenancy.service import TenantContext, require_tenant_admin

router = APIRouter(prefix="/api/security", tags=["security"])


@router.get("/audit-logs")
def get_audit_logs(
    result: str | None = Query(default=None),
    keyword: str = Query(default=""),
    context: TenantContext = Depends(require_tenant_admin),
):
    return list_audit_logs(result=result, keyword=keyword, tenant_id=context.tenant_id)
