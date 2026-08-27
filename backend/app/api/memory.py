"""Business self-service and administrator governance APIs for agent memory."""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.auth.deps import effective_user_id, optional_user
from app.memory import service
from app.db.platform import PlatformSession
from app.tenancy.service import TenantContext, ensure_source_access, tenant_context

router = APIRouter(prefix="/api/memory", tags=["agent-memory"])


def _call(operation, *args, **kwargs):
    try:
        return operation(*args, **kwargs)
    except service.MemoryNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'")) from exc
    except service.MemoryValidation as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _identity(user: dict | None, raw: str | None = None) -> str:
    return effective_user_id(raw, user)


def _admin(user: dict | None) -> str:
    if user and user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可进行记忆治理")
    return effective_user_id("local-admin", user)


class MemoryCreatePayload(BaseModel):
    content: str = Field(min_length=1, max_length=4000)
    sourceId: int | None = None
    kind: str = "preference"
    title: str = Field(default="", max_length=200)
    userId: str | None = None


class MemoryCorrectionPayload(BaseModel):
    content: str = Field(min_length=1, max_length=4000)
    userId: str | None = None


class MemorySettingsPayload(BaseModel):
    enabled: bool
    config: dict | None = None


@router.get("/settings")
def settings():
    return service.get_settings()


@router.patch("/settings")
def update_settings(payload: MemorySettingsPayload, user: dict | None = Depends(optional_user)):
    return service.update_settings(payload.enabled, updated_by=_admin(user), config=payload.config)


@router.get("")
def memories(
    source_id: int | None = Query(default=None, alias="sourceId"),
    status: str | None = Query(default=None),
    kind: str | None = Query(default=None),
    user_id: str | None = Query(default=None, alias="userId"),
    user: dict | None = Depends(optional_user),
    context: TenantContext = Depends(tenant_context),
):
    if source_id is not None:
        with PlatformSession() as session:
            ensure_source_access(session, source_id, context.tenant_id)
    return service.list_memories(
        user_id=_identity(user, user_id), source_id=source_id, tenant_id=context.tenant_id,
        status=status, memory_kind=kind,
    )


@router.post("")
def create_memory(
    payload: MemoryCreatePayload, user: dict | None = Depends(optional_user),
    context: TenantContext = Depends(tenant_context),
):
    if payload.sourceId is not None:
        with PlatformSession() as session:
            ensure_source_access(session, payload.sourceId, context.tenant_id)
    return _call(
        service.remember, payload.content, user_id=_identity(user, payload.userId),
        source_id=payload.sourceId, tenant_id=context.tenant_id,
        memory_kind=payload.kind, title=payload.title,
        explicit=True, evidence_type="manual", reference_id="business-ui",
    )


@router.post("/{memory_id}/correct")
def correct(memory_id: str, payload: MemoryCorrectionPayload, user: dict | None = Depends(optional_user), context: TenantContext = Depends(tenant_context)):
    return _call(service.correct_memory, memory_id, payload.content, user_id=_identity(user, payload.userId), tenant_id=context.tenant_id)


@router.delete("/{memory_id}")
def forget(memory_id: str, user_id: str | None = Query(default=None, alias="userId"), user: dict | None = Depends(optional_user), context: TenantContext = Depends(tenant_context)):
    return _call(service.forget_memory, memory_id, user_id=_identity(user, user_id), tenant_id=context.tenant_id)


@router.get("/governance/summary")
def governance_summary(user: dict | None = Depends(optional_user), context: TenantContext = Depends(tenant_context)):
    _admin(user)
    return service.governance_summary(context.tenant_id)


@router.get("/governance/items")
def governance_items(
    status: str | None = Query(default=None), kind: str | None = Query(default=None),
    source_id: int | None = Query(default=None, alias="sourceId"),
    user: dict | None = Depends(optional_user),
    context: TenantContext = Depends(tenant_context),
):
    _admin(user)
    return service.list_memories(user_id=None, source_id=source_id, tenant_id=context.tenant_id, status=status, memory_kind=kind, limit=300)


@router.post("/governance/consolidate")
def consolidate(user: dict | None = Depends(optional_user), context: TenantContext = Depends(tenant_context)):
    _admin(user)
    return service.consolidate(tenant_id=context.tenant_id)


@router.post("/{memory_id}/promote")
def promote(memory_id: str, user: dict | None = Depends(optional_user), context: TenantContext = Depends(tenant_context)):
    return _call(service.promote_to_skill, memory_id, reviewer=_admin(user), tenant_id=context.tenant_id)
