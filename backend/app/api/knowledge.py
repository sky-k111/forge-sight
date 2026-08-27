"""业务知识中心 API：主题、对象、规则和指标口径。"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.knowledge import candidates, service
from app.tenancy.service import TenantContext, require_tenant_admin, tenant_context

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
def get_topics(context: TenantContext = Depends(tenant_context)):
    return service.list_topics(context.tenant_id)


@router.post("/topics")
def add_topic(payload: TopicPayload, context: TenantContext = Depends(require_tenant_admin)):
    return _call(service.create_topic, payload.model_dump(exclude_unset=True), context.tenant_id)


@router.patch("/topics/{code}")
def edit_topic(code: str, payload: TopicPayload, context: TenantContext = Depends(require_tenant_admin)):
    return _call(service.update_topic, code, payload.model_dump(exclude_unset=True), context.tenant_id)


@router.delete("/topics/{code}")
def remove_topic(code: str, context: TenantContext = Depends(require_tenant_admin)):
    return _call(service.delete_topic, code, context.tenant_id)


class ObjectPayload(BaseModel):
    id: str | None = None
    name: str | None = None
    topic: str | None = None
    description: str | None = None
    relationCount: int | None = Field(default=None, ge=0)
    enabled: bool | None = None


@router.get("/objects")
def get_objects(
    keyword: str = Query(default=""),
    topic: str | None = Query(default=None),
    context: TenantContext = Depends(tenant_context),
):
    return service.list_objects(keyword=keyword, topic=topic, tenant_id=context.tenant_id)


@router.post("/objects")
def add_object(payload: ObjectPayload, context: TenantContext = Depends(require_tenant_admin)):
    return _call(service.create_object, payload.model_dump(exclude_unset=True), context.tenant_id)


@router.patch("/objects/{object_id}")
def edit_object(object_id: str, payload: ObjectPayload, context: TenantContext = Depends(require_tenant_admin)):
    return _call(service.update_object, object_id, payload.model_dump(exclude_unset=True), context.tenant_id)


@router.delete("/objects/{object_id}")
def remove_object(object_id: str, context: TenantContext = Depends(require_tenant_admin)):
    return _call(service.delete_object, object_id, context.tenant_id)


class RulePayload(BaseModel):
    id: str | None = None
    name: str | None = None
    type: str | None = None
    scope: str | None = None
    content: str | None = None
    enabled: bool | None = None


@router.get("/rules")
def get_rules(
    topic: str | None = Query(default=None),
    context: TenantContext = Depends(tenant_context),
):
    return service.list_rules(topic=topic, tenant_id=context.tenant_id)


@router.post("/rules")
def add_rule(payload: RulePayload, context: TenantContext = Depends(require_tenant_admin)):
    return _call(service.create_rule, payload.model_dump(exclude_unset=True), context.tenant_id)


@router.patch("/rules/{rule_id}")
def edit_rule(rule_id: str, payload: RulePayload, context: TenantContext = Depends(require_tenant_admin)):
    return _call(service.update_rule, rule_id, payload.model_dump(exclude_unset=True), context.tenant_id)


@router.delete("/rules/{rule_id}")
def remove_rule(rule_id: str, context: TenantContext = Depends(require_tenant_admin)):
    return _call(service.delete_rule, rule_id, context.tenant_id)


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


class CandidateReviewPayload(BaseModel):
    reason: str = Field(default="", max_length=1000)
    reviewer: str = Field(default="管理员", max_length=64)


@router.get("/metric-candidates")
def get_metric_candidates(
    sourceId: int = Query(...),
    status: str | None = Query(default=None),
    context: TenantContext = Depends(require_tenant_admin),
):
    return _call(candidates.list_candidates, sourceId, status, context.tenant_id)


@router.post("/metric-candidates/generate")
def generate_metric_candidates(
    sourceId: int = Query(...),
    context: TenantContext = Depends(require_tenant_admin),
):
    # Candidate generation resolves the source again inside the service; the tenant
    # argument prevents a caller from pointing it at another enterprise's source.
    _call(service.list_metrics, source_id=sourceId, tenant_id=context.tenant_id)
    return _call(candidates.generate_candidates, sourceId)


@router.post("/metric-candidates/{candidate_id}/accept")
def accept_metric_candidate(
    candidate_id: str,
    payload: CandidateReviewPayload | None = None,
    context: TenantContext = Depends(require_tenant_admin),
):
    reviewer = payload.reviewer if payload else "管理员"
    return _call(candidates.accept_candidate, candidate_id, reviewer, context.tenant_id)


@router.post("/metric-candidates/{candidate_id}/reject")
def reject_metric_candidate(
    candidate_id: str,
    payload: CandidateReviewPayload,
    context: TenantContext = Depends(require_tenant_admin),
):
    return _call(candidates.reject_candidate, candidate_id, payload.reason, payload.reviewer, context.tenant_id)


@router.get("/metrics")
def get_metrics(
    keyword: str = Query(default=""),
    topic: str | None = Query(default=None),
    status: str | None = Query(default=None),
    sourceId: int | None = Query(default=None),
    context: TenantContext = Depends(tenant_context),
):
    return service.list_metrics(
        keyword=keyword,
        topic=topic,
        status=status,
        source_id=sourceId,
        tenant_id=context.tenant_id,
    )


@router.post("/metrics")
def add_metric(payload: MetricPayload, context: TenantContext = Depends(require_tenant_admin)):
    return _call(service.save_metric, payload.model_dump(exclude_unset=True), None, context.tenant_id)


@router.get("/metrics/{metric_id}")
def get_metric(
    metric_id: str,
    sourceId: int | None = Query(default=None),
    context: TenantContext = Depends(tenant_context),
):
    return _call(service.get_metric, metric_id, sourceId, context.tenant_id)


@router.patch("/metrics/{metric_id}")
def edit_metric(
    metric_id: str,
    payload: MetricPayload,
    context: TenantContext = Depends(require_tenant_admin),
):
    return _call(service.save_metric, payload.model_dump(exclude_unset=True), metric_id, context.tenant_id)


@router.post("/metrics/{metric_id}/publish")
def publish_metric(
    metric_id: str,
    sourceId: int | None = Query(default=None),
    context: TenantContext = Depends(require_tenant_admin),
):
    return _call(service.publish_metric, metric_id, sourceId, context.tenant_id)


@router.post("/metrics/{metric_id}/submit-review")
def submit_metric_for_review(
    metric_id: str,
    sourceId: int | None = Query(default=None),
    context: TenantContext = Depends(require_tenant_admin),
):
    return _call(service.submit_metric_for_review, metric_id, sourceId, context.tenant_id)


@router.delete("/metrics/{metric_id}")
def remove_metric(metric_id: str, context: TenantContext = Depends(require_tenant_admin)):
    return _call(service.delete_metric, metric_id, context.tenant_id)
