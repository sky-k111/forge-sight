"""数据源管理接口。"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.metadata import datasources
from app.metadata.scanner import scan_source
from app.db.platform import PlatformSession
from app.retrieval.index_jobs import enqueue_index_job, retry_failed_job, serialize_job, source_index_status

router = APIRouter(prefix="/api/datasources", tags=["datasources"])


class DataSourceCreate(BaseModel):
    name: str
    type: str  # mysql / postgresql
    host: str = "127.0.0.1"
    port: int | None = None
    database: str
    username: str
    password: str = ""


@router.get("")
def get_sources():
    return datasources.list_sources()


@router.post("")
def add_source(payload: DataSourceCreate):
    try:
        return datasources.create_source(payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class DataSourceUpdate(BaseModel):
    name: str | None = None
    host: str | None = None
    port: int | None = None
    database: str | None = None
    username: str | None = None
    password: str | None = None  # 空 = 不修改


@router.patch("/{source_id}")
def edit_source(source_id: int, payload: DataSourceUpdate):
    try:
        return datasources.update_source(source_id, payload.model_dump(exclude_unset=True))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{source_id}/test")
def test_source(source_id: int):
    try:
        return datasources.test_source(source_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{source_id}/scan")
def scan_source_tables(source_id: int):
    source = datasources.get_source(source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="数据源不存在")
    return scan_source(source)


@router.get("/{source_id}/semantic-index")
def get_semantic_index(source_id: int):
    try:
        with PlatformSession() as session:
            return source_index_status(session, source_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{source_id}/semantic-index/rebuild")
def rebuild_semantic_index(source_id: int):
    try:
        with PlatformSession() as session:
            job = enqueue_index_job(session, source_id, mode="full", reason="管理员手动全量重建")
            session.commit()
            return serialize_job(job)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/jobs/{job_id}/retry")
def retry_semantic_index(job_id: str):
    try:
        with PlatformSession() as session:
            job = retry_failed_job(session, job_id)
            session.commit()
            return serialize_job(job)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
