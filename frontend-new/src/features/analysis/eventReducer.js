export const initialAnalysisRuntime = {
  run: null,
  tasks: [],
  steps: {},
  messages: {},
  artifacts: {},
  interaction: null,
  usage: { callCount: 0, retryCallCount: 0, inputTokens: 0, outputTokens: 0, totalTokens: 0, retryTokens: 0, estimatedCost: 0, currency: "CNY" },
  budget: null,
  lastSeq: 0,
  seenEventIds: new Set(),
};

const TERMINAL = new Set(["succeeded", "failed", "blocked", "cancelled"]);

function statusFromEvent(type, payload) {
  if (type === "run.completed") return "succeeded";
  if (type === "run.failed") return "failed";
  if (type === "run.blocked") return "blocked";
  if (type === "run.cancelled") return "cancelled";
  if (type === "run.started") return "running";
  return payload?.status;
}

export function reduceAnalysisEvent(state, event) {
  if (!event?.eventId || state.seenEventIds.has(event.eventId) || event.seq <= state.lastSeq) return state;
  const seenEventIds = new Set(state.seenEventIds).add(event.eventId);
  const next = { ...state, seenEventIds, lastSeq: event.seq };
  const payload = event.payload ?? {};
  const incomingStatus = statusFromEvent(event.type, payload);
  if (incomingStatus && !(TERMINAL.has(state.run?.status) && !TERMINAL.has(incomingStatus))) {
    next.run = { ...(state.run ?? {}), id: event.runId, status: incomingStatus };
  }
  if (event.type === "tasks.planned") next.tasks = payload.tasks ?? [];
  if (event.type.startsWith("step.")) {
    const key = `${event.taskId ?? "run"}:${event.stepKey}:${event.attempt ?? 1}`;
    next.steps = { ...state.steps, [key]: { ...(state.steps[key] ?? {}), ...payload, taskId: event.taskId, stepKey: event.stepKey, attempt: event.attempt } };
  }
  if (event.type === "artifact.created") {
    next.artifacts = { ...state.artifacts, [payload.artifactId]: payload };
  }
  if (event.type === "clarification.requested") next.interaction = payload;
  if (event.type === "clarification.resolved") next.interaction = null;
  if (event.type === "llm.usage.recorded") {
    next.usage = {
      ...state.usage,
      callCount: state.usage.callCount + 1,
      retryCallCount: state.usage.retryCallCount + (payload.isRetry ? 1 : 0),
      inputTokens: state.usage.inputTokens + (payload.inputTokens ?? 0),
      outputTokens: state.usage.outputTokens + (payload.outputTokens ?? 0),
      totalTokens: state.usage.totalTokens + (payload.totalTokens ?? 0),
      retryTokens: state.usage.retryTokens + (payload.isRetry ? payload.totalTokens ?? 0 : 0),
      estimatedCost: state.usage.estimatedCost + (payload.estimatedCost ?? 0),
      currency: payload.currency ?? state.usage.currency,
    };
  }
  if (event.type === "run.budget.warning" || event.type === "run.budget.exceeded") next.budget = payload;
  return next;
}

