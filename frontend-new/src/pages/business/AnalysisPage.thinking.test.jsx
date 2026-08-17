import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";
import {
  AGENT_THINKING_DELAY_MS,
  ReportGenerationMessage,
  buildAnalysisConversationItems,
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

  test("restores the original user request instead of the generated report prompt", () => {
    const generatedPrompt = "为《2026-07-01 至 2026-07-31 制造运营综合周报》完成以下相互独立但范围一致的分析任务。";
    const items = buildAnalysisConversationItems(
      {
        messages: [
          {
            id: "message-report",
            runId: "run-report",
            role: "user",
            content: "帮我生成最后一周有数据的周报",
            createdAt: "2026-08-17T10:00:00+08:00",
            sequenceNo: 1,
          },
        ],
      },
      [{
        id: "run-report",
        question: generatedPrompt,
        status: "succeeded",
        createdAt: "2026-08-17T10:00:01+08:00",
        tasks: [],
        steps: [],
        artifacts: [],
        checkpoints: [],
        usage: {},
        assistantMessage: {
          metadata: {
            report: { id: "report-1", title: "制造运营综合周报", status: "published" },
          },
        },
      }],
    );

    expect(items.find((item) => item.role === "user")).toMatchObject({
      text: "帮我生成最后一周有数据的周报",
    });
    expect(items.some((item) => item.text === generatedPrompt)).toBe(false);
    expect(items.some((item) => item.role === "assistant" && item.run?.report?.id === "report-1")).toBe(true);
  });

  test("hides a persisted internal report prompt when no original user text exists", () => {
    const generatedPrompt = "为《2026-07-01 至 2026-07-31 制造运营综合周报》完成以下相互独立但范围一致的分析任务。";
    const items = buildAnalysisConversationItems(
      {
        messages: [{
          id: "message-internal",
          runId: "run-internal",
          role: "user",
          content: generatedPrompt,
          createdAt: "2026-08-17T10:00:00+08:00",
          sequenceNo: 1,
        }],
      },
      [{
        id: "run-internal",
        question: generatedPrompt,
        status: "running",
        createdAt: "2026-08-17T10:00:01+08:00",
        tasks: [],
        steps: [],
        artifacts: [],
        checkpoints: [],
        usage: {},
        assistantMessage: {
          metadata: { report: { id: "report-2", title: "制造运营综合周报", status: "queued" } },
        },
      }],
    );

    expect(items.find((item) => item.role === "user")?.text).toBe("生成《制造运营综合周报》");
    expect(items.some((item) => item.text === generatedPrompt)).toBe(false);
  });

  test("opens the message center from a generated report card", () => {
    const onOpenMessages = vi.fn();
    render(
      <ReportGenerationMessage
        report={{ id: "report-1", title: "制造运营综合周报", status: "published" }}
        onOpenMessages={onOpenMessages}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "打开消息中心" }));
    expect(onOpenMessages).toHaveBeenCalledTimes(1);
  });
});
