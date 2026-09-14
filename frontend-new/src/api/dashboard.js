import { httpDelete, httpGet, httpPost, httpRequest } from "./_http";
import { currentUserId } from "../lib/auth";

function query(sourceId) {
  const params = new URLSearchParams({ sourceId: String(sourceId), userId: currentUserId() });
  return params.toString();
}

export function getDashboard(sourceId) {
  return httpGet(`/api/dashboard?${query(sourceId)}`);
}

export function saveDashboard(sourceId, config) {
  return httpRequest(`/api/dashboard?${query(sourceId)}`, {
    method: "PUT",
    body: JSON.stringify(config),
  });
}

export function resetDashboard(sourceId) {
  return httpDelete(`/api/dashboard?${query(sourceId)}`);
}

export function queryDashboardWidget(payload) {
  return httpPost(`/api/dashboard/query?userId=${encodeURIComponent(currentUserId())}`, payload);
}
