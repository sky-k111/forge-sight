// 智能建模 / 模型治理 真实后端访问层（FastAPI /api/v1）。
import { httpGet, httpPost } from "./_http";
import { currentUserId } from "../lib/auth";

export const USER_ID = currentUserId;

// —— 算法目录 ——

export async function listAlgorithms() {
  return (await httpGet("/api/v1/modeling/algorithms")).items;
}

// —— 建模任务 ——

export async function listModelingTasks({ ownerId = "", status = "" } = {}) {
  const params = new URLSearchParams();
  if (ownerId) params.set("ownerId", ownerId);
  if (status) params.set("status", status);
  const suffix = params.toString() ? `?${params}` : "";
  return (await httpGet(`/api/v1/modeling/tasks${suffix}`)).items;
}

export function getModelingTask(taskId) {
  return httpGet(`/api/v1/modeling/tasks/${taskId}`);
}

export function createModelingTask(payload) {
  return httpPost("/api/v1/modeling/tasks", {
    ...payload,
    ownerId: payload.ownerId ?? USER_ID(),
  });
}

export function trainModelingTask(
  taskId,
  { strategy = "auto", candidateIds = [], manualModelId = null, userId = USER_ID() } = {},
) {
  return httpPost(`/api/v1/modeling/tasks/${taskId}/train`, {
    strategy,
    candidateIds,
    manualModelId,
    userId,
  });
}

export function getModelingEvaluation(taskId) {
  return httpGet(`/api/v1/modeling/tasks/${taskId}/evaluation`);
}

export function predictWithModel(taskId, records, userId = USER_ID()) {
  return httpPost(`/api/v1/modeling/tasks/${taskId}/predict`, { records, userId });
}

// —— 模型治理 ——

export async function listGovernedModels({ status = "" } = {}) {
  const suffix = status ? `?status=${encodeURIComponent(status)}` : "";
  return (await httpGet(`/api/v1/model-governance/models${suffix}`)).items;
}

export function reviewModel(modelId, { decision, comment = "", userId = USER_ID() }) {
  return httpPost(`/api/v1/model-governance/models/${modelId}/review`, {
    decision,
    comment,
    userId,
  });
}

export function publishModel(modelId, userId = USER_ID()) {
  return httpPost(`/api/v1/model-governance/models/${modelId}/publish`, { userId });
}

export function retireModel(modelId, userId = USER_ID()) {
  return httpPost(`/api/v1/model-governance/models/${modelId}/retire`, { userId });
}
