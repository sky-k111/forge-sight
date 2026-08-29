import { httpGet, httpPatch, httpPost } from "./_http";

// 数据源管理（真实后端 /api/datasources）
export async function listSources() {
  return httpGet("/api/datasources");
}

export async function listSourceTypes() {
  return httpGet("/api/datasources/types");
}

export async function listSourceSchemas(sourceId) {
  return httpGet(`/api/datasources/${sourceId}/schemas`);
}

export async function createSource(payload) {
  return httpPost("/api/datasources", payload);
}

// 修改已有数据源配置；password 留空表示不修改
export async function updateSource(sourceId, payload) {
  return httpPatch(`/api/datasources/${sourceId}`, payload);
}

export async function testSource(sourceId) {
  return httpPost(`/api/datasources/${sourceId}/test`);
}

export async function scanSource(sourceId) {
  return httpPost(`/api/datasources/${sourceId}/scan`);
}

export async function getSemanticIndexStatus(sourceId) {
  return httpGet(`/api/datasources/${sourceId}/semantic-index`);
}

export async function rebuildSemanticIndex(sourceId) {
  return httpPost(`/api/datasources/${sourceId}/semantic-index/rebuild`);
}

export async function retrySemanticIndexJob(jobId) {
  return httpPost(`/api/datasources/jobs/${jobId}/retry`);
}
