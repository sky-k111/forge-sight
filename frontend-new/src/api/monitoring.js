import { httpGet, httpPost } from "./_http";

function queryString(filters = {}) {
  const params = new URLSearchParams();
  Object.entries(filters).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") {
      params.set(key, String(value));
    }
  });
  const query = params.toString();
  return query ? `?${query}` : "";
}

export async function listMonitoringAlerts(filters = {}) {
  return (await httpGet(`/api/v1/monitoring/alerts${queryString(filters)}`)).items;
}

export function getMonitoringAlert(id) {
  return httpGet(`/api/v1/monitoring/alerts/${id}`);
}

export async function getMonitoringEvidence(id) {
  return (await httpGet(`/api/v1/monitoring/alerts/${id}/evidence`)).items;
}

export async function getMonitoringDeliveries(id) {
  return (await httpGet(`/api/v1/monitoring/alerts/${id}/deliveries`)).items;
}

export async function listMonitoringRules() {
  return (await httpGet("/api/v1/monitoring/rules")).items;
}

export function createMonitoringRule(payload) {
  return httpPost("/api/v1/monitoring/rules", payload);
}

export function validateMonitoringRule(id) {
  return httpPost(`/api/v1/monitoring/rules/${id}/validate`);
}

export function publishMonitoringRule(id) {
  return httpPost(`/api/v1/monitoring/rules/${id}/publish`);
}

export function pauseMonitoringRule(id) {
  return httpPost(`/api/v1/monitoring/rules/${id}/pause`);
}

export function resumeMonitoringRule(id) {
  return httpPost(`/api/v1/monitoring/rules/${id}/resume`);
}

export function runMonitoringRule(id) {
  return httpPost(`/api/v1/monitoring/rules/${id}/run`);
}

export async function listMonitoringRuns() {
  return (await httpGet("/api/v1/monitoring/runs")).items;
}

export function retryMonitoringRun(id) {
  return httpPost(`/api/v1/monitoring/runs/${id}/retry`);
}

export function getMonitoringMetrics() {
  return httpGet("/api/v1/monitoring/operations/metrics");
}
