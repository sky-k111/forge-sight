import { afterEach, describe, expect, test, vi } from "vitest";
import { AGENT_THINKING_DELAY_MS, isConversationRun, waitForAgentThinking } from "./AnalysisPage";

describe("agent response thinking delay", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  test("holds an immediate answer for 1.5 seconds", async () => {
    vi.useFakeTimers();
    let resolved = false;
    const pending = waitForAgentThinking().then(() => {
      resolved = true;
    });

    await vi.advanceTimersByTimeAsync(AGENT_THINKING_DELAY_MS - 1);
    expect(resolved).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    await pending;
    expect(resolved).toBe(true);
  });

  test("keeps ordinary conversation out of the analysis evidence panel", () => {
    expect(isConversationRun({ result: { kind: "conversation" } })).toBe(true);
    expect(isConversationRun({ result: { kind: "execution" } })).toBe(false);
  });
});
