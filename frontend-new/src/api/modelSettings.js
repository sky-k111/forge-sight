import { httpGet, httpPatch, httpPost } from "./_http";

export function getLlmSettings() {
  return httpGet("/api/model-settings/llm");
}

export function updateLlmSettings(payload) {
  return httpPatch("/api/model-settings/llm", payload);
}

export function discoverLlmModels(payload) {
  return httpPost("/api/model-settings/llm/discover-models", payload);
}
