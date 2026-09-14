import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { deleteAnalysisSessions, renameAnalysisSession, submitAnalysisRun, subscribeAnalysisRun } from "./api";
import { httpPatch, httpPost, httpRequest } from "../../api/_http";

vi.mock("../../api/_http", () => ({
  apiUrl: (path) => `http://127.0.0.1:8000${path}`,
  httpDelete: vi.fn(),
  httpGet: vi.fn(),
  httpPatch: vi.fn(),
  httpPost: vi.fn(),
  httpRequest: vi.fn(),
}));

vi.mock("../../lib/auth", () => ({
  currentUserId: () => "local-test-user",
  getToken: () => "test-token",
}));

const frame = (seq, type) =>
  `id: ${seq}\nevent: ${type}\ndata: ${JSON.stringify({ type, seq })}\n\n`;

function streamOf(...chunks) {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
}

const okResponse = (body) => ({ ok: true, body });
const errorResponse = (status) => ({ ok: false, status, body: null });

const flushAsync = () => vi.advanceTimersByTimeAsync(0);

test("pins the selected model profile to a submitted run", async () => {
  httpRequest.mockResolvedValueOnce({ runId: "run-model" });

  await submitAnalysisRun("session-1", "分析本月良率", "idem-model", "profile-qwen");

  expect(httpRequest).toHaveBeenCalledWith(
    "/api/analysis/sessions/session-1/runs",
    expect.objectContaining({
      headers: { "Idempotency-Key": "idem-model" },
      body: JSON.stringify({ question: "分析本月良率", modelProfileId: "profile-qwen" }),
    }),
  );
});

test("renames and bulk deletes analysis sessions through persistent APIs", async () => {
  httpPatch.mockResolvedValueOnce({ sessionId: "session-1", title: "六月质量复盘" });
  httpPost.mockResolvedValueOnce({ deletedIds: ["session-1", "session-2"], deletedCount: 2 });

  await renameAnalysisSession("session-1", "六月质量复盘");
  await deleteAnalysisSessions(["session-1", "session-2"]);

  expect(httpPatch).toHaveBeenCalledWith("/api/analysis/sessions/session-1", { title: "六月质量复盘" });
  expect(httpPost).toHaveBeenCalledWith("/api/analysis/sessions/bulk-delete", { sessionIds: ["session-1", "session-2"] });
});

describe("authenticated SSE event subscription", () => {
  let fetchMock;

  beforeEach(() => {
    vi.useFakeTimers();
    fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  test("delivers events parsed from the SSE stream with auth headers", async () => {
    fetchMock.mockResolvedValueOnce(
      okResponse(streamOf(frame(1, "run.started"), frame(2, "run.completed"))),
    );
    const received = [];

    subscribeAnalysisRun("run-1", { onEvent: (event) => received.push(event.type) });
    await flushAsync();

    expect(received).toEqual(["run.started", "run.completed"]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:8000/api/analysis/runs/run-1/stream",
      expect.objectContaining({
        headers: expect.objectContaining({
          Accept: "text/event-stream",
          Authorization: "Bearer test-token",
        }),
        signal: expect.any(AbortSignal),
      }),
    );
  });

  test("skips heartbeat frames", async () => {
    fetchMock.mockResolvedValueOnce(
      okResponse(streamOf("event: heartbeat\ndata: {}\n\n", frame(3, "run.completed"))),
    );
    const received = [];

    subscribeAnalysisRun("run-3", { onEvent: (event) => received.push(event.type) });
    await flushAsync();

    expect(received).toEqual(["run.completed"]);
  });

  test("does not reconnect after a terminal event", async () => {
    fetchMock.mockResolvedValueOnce(
      okResponse(streamOf(frame(1, "run.started"), frame(2, "run.completed"))),
    );
    subscribeAnalysisRun("run-4", {});
    await flushAsync();
    await vi.advanceTimersByTimeAsync(5000);

    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  test("resumes from the last seq via Last-Event-ID after a dropped connection", async () => {
    fetchMock
      .mockResolvedValueOnce(okResponse(streamOf(frame(1, "run.started"))))
      .mockResolvedValueOnce(okResponse(streamOf(frame(2, "run.completed"))));
    subscribeAnalysisRun("run-5", {});

    await flushAsync();
    expect(fetchMock).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(1000);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[1][1].headers).toMatchObject({ "Last-Event-ID": "1" });
  });

  test("reports errors and reconnects when the stream fails", async () => {
    fetchMock
      .mockResolvedValueOnce(errorResponse(500))
      .mockResolvedValueOnce(okResponse(streamOf(frame(9, "run.completed"))));
    const onError = vi.fn();

    subscribeAnalysisRun("run-6", { onError });
    await flushAsync();
    expect(onError).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(1000);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
