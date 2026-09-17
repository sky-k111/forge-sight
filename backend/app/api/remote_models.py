"""Authenticated remote model registration, validation and execution endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.auth.deps import require_user
from app.modeling import remote
from app.modeling.mcp_client import ModelMCPClient, RemoteModelError
from app.tenancy.service import tenant_context, require_tenant_admin

router = APIRouter(prefix="/api/v1/modeling/remote-models", tags=["remote-models"], dependencies=[Depends(require_user)])


class Discovery(BaseModel):
    endpoint: str = Field(max_length=500)
    credentialRef: str = Field(default="", max_length=100)


class Samples(BaseModel):
    records: list[dict] | None = Field(default=None, min_length=1, max_length=200)


def run(action, *args, **kwargs):
    try:
        return action(*args, **kwargs)
    except RemoteModelError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/discover")
def discover(payload: Discovery, context=Depends(tenant_context)):
    def fetch():
        with ModelMCPClient(payload.endpoint, payload.credentialRef) as client:
            return {"items": client.tools()}
    return run(fetch)


@router.get("")
def listing(sourceId: int = Query(gt=0), context=Depends(tenant_context)):
    return {
        "items": remote.list_assets(context, sourceId),
        "canConfigure": bool(context.user_id),
        "canPublish": context.can_administer,
    }


@router.post("")
def create(payload: remote.RemoteConfig, context=Depends(tenant_context)):
    return run(remote.save_asset, payload, context)


@router.put("/{model_id}")
def update(model_id: str, payload: remote.RemoteConfig, context=Depends(tenant_context)):
    return run(remote.save_asset, payload, context, model_id)


@router.post("/{model_id}/validate")
def validate(model_id: str, payload: Samples, context=Depends(tenant_context)):
    return run(remote.invoke, model_id, context, payload.records, validate=True)


@router.post("/{model_id}/publish")
def publish(model_id: str, context=Depends(require_tenant_admin)):
    return run(remote.transition, model_id, context, "published")


@router.post("/{model_id}/retire")
def retire(model_id: str, context=Depends(require_tenant_admin)):
    return run(remote.transition, model_id, context, "retired")


@router.post("/{model_id}/predict")
def predict(model_id: str, payload: Samples, context=Depends(tenant_context)):
    return run(remote.invoke, model_id, context, payload.records)


@router.get("/{model_id}/calls")
def calls(model_id: str, context=Depends(tenant_context)):
    with remote.PlatformSession() as session:
        remote.get_asset(session, model_id, context)
        rows = session.query(remote.RemoteModelCall).filter_by(model_id=model_id).order_by(remote.RemoteModelCall.created_at.desc()).limit(30).all()
        return {"items": [{"id": row.id, "status": row.status, "recordCount": row.record_count,
                           "durationMs": row.duration_ms, "createdAt": row.created_at.isoformat()} for row in rows]}
