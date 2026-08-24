import { httpGet, httpPatch, httpPost } from "./_http";

export function getSkillSettings() {
  return httpGet("/api/skills/settings");
}

export function updateSkillSettings(enabled) {
  return httpPatch("/api/skills/settings", { enabled });
}

export function listAnalysisSkills(status) {
  const query = status ? `?status=${encodeURIComponent(status)}` : "";
  return httpGet(`/api/skills${query}`);
}

export function reviewAnalysisSkill(skillId, action) {
  return httpPost(`/api/skills/${skillId}/${action}`);
}

export function submitSkillFeedback(runId, rating, comment = "") {
  return httpPost("/api/skills/feedback", { runId, rating, comment });
}
