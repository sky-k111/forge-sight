import { httpGet, httpPost } from "./_http";

// 安全审计（真实后端 /api/security）
export async function listAuditLogs({
  result,
  keyword = "",
  user,
  reviewed,
} = {}) {
  const params = new URLSearchParams();
  if (result) params.set("result", result);
  if (keyword) params.set("keyword", keyword);
  if (user) params.set("user", user);
  if (reviewed) params.set("reviewed", reviewed);
  return httpGet(`/api/security/audit-logs?${params}`);
}

export async function reviewAuditLog(id) {
  return httpPost(`/api/security/audit-logs/${id}/review`, {});
}

export async function reviewAuditLogsBatch(ids) {
  return httpPost("/api/security/audit-logs/review-batch", { ids });
}
