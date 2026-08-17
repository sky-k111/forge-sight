import { afterEach, describe, expect, test, vi } from "vitest";
import {
  AGENT_THINKING_DELAY_MS,
  getPendingClarification,
  isConversationRun,
  upsertAnalysisRunMessage,
  waitForAgentThinking,
} from "./AnalysisPage";

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

  test("keeps a clarification in the conversation and exposes its interaction id", () => {
    const message = {
      role: "assistant",
      run: {
        status: "clarifying",
        clarification: { interactionId: "interaction-1", question: "请补充统计时间" },
      },
    };

    expect(getPendingClarification([message])).toEqual({ message, clarification: message.run.clarification });
    expect(getPendingClarification([{ role: "assistant", run: { status: "succeeded" } }])).toBeNull();
  });

  test("places a follow-up clarification after the user's answer", () => {
    const firstRun = {
      id: "run-1",
      status: "clarifying",
      clarification: { interactionId: "interaction-1", question: "请补充报告要求" },
    };
    const followUpRun = {
      ...firstRun,
      clarification: { interactionId: "interaction-2", question: "还需要确认报告时间" },
    };
    const items = [
      { id: "run-1", role: "assistant", run: firstRun, animate: false },
      { id: "user-1", role: "user", text: "你好" },
    ];

    expect(upsertAnalysisRunMessage(items, followUpRun)).toEqual([
      ...items,
      { id: "run-1-clarification-interaction-2", role: "assistant", run: followUpRun, animate: false },
    ]);
  });
});
