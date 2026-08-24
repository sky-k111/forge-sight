import { httpDelete, httpGet, httpPatch, httpPost } from "./_http";

export function getMemorySettings() {
  return httpGet("/api/memory/settings");
}

export function updateMemorySettings(enabled, config) {
  return httpPatch("/api/memory/settings", { enabled, config });
}

export function listMemories({ status, kind, sourceId } = {}) {
  const params = new URLSearchParams();
  if (status) params.set("status", status);
  if (kind) params.set("kind", kind);
  if (sourceId) params.set("sourceId", sourceId);
  const query = params.toString();
  return httpGet(`/api/memory${query ? `?${query}` : ""}`);
}

export function createMemory(payload) {
  return httpPost("/api/memory", payload);
}

export function correctMemory(memoryId, content) {
  return httpPost(`/api/memory/${memoryId}/correct`, { content });
}

export function forgetMemory(memoryId) {
  return httpDelete(`/api/memory/${memoryId}`);
}

export function getMemoryGovernanceSummary() {
  return httpGet("/api/memory/governance/summary");
}

export function listGovernanceMemories({ status, kind } = {}) {
  const params = new URLSearchParams();
  if (status) params.set("status", status);
  if (kind) params.set("kind", kind);
  const query = params.toString();
  return httpGet(`/api/memory/governance/items${query ? `?${query}` : ""}`);
}

export function consolidateMemories() {
  return httpPost("/api/memory/governance/consolidate");
}

export function promoteMemory(memoryId) {
  return httpPost(`/api/memory/${memoryId}/promote`);
}
