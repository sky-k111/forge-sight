const ACTIVE_TENANT_KEY = "forgesight:active-tenant-id";

export const TENANT_CHANGED_EVENT = "forgesight:tenant-changed";

export function getActiveTenantId() {
  try {
    return window.localStorage.getItem(ACTIVE_TENANT_KEY) || "tenant-default";
  } catch {
    return "tenant-default";
  }
}

export function setActiveTenantId(tenantId) {
  if (!tenantId) return;
  try {
    window.localStorage.setItem(ACTIVE_TENANT_KEY, tenantId);
  } catch {
    // 无持久化权限时仍可由当前页面状态维持企业上下文。
  }
}

export function syncActiveTenant(user) {
  const tenants = Array.isArray(user?.tenants) ? user.tenants : [];
  if (!tenants.length) return getActiveTenantId();
  const current = getActiveTenantId();
  if (tenants.some((item) => item.id === current)) return current;
  const selected = user?.defaultTenantId || tenants.find((item) => item.isDefault)?.id || tenants[0].id;
  setActiveTenantId(selected);
  return selected;
}

export function clearTenantWorkspaceState() {
  try {
    Object.keys(window.sessionStorage)
      .filter((key) => key.startsWith("forgesight:"))
      .forEach((key) => window.sessionStorage.removeItem(key));
  } catch {
    // 忽略不可用的浏览器存储。
  }
}
