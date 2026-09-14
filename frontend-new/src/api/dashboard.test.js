import { beforeEach, describe, expect, it, vi } from "vitest";
import { getDashboard, queryDashboardWidget, saveDashboard } from "./dashboard";

describe("dashboard api", () => {
  beforeEach(() => { vi.restoreAllMocks(); localStorage.clear(); });

  it("saves only the versioned dashboard contract", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue({ ok: true, json: async () => ({ version: 3 }) });
    await saveDashboard(8, { version: 2, period: "last30", showContext: true, widgets: [] });
    expect(fetchSpy.mock.calls[0][0]).toContain("/api/dashboard?sourceId=8");
    expect(fetchSpy.mock.calls[0][1]).toMatchObject({ method: "PUT" });
  });

  it("loads configuration and queries a governed metric reference", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue({ ok: true, json: async () => ({}) });
    await getDashboard(3);
    await queryDashboardWidget({ sourceId: 3, metricId: "M-001", kind: "trend", period: "last7" });
    expect(fetchSpy.mock.calls[0][0]).toContain("sourceId=3");
    expect(JSON.parse(fetchSpy.mock.calls[1][1].body)).not.toHaveProperty("sql");
  });
});
