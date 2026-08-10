import { apiUrl, httpDelete, httpGet, httpPost, httpRequest } from "../../api/_http";

export function createAnalysisSession({ title, sourceId, userId = "local-test-user" }) {
  return httpPost("/api/analysis/sessions", { title, sourceId, userId });
}

export function listAnalysisSessions(userId = "local-test-user") {
  return httpGet(`/api/analysis/sessions?userId=${encodeURIComponent(userId)}`);
}

export function getAnalysisSession(sessionId) {
  return httpGet(`/api/analysis/sessions/${sessionId}`);
}

export function deleteAnalysisSession(sessionId, userId = "local-test-user") {
  return httpDelete(`/api/analysis/sessions/${sessionId}?userId=${encodeURIComponent(userId)}`);
}

export function submitAnalysisRun(sessionId, question, idempotencyKey = crypto.randomUUID()) {
  return httpRequest(`/api/analysis/sessions/${sessionId}/runs`, {
    method: "POST",
    headers: { "Idempotency-Key": idempotencyKey },
    body: JSON.stringify({ question }),
  });
}

export function getAnalysisRun(runId) {
  return httpGet(`/api/analysis/runs/${runId}`);
}

export function getAnalysisEvents(runId, afterSeq = 0) {
  return httpGet(`/api/analysis/runs/${runId}/events?afterSeq=${afterSeq}`);
}

export function getAnalysisUsage(runId, includeAttempts = false) {
  return httpGet(`/api/analysis/runs/${runId}/usage?includeAttempts=${includeAttempts}`);
}

export function cancelAnalysisRun(runId) {
  return httpPost(`/api/analysis/runs/${runId}/cancel`);
}

export function respondToClarification(interactionId, answer) {
  return httpPost(`/api/analysis/interactions/${interactionId}/responses`, { answer });
}

export function getAnalysisCheckpoints(runId) {
  return httpGet(`/api/analysis/runs/${runId}/checkpoints`);
}

export function createAnalysisBranch(
  checkpointId,
  {
    title,
    userId = "local-test-user",
    instruction,
    adjustments = {},
    reuseArtifacts = "auto",
  } = {},
) {
  return httpPost(`/api/analysis/checkpoints/${checkpointId}/branches`, {
    title,
    userId,
    instruction,
    adjustments,
    reuseArtifacts,
  });
}

const EVENT_TYPES = [
  "run.accepted", "run.started", "run.resumed", "run.status.changed", "run.completed", "run.failed", "run.blocked", "run.cancelled",
  "tasks.planned", "task.started", "task.completed", "task.failed", "task.skipped",
  "step.started", "step.progress", "step.completed", "step.retrying", "step.failed", "step.blocked", "step.skipped",
  "clarification.requested", "clarification.resolved", "clarification.expired",
  "context.compiled", "sql.generated", "sql.validation.completed", "sql.execution.started", "sql.execution.completed",
  "artifact.created", "message.delta", "message.completed", "checkpoint.created", "branch.created", "state.snapshot",
  "llm.call.started", "llm.usage.recorded", "run.budget.warning", "run.budget.exceeded", "heartbeat",
];

export function subscribeAnalysisRun(runId, { onEvent, onError, includeAdmin = false } = {}) {
  const source = new EventSource(apiUrl(`/api/analysis/runs/${runId}/stream?includeAdmin=${includeAdmin}`));
  const listeners = [];
  for (const type of EVENT_TYPES) {
    const listener = (message) => {
      if (!message.data || message.data === "{}") return;
      try {
        onEvent?.(JSON.parse(message.data));
      } catch (error) {
        onError?.(error);
      }
    };
    source.addEventListener(type, listener);
    listeners.push([type, listener]);
  }
  source.onerror = (error) => onError?.(error);
  return () => {
    for (const [type, listener] of listeners) source.removeEventListener(type, listener);
    source.close();
  };
}
