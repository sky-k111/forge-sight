import { httpGet } from "./_http";

// 安全审计（真实后端 /api/security）
export async function listAuditLogs({ result, keyword = "" } = {}) {
  const params = new URLSearchParams();
  if (result) params.set("result", result);
  if (keyword) params.set("keyword", keyword);
  return httpGet(`/api/security/audit-logs?${params}`);
}
