"""资源中心接口，对齐前端 api/resources.js 契约。"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.metadata import service, suggest
from app.metadata.scanner import scan_source
from app.db.models import DataSource
from app.db.platform import PlatformSession
from app.tenancy.service import TenantContext, ensure_source_access, require_tenant_admin, tenant_context

router = APIRouter(prefix="/api/resources", tags=["resources"])


def _source_id(context: TenantContext, source_id: int | None) -> int:
    with PlatformSession() as session:
        if source_id is not None:
            return ensure_source_access(session, source_id, context.tenant_id).id
        source = (
            session.query(DataSource).filter_by(tenant_id=context.tenant_id, is_default=True).first()
            or session.query(DataSource).filter_by(tenant_id=context.tenant_id).first()
        )
        if source is None:
            raise HTTPException(status_code=404, detail="当前企业尚未配置数据源")
        return source.id


@router.post("/scan")
def trigger_scan(context: TenantContext = Depends(require_tenant_admin)):
    """重新扫描默认数据源元数据并刷新登记（管理员操作）。多数据源扫描走 /api/datasources/{id}/scan。"""
    source_id = _source_id(context, None)
    with PlatformSession() as session:
        source = ensure_source_access(session, source_id, context.tenant_id)
        session.expunge(source)
    return scan_source(source)


@router.get("/tables")
def get_tables(
    keyword: str = Query(default=""),
    topic: str | None = Query(default=None),
    sourceId: int | None = Query(default=None),
    context: TenantContext = Depends(tenant_context),
):
    return service.list_tables(keyword=keyword, topic=topic, source_id=_source_id(context, sourceId))


@router.get("/tables/unregistered")
def get_unregistered_tables(sourceId: int | None = Query(default=None), context: TenantContext = Depends(tenant_context)):
    return service.list_unregistered_tables(source_id=_source_id(context, sourceId))


@router.get("/tables/{table_name}/fields")
def get_fields(table_name: str, sourceId: int | None = Query(default=None), context: TenantContext = Depends(tenant_context)):
    fields = service.get_table_fields(table_name, source_id=_source_id(context, sourceId))
    if fields is None:
        raise HTTPException(status_code=404, detail=f"数据表 {table_name} 未登记字段信息")
    return fields


@router.get("/tables/{table_name}/preview")
def get_preview(table_name: str, sourceId: int | None = Query(default=None), context: TenantContext = Depends(tenant_context)):
    preview = service.get_table_preview(table_name, source_id=_source_id(context, sourceId))
    if preview is None:
        raise HTTPException(status_code=404, detail=f"数据表 {table_name} 暂无预览数据")
    return preview


@router.get("/relations")
def get_relations(sourceId: int | None = Query(default=None), context: TenantContext = Depends(tenant_context)):
    return service.list_relations(source_id=_source_id(context, sourceId))


class ConfirmRequest(BaseModel):
    sourceId: int
    suggestion: dict


@router.post("/tables/{table_name}/suggest")
def suggest_semantics(table_name: str, sourceId: int = Query(...), context: TenantContext = Depends(require_tenant_admin)):
    """LLM 生成新表语义推荐（中文名/主题/主外键/字段含义）。结果须经管理员确认。"""
    try:
        return suggest.suggest_table_semantics(table_name, _source_id(context, sourceId))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/tables/{table_name}/confirm")
def confirm_semantics(table_name: str, request: ConfirmRequest, context: TenantContext = Depends(require_tenant_admin)):
    """管理员确认语义推荐，表正式进入资源目录并对问析可见。"""
    try:
        return suggest.confirm_table_semantics(table_name, _source_id(context, request.sourceId), request.suggestion)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
