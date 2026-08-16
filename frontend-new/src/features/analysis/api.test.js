import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { httpGet } from "../../api/_http";
import { subscribeAnalysisRun } from "./api";

vi.mock("../../api/_http", () => ({
  apiUrl: (path) => `http://127.0.0.1:8000${path}`,
  httpDelete: vi.fn(),
  httpGet: vi.fn(),
  httpPost: vi.fn(),
  httpRequest: vi.fn(),
}));

describe("authenticated analysis event subscription", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
    vi.stubGlobal("EventSource", class ForbiddenEventSource {
      constructor() {
        throw new Error("native EventSource cannot carry the login token");
      }
    });
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  test("delivers the terminal event through authenticated incremental requests", async () => {
    httpGet
      .mockResolvedValueOnce({
        items: [{ eventId: "evt-1", seq: 1, type: "run.started", payload: { status: "running" } }],
      })
      .mockResolvedValueOnce({
        items: [{ eventId: "evt-2", seq: 2, type: "run.completed", payload: { status: "succeeded" } }],
      });
    const received = [];

    const unsubscribe = subscribeAnalysisRun("run-1", {
      onEvent: (event) => received.push(event.type),
    });
    await vi.advanceTimersByTimeAsync(400);

    expect(received).toEqual(["run.started", "run.completed"]);
    expect(httpGet).toHaveBeenNthCalledWith(1, "/api/analysis/runs/run-1/events?afterSeq=0");
    expect(httpGet).toHaveBeenNthCalledWith(2, "/api/analysis/runs/run-1/events?afterSeq=1");
    unsubscribe();
  });
});
