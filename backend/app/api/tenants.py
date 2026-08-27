"""Enterprise workspace and tenant membership API."""

from fastapi import APIRouter, Depends

from app.auth.deps import require_user
from app.tenancy.service import (
    TenantContext,
    list_tenant_members,
    list_user_tenants,
    require_tenant_admin,
    tenant_context,
    tenant_summary,
)

router = APIRouter(prefix="/api/tenants", tags=["tenants"])


@router.get("/me")
def my_tenants(user: dict = Depends(require_user)) -> dict:
    return {"items": list_user_tenants(user["id"])}


@router.get("/current")
def current_tenant(context: TenantContext = Depends(tenant_context)) -> dict:
    return tenant_summary(context)


@router.get("/current/members")
def current_tenant_members(
    user: dict = Depends(require_user),
    context: TenantContext = Depends(require_tenant_admin),
) -> dict:
    # 成员名单包含平台账号信息，即使演示模式的匿名管理员上下文也不允许读取。
    return {"items": list_tenant_members(context.tenant_id)}
