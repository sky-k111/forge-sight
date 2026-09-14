import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, test, vi } from "vitest";

import { listMonitoringAlerts } from "../../api/monitoring";
import { listSources } from "../../api/datasources";
import AlertsPage from "./AlertsPage";

vi.mock("../../api/monitoring", () => ({ listMonitoringAlerts: vi.fn() }));
vi.mock("../../api/datasources", () => ({ listSources: vi.fn() }));

const alerts = [
  {
    id: "alt-1",
    sourceId: 1,
    title: "总装检测工序良率异常",
    metricName: "工序良率",
    entityName: "总装检测",
    entityId: "PR01",
    severity: "high",
    status: "firing",
    currentValue: 0.84,
    baselineValue: 0.96,
    sampleSize: 120,
    lastSeenAt: "2026-08-27T08:00:00",
  },
  {
    id: "alt-2",
    sourceId: 1,
    title: "冲压产量恢复",
    metricName: "产量",
    entityName: "冲压",
    entityId: "PR02",
    severity: "medium",
    status: "resolved",
    currentValue: 0,
    baselineValue: null,
    sampleSize: 20,
    lastSeenAt: "2026-08-28T08:00:00",
  },
  {
    id: "alt-3",
    sourceId: 1,
    title: "涂装库存异常",
    metricName: "库存",
    entityName: "涂装",
    entityId: "PR03",
    severity: "critical",
    status: "recovering",
    currentValue: null,
    baselineValue: 12,
    sampleSize: null,
    lastSeenAt: "2026-08-26T08:00:00",
  },
];

function mount(path = "/workspace/alerts") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/workspace/alerts" element={<AlertsPage />} />
        <Route path="/workspace/alerts/:alertId" element={<h2>异常详情目标</h2>} />
      </Routes>
    </MemoryRouter>
  );
}

describe("AlertsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listSources).mockResolvedValue([
      { id: 1, name: "华东制造数据" },
      { id: 2, name: "华南制造数据" },
    ]);
    vi.mocked(listMonitoringAlerts).mockResolvedValue(alerts);
  });

  test("shows a read-only table with explicit metrics and raw values", async () => {
    mount();
    const table = await screen.findByRole("table", { name: "异常记录" });
    expect(within(table).getByText("工序良率")).toBeTruthy();
    expect(within(table).getByText("0.84")).toBeTruthy();
    expect(within(table).getByText("0")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /关闭|验收|处理/ })).toBeNull();
  });

  test("filters locally without changing the scope summary", async () => {
    mount();
    await screen.findByRole("table", { name: "异常记录" });
    fireEvent.click(screen.getByRole("button", { name: /已恢复/ }));
    expect(screen.queryByRole("link", { name: "查看证据：总装检测工序良率异常" })).toBeNull();
    expect(screen.getByRole("link", { name: "查看证据：冲压产量恢复" })).toBeTruthy();
    const summary = screen.getByRole("region", { name: "范围内异常汇总" });
    expect(within(summary).getByTestId("monitoring-count-firing").textContent).toBe("1条");
    expect(listMonitoringAlerts).toHaveBeenCalledTimes(1);
    fireEvent.change(screen.getByRole("searchbox", { name: "搜索异常" }), {
      target: { value: "不存在的工序" },
    });
    expect(screen.getByText("没有匹配的异常记录")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "清除筛选" }));
    expect(screen.getByRole("link", { name: "查看证据：总装检测工序良率异常" })).toBeTruthy();
  });

  test("scopes the backend request by source and preserves filters in detail links", async () => {
    mount("/workspace/alerts?sourceId=1&status=firing&severity=high");
    const link = await screen.findByRole("link", { name: "查看证据：总装检测工序良率异常" });
    expect(link.getAttribute("href")).toContain("sourceId=1");
    expect(link.getAttribute("href")).toContain("status=firing");
    expect(listMonitoringAlerts).toHaveBeenCalledWith({ sourceId: "1", limit: 300 });
    fireEvent.change(screen.getByRole("combobox", { name: "监测数据源" }), {
      target: { value: "2" },
    });
    await waitFor(() =>
      expect(listMonitoringAlerts).toHaveBeenLastCalledWith({ sourceId: "2", limit: 300 })
    );
  });

  test("does not turn an empty record list into a claim that monitoring is healthy", async () => {
    vi.mocked(listMonitoringAlerts).mockResolvedValue([]);
    mount();
    expect(await screen.findByText("当前范围暂无异常记录")).toBeTruthy();
    expect(screen.queryByText(/监控仍在后台运行|监测正常|全部正常/)).toBeNull();
    expect(screen.getByText(/不代表监测任务的运行状态/)).toBeTruthy();
  });

  test("retries a failed load and ignores an older source response", async () => {
    vi.mocked(listMonitoringAlerts).mockRejectedValueOnce(new Error("连接失败"));
    mount();
    await screen.findByText("异常记录加载失败");
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    await screen.findByRole("table", { name: "异常记录" });
    let finishOld;
    vi.mocked(listMonitoringAlerts)
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            finishOld = resolve;
          })
      )
      .mockResolvedValueOnce([{ ...alerts[1], title: "最新数据源记录" }]);
    fireEvent.change(screen.getByRole("combobox", { name: "监测数据源" }), {
      target: { value: "1" },
    });
    await waitFor(() => expect(finishOld).toBeTypeOf("function"));
    fireEvent.change(screen.getByRole("combobox", { name: "监测数据源" }), {
      target: { value: "2" },
    });
    await screen.findByText("最新数据源记录");
    finishOld(alerts);
    await waitFor(() => expect(screen.queryByText("总装检测工序良率异常")).toBeNull());
  });

  test("paginates records instead of clipping a long result list", async () => {
    vi.mocked(listMonitoringAlerts).mockResolvedValue(
      Array.from({ length: 32 }, (_, i) => ({
        ...alerts[0],
        id: `alert-${i}`,
        title: `异常 ${i + 1}`,
      }))
    );
    mount();
    await screen.findByRole("table", { name: "异常记录" });
    expect(screen.getAllByRole("link", { name: /查看证据：/ })).toHaveLength(15);
    fireEvent.click(screen.getByRole("button", { name: "下一页异常" }));
    expect(screen.getByRole("link", { name: "查看证据：异常 16" })).toBeTruthy();
    expect(screen.queryByRole("link", { name: "查看证据：异常 1" })).toBeNull();
  });
});
