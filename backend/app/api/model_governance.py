"""模型治理 API：注册表查询、复核、发布、下线、免审直发、工件清理。"""

from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth.deps import optional_user
from app.modeling import service
from app.modeling.contracts import GovernanceAction, GovernanceReview, ThresholdUpdate
from app.tenancy.service import TenantContext, require_tenant_admin

router = APIRouter(prefix="/api/v1", tags=["model-governance"])


def _raise(exc: Exception):
    if isinstance(exc, service.ModelNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, service.ModelingError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    raise exc


@router.get("/model-governance/models")
def list_models(
    statusValue: str | None = Query(default=None, alias="status"),
    task_id: str = Query(default="", alias="taskId"),
):
    return {"items": service.list_models(status=statusValue or "", task_id=task_id)}


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


@router.get("/model-governance/drift")
def get_drift_report(context: TenantContext = Depends(require_tenant_admin)):
    """偏差监控：预测留痕自动回填实际值后的模型健康报表。"""
    return service.drift_report(context.tenant_id)


@router.post("/model-governance/models/{model_id}/submit-review")
def submit_review(model_id: str, payload: GovernanceAction):
    try:
        return service.submit_for_review(model_id, payload.userId)
    except Exception as exc:
        _raise(exc)


@router.post("/model-governance/models/{model_id}/publish-direct")
def publish_direct(
    model_id: str,
    payload: GovernanceAction,
    user: dict | None = Depends(optional_user),
    context: TenantContext = Depends(require_tenant_admin),
):
    """管理员免审直发：角色由服务端校验，机器门禁仍不可跳过。"""
    actor = (user or {}).get("username") or payload.userId
    try:
        return service.publish_model_direct(model_id, actor)
    except Exception as exc:
        _raise(exc)


@router.post("/model-governance/admin/purge-artifacts")
def purge_artifacts(
    payload: GovernanceAction,
    context: TenantContext = Depends(require_tenant_admin),
):
    return service.purge_retired_artifacts()


@router.post("/model-governance/models/{model_id}/retire")
def retire_model(model_id: str, payload: GovernanceAction):
    try:
        return service.retire_model(model_id, payload.userId)
    except Exception as exc:
        _raise(exc)


@router.post("/model-governance/models/{model_id}/rollback")
def rollback_model(model_id: str, payload: GovernanceAction):
    try:
        return service.rollback_model(model_id, payload.userId)
    except Exception as exc:
        _raise(exc)


@router.post("/model-governance/models/{model_id}/threshold-variant", status_code=201)
def threshold_variant(model_id: str, payload: ThresholdUpdate):
    try:
        return service.create_threshold_variant(model_id, payload.threshold, payload.reason, payload.userId)
    except Exception as exc:
        _raise(exc)
