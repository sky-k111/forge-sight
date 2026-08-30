import { httpGet, httpPatch } from "./_http";

export function getPlannerSettings() {
  return httpGet("/api/analysis/planner-settings");
}

export function updatePlannerSettings(payload) {
  return httpPatch("/api/analysis/planner-settings", payload);
}
