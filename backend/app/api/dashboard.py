"""Personal business-dashboard API."""

from fastapi import APIRouter, Depends, Query

from app.auth.deps import effective_stable_user_id, optional_user
from app.dashboard import service
from app.dashboard.schemas import DashboardConfig, WidgetQuery
from app.tenancy.service import TenantContext, tenant_context

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


def _actor(user: dict | None, compatibility_id: str | None) -> str:
    return effective_stable_user_id(compatibility_id, user)


@router.get("")
def get_dashboard(
    sourceId: int = Query(gt=0), userId: str | None = Query(default=None, max_length=64),
    user: dict | None = Depends(optional_user), context: TenantContext = Depends(tenant_context),
):
    return service.load_config(sourceId, context.tenant_id, _actor(user, userId))


@router.put("")
def put_dashboard(
    config: DashboardConfig, sourceId: int = Query(gt=0), userId: str | None = Query(default=None, max_length=64),
    user: dict | None = Depends(optional_user), context: TenantContext = Depends(tenant_context),
):
    return service.save_config(sourceId, context.tenant_id, _actor(user, userId), config)


@router.delete("")
def delete_dashboard(
    sourceId: int = Query(gt=0), userId: str | None = Query(default=None, max_length=64),
    user: dict | None = Depends(optional_user), context: TenantContext = Depends(tenant_context),
):
    return service.reset_config(sourceId, context.tenant_id, _actor(user, userId))


@router.post("/query")
def post_widget_query(
    request: WidgetQuery, userId: str | None = Query(default=None, max_length=64),
    user: dict | None = Depends(optional_user), context: TenantContext = Depends(tenant_context),
):
    return service.query_widget(request, context.tenant_id, _actor(user, userId))
