import { httpDelete, httpGet, httpPost, httpRequest } from "../../api/_http";
import { currentUserId } from "../../lib/auth";

export function createAnalysisSession({ title, sourceId, userId = currentUserId() }) {
  return httpPost("/api/analysis/sessions", { title, sourceId, userId });
}

export function listAnalysisSessions(userId = currentUserId()) {
  return httpGet(`/api/analysis/sessions?userId=${encodeURIComponent(userId)}`);
}

export function getAnalysisSession(sessionId) {
  return httpGet(`/api/analysis/sessions/${sessionId}`);
}

export function deleteAnalysisSession(sessionId, userId = currentUserId()) {
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
    userId = currentUserId(),
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
  "modeling.proposed", "run.modeling_proposed",
];

export function subscribeAnalysisRun(runId, { onEvent, onError, includeAdmin = false } = {}) {
  let stopped = false;
  let timer = null;
  let afterSeq = 0;
  const terminalEvents = new Set(["run.completed", "run.failed", "run.blocked", "run.cancelled"]);

  const poll = async () => {
    if (stopped) return;
    try {
      const suffix = includeAdmin ? "&includeAdmin=true" : "";
      const response = await httpGet(
        `/api/analysis/runs/${runId}/events?afterSeq=${afterSeq}${suffix}`,
      );
      if (stopped) return;
      const events = (response.items ?? []).filter((event) => EVENT_TYPES.includes(event.type));
      for (const event of events) {
        afterSeq = Math.max(afterSeq, Number(event.seq) || 0);
        onEvent?.(event);
      }
      if (events.some((event) => terminalEvents.has(event.type))) {
        stopped = true;
        return;
      }
      timer = window.setTimeout(poll, events.length >= 500 ? 0 : 350);
    } catch (error) {
      if (stopped) return;
      onError?.(error);
      timer = window.setTimeout(poll, 700);
    }
  };

  poll();
  return () => {
    stopped = true;
    window.clearTimeout(timer);
  };
}
