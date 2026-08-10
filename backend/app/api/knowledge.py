"""业务知识中心 API：主题、对象、规则和指标口径。"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.knowledge import service

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


def _call(operation, *args, **kwargs):
    try:
        return operation(*args, **kwargs)
    except service.KnowledgeNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'")) from exc
    except service.KnowledgeConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except service.KnowledgeValidation as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class TopicPayload(BaseModel):
    code: str | None = None
    name: str | None = None
    color: str | None = None
    description: str | None = None
    coverage: int | None = Field(default=None, ge=0, le=100)
    enabled: bool | None = None


@router.get("/topics")
def get_topics():
    return service.list_topics()


@router.post("/topics")
def add_topic(payload: TopicPayload):
    return _call(service.create_topic, payload.model_dump(exclude_unset=True))


@router.patch("/topics/{code}")
def edit_topic(code: str, payload: TopicPayload):
    return _call(service.update_topic, code, payload.model_dump(exclude_unset=True))


@router.delete("/topics/{code}")
def remove_topic(code: str):
    return _call(service.delete_topic, code)


class ObjectPayload(BaseModel):
    id: str | None = None
    name: str | None = None
    topic: str | None = None
    description: str | None = None
    relationCount: int | None = Field(default=None, ge=0)
    enabled: bool | None = None


@router.get("/objects")
def get_objects(keyword: str = Query(default=""), topic: str | None = Query(default=None)):
    return service.list_objects(keyword=keyword, topic=topic)


@router.post("/objects")
def add_object(payload: ObjectPayload):
    return _call(service.create_object, payload.model_dump(exclude_unset=True))


@router.patch("/objects/{object_id}")
def edit_object(object_id: str, payload: ObjectPayload):
    return _call(service.update_object, object_id, payload.model_dump(exclude_unset=True))


@router.delete("/objects/{object_id}")
def remove_object(object_id: str):
    return _call(service.delete_object, object_id)


class RulePayload(BaseModel):
    id: str | None = None
    name: str | None = None
    type: str | None = None
    scope: str | None = None
    content: str | None = None
    enabled: bool | None = None


@router.get("/rules")
def get_rules(topic: str | None = Query(default=None)):
    return service.list_rules(topic=topic)


@router.post("/rules")
def add_rule(payload: RulePayload):
    return _call(service.create_rule, payload.model_dump(exclude_unset=True))


@router.patch("/rules/{rule_id}")
def edit_rule(rule_id: str, payload: RulePayload):
    return _call(service.update_rule, rule_id, payload.model_dump(exclude_unset=True))


@router.delete("/rules/{rule_id}")
def remove_rule(rule_id: str):
    return _call(service.delete_rule, rule_id)


class MetricPayload(BaseModel):
    id: str | None = None
    name: str | None = None
    topic: str | None = None
    formula: str | None = None
    physicalFormula: str | None = None
    source: str | list[str] | None = None
    sourceTables: list[str] | None = None
    sourceId: int | None = None
    owner: str | None = None
    definition: str | None = None
    synonyms: list[str] | None = None
    dimensions: list[str] | None = None
    semanticTimeField: str | None = None
    timeField: str | None = None
    bindingTimeField: str | None = None
    filterExpression: str | None = None
    sqlDialect: str | None = None
    fieldMappings: list[dict] | None = None
    bindingNotes: str | None = None
    precision: str | None = None


@router.get("/metrics")
def get_metrics(
    keyword: str = Query(default=""),
    topic: str | None = Query(default=None),
    status: str | None = Query(default=None),
    sourceId: int | None = Query(default=None),
):
    return service.list_metrics(keyword=keyword, topic=topic, status=status, source_id=sourceId)


@router.post("/metrics")
def add_metric(payload: MetricPayload):
    return _call(service.save_metric, payload.model_dump(exclude_unset=True))


@router.get("/metrics/{metric_id}")
def get_metric(metric_id: str, sourceId: int | None = Query(default=None)):
    return _call(service.get_metric, metric_id, sourceId)


@router.patch("/metrics/{metric_id}")
def edit_metric(metric_id: str, payload: MetricPayload):
    return _call(service.save_metric, payload.model_dump(exclude_unset=True), metric_id)


@router.post("/metrics/{metric_id}/publish")
def publish_metric(metric_id: str, sourceId: int | None = Query(default=None)):
    return _call(service.publish_metric, metric_id, sourceId)


@router.post("/metrics/{metric_id}/submit-review")
def submit_metric_for_review(metric_id: str, sourceId: int | None = Query(default=None)):
    return _call(service.submit_metric_for_review, metric_id, sourceId)


@router.delete("/metrics/{metric_id}")
def remove_metric(metric_id: str):
    return _call(service.delete_metric, metric_id)
