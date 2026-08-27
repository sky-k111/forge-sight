import { httpGet } from "./_http";

export function listMyTenants() {
  return httpGet("/api/tenants/me");
}

export function getCurrentTenant() {
  return httpGet("/api/tenants/current");
}

export function listCurrentTenantMembers() {
  return httpGet("/api/tenants/current/members");
}
