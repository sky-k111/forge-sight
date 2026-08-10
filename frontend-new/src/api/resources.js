import { httpGet, httpPost } from "./_http";

// 资源中心（真实后端 /api/resources）。函数签名保持与原 mock 契约一致。
export async function listTables({ keyword = "", topic, sourceId } = {}) {
  const params = new URLSearchParams();
  if (keyword) params.set("keyword", keyword);
  if (topic) params.set("topic", topic);
  if (sourceId) params.set("sourceId", sourceId);
  return httpGet(`/api/resources/tables?${params}`);
}

export async function listUnregisteredTables(sourceId) {
  const params = sourceId ? `?sourceId=${sourceId}` : "";
  return httpGet(`/api/resources/tables/unregistered${params}`);
}

export async function getTableFields(tableName, sourceId) {
  const params = sourceId ? `?sourceId=${sourceId}` : "";
  return httpGet(`/api/resources/tables/${tableName}/fields${params}`);
}

export async function getTablePreview(tableName, sourceId) {
  const params = sourceId ? `?sourceId=${sourceId}` : "";
  return httpGet(`/api/resources/tables/${tableName}/preview${params}`);
}

export async function suggestTableSemantics(tableName, sourceId) {
  return httpPost(`/api/resources/tables/${tableName}/suggest?sourceId=${sourceId}`);
}

export async function confirmTableSemantics(tableName, sourceId, suggestion) {
  return httpPost(`/api/resources/tables/${tableName}/confirm`, { sourceId, suggestion });
}
