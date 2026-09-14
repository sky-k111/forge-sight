import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, test, vi } from "vitest";

import ReportComposerPage from "./ReportComposerPage";
import { listSources } from "../../api/datasources";
import {
  deleteReportComposition,
  getReportComposerCatalog,
  listReportCompositions,
  previewReportComposition,
  saveReportComposition,
} from "../../api/reports";

vi.mock("../../api/datasources", () => ({
  listSources: vi.fn(),
}));

vi.mock("../../api/reports", () => ({
  createReport: vi.fn(),
  deleteReportComposition: vi.fn(),
  getReportComposerCatalog: vi.fn(),
  listReportCompositions: vi.fn(),
  previewReportComposition: vi.fn(),
  saveReportComposition: vi.fn(),
}));

const productionMetric = {
  id: "M-001", name: "实际产量", topic: "生产", definition: "已完工数量汇总",
  dimensions: ["产线"], unit: "件", version: 3, bindingVersion: 2,
  kinds: ["kpi", "trend", "rank"], sourceTables: ["public.production_records"],
};
const qualityMetric = {
  id: "M-002", name: "工序良率", topic: "质量", definition: "合格数除以报工数",
  dimensions: ["工序"], unit: "%", version: 1, bindingVersion: 1,
  kinds: ["kpi", "trend"], sourceTables: ["public.quality_records"],
};

const catalogFixture = {
  source: { id: 1, name: "制造数据底座", type: "mysql" },
  metrics: [productionMetric, qualityMetric],
  tables: [
    { qualifiedName: "public.production_records", name: "production_records", cnName: "生产报工", topic: "生产", fields: [{ name: "qty", cnName: "数量" }], metricIds: ["M-001"], dataTo: "2026-09-12" },
    { qualifiedName: "public.quality_records", name: "quality_records", cnName: "质检记录", topic: "质量", fields: [], metricIds: ["M-002"], dataTo: "2026-09-12" },
  ],
  relations: [],
  starters: [
    { id: "starter-blank", name: "空白周报", description: "从指标关系图逐项添加内容。", blocks: [] },
    { id: "starter-综合", name: "综合起始模板", description: "生产、质量、设备与库存可以自由混搭", blocks: [{ id: "block-1-m-001", kind: "kpi", title: "实际产量", metricId: "M-001", dimension: null, grain: "day", chart: "number", width: "half", height: "compact", accent: "default", compare: true, topN: 12 }] },
  ],
  suggestedRange: { start: "2026-09-06", end: "2026-09-12" },
};

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/workspace/reports/composer"]}>
      <ReportComposerPage />
    </MemoryRouter>,
  );
}

describe("ReportComposerPage", () => {
  beforeEach(() => {
    vi.mocked(listSources).mockResolvedValue([{ id: 1, name: "制造数据底座", isDefault: true }]);
    vi.mocked(getReportComposerCatalog).mockResolvedValue(catalogFixture);
    vi.mocked(listReportCompositions).mockResolvedValue([]);
    vi.mocked(saveReportComposition).mockResolvedValue({ id: "tpl-1", version: 1, composition: catalogFixture.starters[1] });
    vi.mocked(deleteReportComposition).mockResolvedValue(undefined);
    vi.mocked(previewReportComposition).mockResolvedValue({ range: { start: "2026-09-06", end: "2026-09-12" }, items: [] });
  });

  test("loads the 综合 starter into a full workbench with governed metrics", async () => {
    renderPage();
    expect(await screen.findByRole("button", { name: "编辑实际产量" })).toBeTruthy();
    expect(screen.getAllByText("工序良率").length).toBeGreaterThan(0);
    expect(screen.getByLabelText("周报名称").value).toBe("综合周报");
    expect(screen.getByText("1/24 项内容")).toBeTruthy();
    expect(screen.getByRole("button", { name: "生成周报" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "数据关系图" })).toBeTruthy();
  });

  test("searches metrics and adds a governed block to the canvas", async () => {
    renderPage();
    await screen.findByLabelText("搜索指标");
    fireEvent.change(screen.getByLabelText("搜索指标"), { target: { value: "良率" } });
    expect(screen.queryByText("已完工数量汇总")).toBeNull();
    expect(screen.getByText("合格数除以报工数")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "添加工序良率到周报" }));
    fireEvent.click(screen.getByRole("button", { name: /指标概览/ }));
    await waitFor(() => expect(screen.getByText("2/24 项内容")).toBeTruthy());
    expect(screen.getByRole("button", { name: "编辑工序良率" })).toBeTruthy();
    expect(screen.getByText("未保存更改")).toBeTruthy();
  });

  test("selecting a block opens its inspector and editing the title marks the draft dirty", async () => {
    renderPage();
    const block = await screen.findByRole("button", { name: "编辑实际产量" });
    fireEvent.click(block);
    expect(await screen.findByRole("heading", { name: "内容属性" })).toBeTruthy();
    fireEvent.change(screen.getByLabelText("显示标题"), { target: { value: "本周实际产量" } });
    expect(screen.getByRole("button", { name: "编辑本周实际产量" })).toBeTruthy();
    expect(screen.getByText("未保存更改")).toBeTruthy();
  });

  test("report overview lists structure checks before a block is selected", async () => {
    renderPage();
    await screen.findByRole("heading", { name: "报告概览" });
    expect(screen.getByText("结构检查")).toBeTruthy();
    expect(screen.getByText("至少一个趋势分析")).toBeTruthy();
    expect(screen.getByText("主题覆盖")).toBeTruthy();
  });

  test("calendar lets a user pick a start and end day to set the period", async () => {
    renderPage();
    await screen.findByLabelText("搜索指标");
    fireEvent.click(screen.getByRole("button", { name: "统计周期" }));
    expect(screen.getByRole("button", { name: "数据可覆盖期" })).toBeTruthy();
    const days = screen.getAllByRole("button", { name: /^20\d\d-\d\d-\d\d$/ });
    expect(days.length).toBeGreaterThan(20);
    const start = days.find((cell) => cell.getAttribute("aria-label").endsWith("-10"));
    const end = days.find((cell) => cell.getAttribute("aria-label").endsWith("-12"));
    fireEvent.click(start);
    fireEvent.click(end);
    await waitFor(() => expect(screen.getByRole("button", { name: "统计周期" }).textContent).toMatch(/10 ~ .*12/));
  });

  test("card grid reflects block width and toggles it from the hover action", async () => {
    renderPage();
    const card = await screen.findByRole("button", { name: "编辑实际产量" });
    expect(card.className).not.toContain("sm:col-span-2");
    fireEvent.click(screen.getByRole("button", { name: "改为通栏" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "编辑实际产量" }).className).toContain("sm:col-span-2"));
    expect(screen.getByText("未保存更改")).toBeTruthy();
  });

  test("preview guard explains the missing content before calling the gateway", async () => {
    renderPage();
    await screen.findByLabelText("搜索指标");
    fireEvent.click(screen.getByRole("button", { name: "模板" }));
    fireEvent.click(screen.getByRole("button", { name: /空白周报/ }));
    await waitFor(() => expect(screen.getByText("0/24 项内容")).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: "预览" }));
    const notice = await screen.findByRole("status");
    expect(notice.textContent).toContain("先从左侧指标库添加至少一个内容");
    expect(previewReportComposition).not.toHaveBeenCalled();
  });

  test("preview renders a data-bar table and a two-period trend overlay", async () => {
    const tableMetric = { id: "M-001", name: "不良数量", unit: "件", version: 1, bindingVersion: 1 };
    const trendMetric = { id: "M-001", name: "实际产量", unit: "件", version: 3, bindingVersion: 2 };
    vi.mocked(previewReportComposition).mockResolvedValue({
      range: { start: "2026-07-09", end: "2026-07-15" },
      items: [
        {
          state: "ready", metric: tableMetric, columns: ["缺陷类型", "不良数量"],
          rows: [["尺寸", 70], ["外观", 30]],
          block: { id: "b-table", kind: "breakdown", chart: "table", dimension: "缺陷类型", title: "缺陷明细", width: "full", height: "standard", accent: "default", topN: 12 },
        },
        {
          state: "ready", metric: trendMetric, columns: ["周", "实际产量"],
          rows: [["2026-28", 600], ["2026-29", 700]], previousRows: [["2026-26", 500], ["2026-27", 550]],
          block: { id: "b-trend", kind: "trend", chart: "line", grain: "week", compare: true, title: "产量趋势", width: "full", height: "standard", accent: "default" },
        },
      ],
    });
    renderPage();
    await screen.findByLabelText("搜索指标");
    fireEvent.click(screen.getByRole("button", { name: "预览", exact: true }));
    expect(await screen.findByText("缺陷明细")).toBeTruthy();
    expect(screen.getByText("占比")).toBeTruthy();
    expect(screen.getByText("累计")).toBeTruthy();
    expect(screen.getByText("上一周期")).toBeTruthy();
  });
});
