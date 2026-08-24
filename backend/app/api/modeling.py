"""智能建模 API：算法目录、任务生命周期、训练/评估/推理。"""

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.auth.deps import effective_user_id, optional_user
from app.modeling import service
from app.modeling.contracts import ModelingTaskCreate, PredictRequest, TrainRequest

router = APIRouter(prefix="/api/v1", tags=["modeling"])


def _raise(exc: Exception):
    if isinstance(exc, service.ModelNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, service.ModelingError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    raise exc


@router.get("/modeling/algorithms")
def list_algorithms():
    return {"items": service.list_algorithms()}


@router.get("/modeling/reusable-models")
def reusable_models(
    question: str = Query(min_length=1, max_length=1000),
    source_id: int = Query(alias="sourceId", ge=1),
    task_type: str = Query(default="", alias="taskType", pattern=r"^(|regression|classification|clustering|anomaly)$"),
):
    return {"items": service.find_reusable_models(question, source_id, task_type)}


@router.post("/modeling/tasks", status_code=status.HTTP_201_CREATED)
def create_task(payload: ModelingTaskCreate, user: dict | None = Depends(optional_user)):
    try:
        task, _duplicate = service.create_task(
            payload.model_copy(update={"ownerId": effective_user_id(payload.ownerId, user)})
        )
    except Exception as exc:
        _raise(exc)
    return task


@router.get("/modeling/tasks")
def list_tasks(
    ownerId: str = Query(default="", min_length=0, max_length=64),
    statusValue: str | None = Query(default=None, alias="status"),
):
    return {"items": service.list_tasks(owner_id=ownerId, status=statusValue or "")}


@router.get("/modeling/tasks/{task_id}")
def get_task(task_id: str):
    try:
        return service.get_task(task_id)
    except Exception as exc:
        _raise(exc)


@router.post("/modeling/tasks/{task_id}/train")
def train_task(task_id: str, payload: TrainRequest, user: dict | None = Depends(optional_user)):
    try:
        return service.train_task(
            task_id,
            payload.strategy,
            list(payload.candidateIds),
            payload.manualModelId,
            effective_user_id(payload.userId, user),
            payload.optimizationLevel,
        )
    except Exception as exc:
        _raise(exc)


@router.get("/modeling/tasks/{task_id}/evaluation")
def get_evaluation(task_id: str):
    try:
        return service.get_evaluation(task_id)
    except Exception as exc:
        _raise(exc)


@router.post("/modeling/tasks/{task_id}/diagnostics")
def diagnose_task(task_id: str, user: dict | None = Depends(optional_user)):
    try:
        return service.diagnose_task(task_id, effective_user_id("local-test-user", user))
    except Exception as exc:
        _raise(exc)


@router.get("/modeling/tasks/{task_id}/versions")
def model_versions(task_id: str):
    try:
        return service.model_version_history(task_id)
    except Exception as exc:
        _raise(exc)


@router.get("/modeling/tasks/{task_id}/evaluations")
def list_evaluations(task_id: str):
    try:
        return {"items": service.list_evaluations(task_id)}
    except Exception as exc:
        _raise(exc)


@router.post("/modeling/tasks/{task_id}/predict")
def predict(task_id: str, payload: PredictRequest, user: dict | None = Depends(optional_user)):
    try:
        return service.predict(task_id, list(payload.records), effective_user_id(payload.userId, user))
    except Exception as exc:
        _raise(exc)
