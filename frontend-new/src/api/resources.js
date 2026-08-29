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

function tableParams(sourceId, schemaName) {
  const params = new URLSearchParams();
  if (sourceId) params.set("sourceId", sourceId);
  if (schemaName) params.set("schemaName", schemaName);
  const query = params.toString();
  return query ? `?${query}` : "";
}

export async function getTableFields(tableName, sourceId, schemaName) {
  return httpGet(`/api/resources/tables/${encodeURIComponent(tableName)}/fields${tableParams(sourceId, schemaName)}`);
}

export async function getTablePreview(tableName, sourceId, schemaName) {
  return httpGet(`/api/resources/tables/${encodeURIComponent(tableName)}/preview${tableParams(sourceId, schemaName)}`);
}

export async function listRelations(sourceId) {
  const params = sourceId ? `?sourceId=${sourceId}` : "";
  return httpGet(`/api/resources/relations${params}`);
}

export async function suggestTableSemantics(tableName, sourceId, schemaName) {
  return httpPost(`/api/resources/tables/${encodeURIComponent(tableName)}/suggest${tableParams(sourceId, schemaName)}`);
}

export async function confirmTableSemantics(tableName, sourceId, schemaName, suggestion) {
  return httpPost(`/api/resources/tables/${encodeURIComponent(tableName)}/confirm`, { sourceId, schemaName, suggestion });
}
