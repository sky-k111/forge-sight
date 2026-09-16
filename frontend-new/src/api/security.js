import { httpGet, httpPost, httpRequest } from "./_http";

// 安全审计（真实后端 /api/security）
export async function listAuditLogs({
  result,
  keyword = "",
  user,
  reviewed,
  cursor, snapshotId, limit,
} = {}) {
  const params = new URLSearchParams();
  if (result) params.set("result", result);
  if (keyword) params.set("keyword", keyword);
  if (user) params.set("user", user);
  if (reviewed) params.set("reviewed", reviewed);
  if (cursor) params.set("cursor", cursor);
  if (snapshotId !== undefined && snapshotId !== null) params.set("snapshot_id", snapshotId);
  if (limit) params.set("limit", limit);
  return httpGet(`/api/security/audit-logs?${params}`);
}

export function getRuntimeConfiguration() {
  return httpGet("/api/security/runtime");
}

export function exportAuditLogs({ result, keyword, user, reviewed, snapshotId } = {}) {
  const params = new URLSearchParams();
  Object.entries({ result, keyword, user, reviewed, snapshot_id: snapshotId }).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") params.set(key, value);
  });
  return httpRequest(`/api/security/audit-logs/export?${params}`, { responseType: "blob" });
}

export async function reviewAuditLog(id) {
  return httpPost(`/api/security/audit-logs/${id}/review`, {});
}

export async function reviewAuditLogsBatch(ids) {
  return httpPost("/api/security/audit-logs/review-batch", { ids });
}
