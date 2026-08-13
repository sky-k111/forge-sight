import { httpGet, httpPatch, httpPost } from "./_http";

function queryString(filters = {}) {
  const params = new URLSearchParams();
  Object.entries(filters).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") params.set(key, String(value));
  });
  const query = params.toString();
  return query ? `?${query}` : "";
}

export function listMessages(filters = {}) {
  return httpGet(`/api/v1/messages${queryString(filters)}`);
}

export async function getUnreadCount() {
  return (await httpGet("/api/v1/messages/unread-count")).count;
}

export function getMessage(id) {
  return httpGet(`/api/v1/messages/${id}`);
}

export function markMessageRead(id) {
  return httpPost(`/api/v1/messages/${id}/read`);
}

export async function listAnnouncements(status) {
  return (await httpGet(`/api/v1/announcements${queryString({ status })}`)).items;
}

export function createAnnouncement(payload) {
  return httpPost("/api/v1/announcements", payload);
}

export function updateAnnouncement(id, payload) {
  return httpPatch(`/api/v1/announcements/${id}`, payload);
}

export function publishAnnouncement(id) {
  return httpPost(`/api/v1/announcements/${id}/publish`);
}

export function withdrawAnnouncement(id, reason) {
  return httpPost(`/api/v1/announcements/${id}/withdraw`, { reason });
}

export async function listDeliveries(filters = {}) {
  return (await httpGet(`/api/v1/deliveries${queryString(filters)}`)).items;
}

export function retryDelivery(id, confirmUnknown = false) {
  return httpPost(`/api/v1/deliveries/${id}/retry${queryString({ confirmUnknown: confirmUnknown || undefined })}`);
}
