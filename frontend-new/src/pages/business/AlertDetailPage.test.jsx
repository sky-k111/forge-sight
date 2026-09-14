import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, test, vi } from "vitest";
import {
  getMonitoringAlert,
  getMonitoringDeliveries,
  getMonitoringEvidence,
} from "../../api/monitoring";
import { listSources } from "../../api/datasources";
import AlertDetailPage from "./AlertDetailPage";

vi.mock("../../api/monitoring", () => ({
  getMonitoringAlert: vi.fn(),
  getMonitoringDeliveries: vi.fn(),
  getMonitoringEvidence: vi.fn(),
}));
vi.mock("../../api/datasources", () => ({ listSources: vi.fn() }));
vi.mock("../../features/monitoring/MonitoringEvidenceChart", () => ({
  default: ({ items, rule }) => (
    <div role="img" aria-label="异常指标历史趋势" data-threshold={rule?.condition?.openThreshold}>
      {items.length} 条观测
    </div>
  ),
}));

const alert = {
  id: "alt-1",
  sourceId: 1,
  title: "总装良率异常",
  metricName: "工序良率",
  entityName: "总装",
  entityId: "PR01",
  severity: "high",
  status: "firing",
  currentValue: 0,
  baselineValue: null,
  sampleSize: 20,
  recurrenceCount: 0,
  firstSeenAt: "2026-08-27T08:00:00",
  lastSeenAt: "2026-08-28T08:00:00",
  ruleVersion: 1,
  rule: {
    version: 1,
    name: "工序良率红线",
    grain: "day",
    queryPlan: { kind: "ratio" },
    condition: {
      operator: "lt",
      openThreshold: 0.9,
      closeThreshold: 0.93,
      pendingPeriods: 2,
      recoveryPeriods: 2,
    },
  },
};
function mount() {
  return render(
    <MemoryRouter initialEntries={["/workspace/alerts/alt-1?sourceId=1&status=firing"]}>
      <Routes>
        <Route path="/workspace/alerts/:alertId" element={<AlertDetailPage />} />
        <Route path="/workspace/analysis" element={<h2>新分析目标</h2>} />
      </Routes>
    </MemoryRouter>
  );
}
describe("AlertDetailPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.sessionStorage.clear();
    vi.mocked(getMonitoringAlert).mockResolvedValue(alert);
    vi.mocked(listSources).mockResolvedValue([{ id: 1, name: "华东制造数据" }]);
    vi.mocked(getMonitoringEvidence).mockResolvedValue([
      {
        id: "e-1",
        logicalPeriod: "2026-08-27",
        currentValue: 0,
        baselineValue: null,
        sampleSize: 20,
        qualityStatus: "ok",
        evaluationState: "firing",
        dataCutoffAt: "2026-08-27T23:59:00",
      },
    ]);
    vi.mocked(getMonitoringDeliveries).mockResolvedValue([]);
  });
  test("shows named metrics, genuine rules and preserves list filters", async () => {
    mount();
    await screen.findByRole("heading", { name: "总装良率异常" });
    expect(screen.getByRole("link", { name: /返回异常列表/ }).getAttribute("href")).toBe(
      "/workspace/alerts?sourceId=1&status=firing"
    );
    expect(screen.getAllByText("工序良率").length).toBeGreaterThan(0);
    expect(screen.getByText(/^低于 0.9，/)).toBeTruthy();
    expect(screen.getByText(/不低于 0.93/)).toBeTruthy();
    expect(screen.queryByText(/站内消息仍会独立送达/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /观测明细/ }));
    expect(screen.getByRole("table", { name: "异常观测明细" })).toBeTruthy();
    expect(screen.getByText("通过")).toBeTruthy();
  });
  test("keeps evidence visible if notification loading fails and can retry", async () => {
    vi.mocked(getMonitoringDeliveries).mockRejectedValueOnce(new Error("渠道不可用"));
    mount();
    await screen.findByRole("heading", { name: "总装良率异常" });
    expect(screen.getByText("通知记录加载失败")).toBeTruthy();
    expect(screen.getByRole("img", { name: "异常指标历史趋势" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    await waitFor(() => expect(screen.queryByText("通知记录加载失败")).toBeNull());
    expect(screen.getByText("暂无通知投递记录")).toBeTruthy();
  });
  test("recovers from a detail failure instead of remaining stuck in the error state", async () => {
    vi.mocked(getMonitoringAlert).mockRejectedValueOnce(new Error("记录不可用"));
    mount();
    await screen.findByText("异常详情加载失败");
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    await screen.findByRole("heading", { name: "总装良率异常" });
    expect(screen.queryByText("异常详情加载失败")).toBeNull();
  });
  test("does not let a slow notification endpoint block the main evidence", async () => {
    vi.mocked(getMonitoringDeliveries).mockReturnValueOnce(new Promise(() => {}));
    mount();
    expect(await screen.findByRole("heading", { name: "总装良率异常" })).toBeTruthy();
    expect(screen.getByRole("img", { name: "异常指标历史趋势" })).toBeTruthy();
    expect(screen.getByText("正在加载通知记录…")).toBeTruthy();
  });
  test("does not overlay a newer rule threshold on an older alert", async () => {
    vi.mocked(getMonitoringAlert).mockResolvedValueOnce({
      ...alert,
      rule: { ...alert.rule, version: 2 },
    });
    mount();
    await screen.findByRole("heading", { name: "总装良率异常" });
    expect(screen.getByText(/异常触发版本 v1，与当前规则 v2 不同/)).toBeTruthy();
    expect(
      screen.getByRole("img", { name: "异常指标历史趋势" }).hasAttribute("data-threshold")
    ).toBe(false);
  });
  test("prepares a scoped question without automatically calling a model", async () => {
    mount();
    await screen.findByRole("heading", { name: "总装良率异常" });
    fireEvent.click(screen.getByRole("button", { name: "带入智能分析" }));
    expect(await screen.findByRole("heading", { name: "新分析目标" })).toBeTruthy();
    expect(window.sessionStorage.getItem("forgesight:active-source-id")).toBe("1");
    expect(window.sessionStorage.getItem("forgesight:analysis-new-session")).toBe("1");
    expect(window.sessionStorage.getItem("forgesight:analysis-draft")).toContain("工序良率");
    expect(window.sessionStorage.getItem("forgesight:analysis-draft")).toContain("PR01");
  });
});
