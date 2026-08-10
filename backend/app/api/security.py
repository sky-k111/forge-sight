"""安全审计接口，对齐前端 api/security.js 契约。"""

from fastapi import APIRouter, Query

from app.audit.service import list_audit_logs

router = APIRouter(prefix="/api/security", tags=["security"])


@router.get("/audit-logs")
def get_audit_logs(result: str | None = Query(default=None), keyword: str = Query(default="")):
    return list_audit_logs(result=result, keyword=keyword)
