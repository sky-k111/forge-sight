"""模型治理 API：注册表查询、复核、发布、下线。"""

from fastapi import APIRouter, HTTPException, Query

from app.modeling import service
from app.modeling.contracts import GovernanceAction, GovernanceReview

router = APIRouter(prefix="/api/v1", tags=["model-governance"])


def _raise(exc: Exception):
    if isinstance(exc, service.ModelNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, service.ModelingError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    raise exc


@router.get("/model-governance/models")
def list_models(statusValue: str | None = Query(default=None, alias="status")):
    return {"items": service.list_models(status=statusValue or "")}


@router.post("/model-governance/models/{model_id}/review")
def review_model(model_id: str, payload: GovernanceReview):
    try:
        return service.review_model(model_id, payload.decision, payload.comment, payload.userId)
    except Exception as exc:
        _raise(exc)


@router.post("/model-governance/models/{model_id}/publish")
def publish_model(model_id: str, payload: GovernanceAction):
    try:
        return service.publish_model(model_id, payload.userId)
    except Exception as exc:
        _raise(exc)


@router.post("/model-governance/models/{model_id}/retire")
def retire_model(model_id: str, payload: GovernanceAction):
    try:
        return service.retire_model(model_id, payload.userId)
    except Exception as exc:
        _raise(exc)
