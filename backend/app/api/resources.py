"""资源中心接口，对齐前端 api/resources.js 契约。"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.metadata import service, suggest
from app.metadata.scanner import scan_default_source

router = APIRouter(prefix="/api/resources", tags=["resources"])


@router.post("/scan")
def trigger_scan():
    """重新扫描默认数据源元数据并刷新登记（管理员操作）。多数据源扫描走 /api/datasources/{id}/scan。"""
    return scan_default_source()


@router.get("/tables")
def get_tables(
    keyword: str = Query(default=""),
    topic: str | None = Query(default=None),
    sourceId: int | None = Query(default=None),
):
    return service.list_tables(keyword=keyword, topic=topic, source_id=sourceId)


@router.get("/tables/unregistered")
def get_unregistered_tables(sourceId: int | None = Query(default=None)):
    return service.list_unregistered_tables(source_id=sourceId)


@router.get("/tables/{table_name}/fields")
def get_fields(table_name: str, sourceId: int | None = Query(default=None)):
    fields = service.get_table_fields(table_name, source_id=sourceId)
    if fields is None:
        raise HTTPException(status_code=404, detail=f"数据表 {table_name} 未登记字段信息")
    return fields


@router.get("/tables/{table_name}/preview")
def get_preview(table_name: str, sourceId: int | None = Query(default=None)):
    preview = service.get_table_preview(table_name, source_id=sourceId)
    if preview is None:
        raise HTTPException(status_code=404, detail=f"数据表 {table_name} 暂无预览数据")
    return preview


@router.get("/relations")
def get_relations(sourceId: int | None = Query(default=None)):
    return service.list_relations(source_id=sourceId)


class ConfirmRequest(BaseModel):
    sourceId: int
    suggestion: dict


@router.post("/tables/{table_name}/suggest")
def suggest_semantics(table_name: str, sourceId: int = Query(...)):
    """LLM 生成新表语义推荐（中文名/主题/主外键/字段含义）。结果须经管理员确认。"""
    try:
        return suggest.suggest_table_semantics(table_name, sourceId)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/tables/{table_name}/confirm")
def confirm_semantics(table_name: str, request: ConfirmRequest):
    """管理员确认语义推荐，表正式进入资源目录并对问析可见。"""
    try:
        return suggest.confirm_table_semantics(table_name, request.sourceId, request.suggestion)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
