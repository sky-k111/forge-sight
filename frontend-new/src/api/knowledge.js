import { httpDelete, httpGet, httpPatch, httpPost } from "./_http";

function queryString(filters = {}) {
  const params = new URLSearchParams();
  Object.entries(filters).forEach(([key, value]) => {
    if (value !== undefined && value !== null && String(value).trim()) params.set(key, value);
  });
  const text = params.toString();
  return text ? `?${text}` : "";
}

export function listTopics() {
  return httpGet("/api/knowledge/topics");
}

export function createTopic(topic) {
  return httpPost("/api/knowledge/topics", topic);
}

export function updateTopic(code, topic) {
  return httpPatch(`/api/knowledge/topics/${code}`, topic);
}

export function deleteTopic(code) {
  return httpDelete(`/api/knowledge/topics/${code}`);
}

export function listObjects({ keyword = "", topic } = {}) {
  return httpGet(`/api/knowledge/objects${queryString({ keyword, topic })}`);
}

export function createObject(object) {
  return httpPost("/api/knowledge/objects", object);
}

export function updateObject(id, object) {
  return httpPatch(`/api/knowledge/objects/${id}`, object);
}

export function deleteObject(id) {
  return httpDelete(`/api/knowledge/objects/${id}`);
}

export function listRules({ topic } = {}) {
  return httpGet(`/api/knowledge/rules${queryString({ topic })}`);
}

export function createRule(rule) {
  return httpPost("/api/knowledge/rules", rule);
}

export function updateRule(id, rule) {
  return httpPatch(`/api/knowledge/rules/${id}`, rule);
}

export function deleteRule(id) {
  return httpDelete(`/api/knowledge/rules/${id}`);
}

export function listMetrics({ keyword = "", topic, status, sourceId } = {}) {
  return httpGet(`/api/knowledge/metrics${queryString({ keyword, topic, status, sourceId })}`);
}

export function getMetric(id, sourceId) {
  return httpGet(`/api/knowledge/metrics/${id}${queryString({ sourceId })}`);
}

export function saveMetric(metric) {
  return metric.id
    ? httpPatch(`/api/knowledge/metrics/${metric.id}`, metric)
    : httpPost("/api/knowledge/metrics", metric);
}

export function publishMetric(id, sourceId) {
  return httpPost(`/api/knowledge/metrics/${id}/publish${queryString({ sourceId })}`);
}

export function submitMetricForReview(id, sourceId) {
  return httpPost(`/api/knowledge/metrics/${id}/submit-review${queryString({ sourceId })}`);
}

export function deleteMetric(id) {
  return httpDelete(`/api/knowledge/metrics/${id}`);
}

export function listMetricCandidates({ sourceId, status } = {}) {
  return httpGet(`/api/knowledge/metric-candidates${queryString({ sourceId, status })}`);
}

export function generateMetricCandidates(sourceId) {
  return httpPost(`/api/knowledge/metric-candidates/generate${queryString({ sourceId })}`);
}

export function acceptMetricCandidate(id) {
  return httpPost(`/api/knowledge/metric-candidates/${id}/accept`, { reviewer: "管理员" });
}

export function rejectMetricCandidate(id, reason = "") {
  return httpPost(`/api/knowledge/metric-candidates/${id}/reject`, { reviewer: "管理员", reason });
}
