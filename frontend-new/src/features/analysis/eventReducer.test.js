import { describe, expect, test } from "vitest";

import { initialAnalysisRuntime, reduceAnalysisEvent } from "./eventReducer";

function event(id, seq, type, payload = {}) {
  return { eventId: id, seq, type, runId: "run-1", taskId: "task-1", stepKey: "query.generate", attempt: 1, payload };
}

describe("analysis event reducer", () => {
  test("deduplicates events and aggregates LLM usage", () => {
    const usageEvent = event("evt-1", 1, "llm.usage.recorded", {
      inputTokens: 100,
      outputTokens: 20,
      totalTokens: 120,
      estimatedCost: 0.01,
      currency: "CNY",
      isRetry: true,
    });
    const once = reduceAnalysisEvent(initialAnalysisRuntime, usageEvent);
    const twice = reduceAnalysisEvent(once, usageEvent);
    expect(once.usage.totalTokens).toBe(120);
    expect(once.usage.retryTokens).toBe(120);
    expect(twice).toBe(once);
  });

  test("does not let a late running event overwrite a terminal state", () => {
    const completed = reduceAnalysisEvent(initialAnalysisRuntime, event("evt-1", 1, "run.completed"));
    const late = reduceAnalysisEvent(completed, event("evt-2", 2, "step.started", { status: "running" }));
    expect(late.run.status).toBe("succeeded");
  });
});

