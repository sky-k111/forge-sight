import { httpGet, httpPatch } from "./_http";

export function getLlmSettings() {
  return httpGet("/api/model-settings/llm");
}

export function updateLlmSettings(payload) {
  return httpPatch("/api/model-settings/llm", payload);
}
