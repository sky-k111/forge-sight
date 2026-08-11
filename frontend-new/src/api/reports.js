import { apiUrl, httpGet, httpPost } from "./_http";
import { currentUserId } from "../lib/auth";

const USER_ID = currentUserId;

export async function listReportTemplates(sourceId) {
  const suffix = sourceId ? `?sourceId=${encodeURIComponent(sourceId)}` : "";
  return (await httpGet(`/api/v1/report-templates${suffix}`)).items;
}

export function getTemplateCoverage(templateId, sourceId) {
  return httpGet(`/api/v1/report-templates/${templateId}/coverage?sourceId=${encodeURIComponent(sourceId)}`);
}

export async function listReports({ ownerId = USER_ID(), sourceId, status } = {}) {
  const params = new URLSearchParams({ ownerId });
  if (sourceId) params.set("sourceId", sourceId);
  if (status) params.set("status", status);
  return (await httpGet(`/api/v1/reports?${params}`)).items;
}

export function getReport(id) {
  return httpGet(`/api/v1/reports/${id}`);
}

export function createReport(payload) {
  return httpPost("/api/v1/reports", {
    ...payload,
    ownerId: payload.ownerId ?? USER_ID(),
  });
}

export function submitReportReview(id, userId = USER_ID()) {
  return httpPost(`/api/v1/reports/${id}/submit-review`, { userId });
}

export function approveReport(id, userId = USER_ID()) {
  return httpPost(`/api/v1/reports/${id}/approve`, { userId });
}

export function publishReport(id, { userId = USER_ID(), pushToFeishu = false } = {}) {
  return httpPost(`/api/v1/reports/${id}/publish`, { userId, pushToFeishu });
}

export function pushReportToFeishu(id) {
  return httpPost(`/api/v1/reports/${id}/deliveries/feishu`);
}

export function getReportIntegrations() {
  return httpGet("/api/v1/report-integrations");
}

export async function listReportSubscriptions(ownerId = USER_ID()) {
  return (await httpGet(`/api/v1/report-subscriptions?ownerId=${encodeURIComponent(ownerId)}`)).items;
}

export function createReportSubscription(payload) {
  return httpPost("/api/v1/report-subscriptions", {
    ...payload,
    ownerId: payload.ownerId ?? USER_ID(),
  });
}

export function reportExportUrl(id, format = "html") {
  return apiUrl(`/api/v1/reports/${id}/export?format=${format}`);
}
