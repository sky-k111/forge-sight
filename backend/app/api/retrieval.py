"""阶段 4 语义检索 API。"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.retrieval import service

router = APIRouter(prefix="/api/retrieval", tags=["retrieval"])


class ContextRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    sourceId: int | None = Field(default=None, ge=1)
    maxMetrics: int = Field(default=5, ge=1, le=10)
    maxTables: int = Field(default=8, ge=1, le=15)
    maxFieldsPerTable: int = Field(default=12, ge=1, le=30)


@router.post("/context")
def retrieve_context(payload: ContextRequest):
    try:
        return service.build_context_package(
            payload.question,
            payload.sourceId,
            max_metrics=payload.maxMetrics,
            max_tables=payload.maxTables,
            max_fields_per_table=payload.maxFieldsPerTable,
        )
    except service.RetrievalNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'")) from exc
    except service.RetrievalValidation as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

