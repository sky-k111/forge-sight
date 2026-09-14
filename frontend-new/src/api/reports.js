import { apiUrl, httpDelete, httpGet, httpPost, httpRequest } from "./_http";
import { currentUserId } from "../lib/auth";
import { getToken } from "../lib/auth";

const USER_ID = currentUserId;

export async function listReportTemplates(sourceId) {
  const suffix = sourceId ? `?sourceId=${encodeURIComponent(sourceId)}` : "";
  return (await httpGet(`/api/v1/report-templates${suffix}`)).items;
}

export function getTemplateCoverage(templateId, sourceId) {
  return httpGet(`/api/v1/report-templates/${templateId}/coverage?sourceId=${encodeURIComponent(sourceId)}`);
}

export function getReportComposerCatalog(sourceId) {
  return httpGet(`/api/v1/report-composer/catalog?sourceId=${encodeURIComponent(sourceId)}`);
}

export async function listReportCompositions(sourceId) {
  return (await httpGet(`/api/v1/report-compositions?sourceId=${encodeURIComponent(sourceId)}&ownerId=${encodeURIComponent(USER_ID())}`)).items;
}

export function saveReportComposition(sourceId, composition, saved) {
  const path = saved?.id
    ? `/api/v1/report-compositions/${saved.id}`
    : "/api/v1/report-compositions";
  return httpRequest(`${path}?sourceId=${encodeURIComponent(sourceId)}&ownerId=${encodeURIComponent(USER_ID())}`, {
    method: saved?.id ? "PUT" : "POST",
    body: JSON.stringify({ version: saved?.version ?? 0, composition }),
  });
}

export function deleteReportComposition(sourceId, templateId) {
  return httpDelete(`/api/v1/report-compositions/${templateId}?sourceId=${encodeURIComponent(sourceId)}&ownerId=${encodeURIComponent(USER_ID())}`);
}

export function previewReportComposition(payload) {
  return httpPost(`/api/v1/report-compositions/preview?ownerId=${encodeURIComponent(USER_ID())}`, payload);
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

export function reportFileUrl(file) {
  if (!file?.downloadUrl) return null;
  return apiUrl(file.downloadUrl);
}

export async function downloadReportFile(file, fallbackName = "ForgeSight-weekly-report") {
  const url = reportFileUrl(file);
  if (!url) throw new Error("报告文件尚未就绪");
  const token = getToken();
  const response = await fetch(url, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
  if (!response.ok) throw new Error(response.status === 409 ? "报告文件仍在生成，请稍后再试" : "报告文件下载失败");
  const blob = await response.blob();
  const objectUrl = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = objectUrl;
  link.download = `${fallbackName}.${file.format === "xlsx" ? "xlsx" : "pdf"}`;
  link.click();
  URL.revokeObjectURL(objectUrl);
}
