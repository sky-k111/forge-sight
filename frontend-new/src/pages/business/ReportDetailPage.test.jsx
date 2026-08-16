import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, test, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import ReportDetailPage from "./ReportDetailPage";
import { getReport } from "../../api/reports";

vi.mock("../../api/reports", () => ({
  approveReport: vi.fn(),
  downloadReportFile: vi.fn(),
  getReport: vi.fn(),
  publishReport: vi.fn(),
  submitReportReview: vi.fn(),
}));

const report = {
  id: "rpt-1",
  title: "华东制造周报",
  sourceName: "华东制造数据底座",
  status: "published",
  statusLabel: "已发布",
  periodStart: "2026-08-03T00:00:00+08:00",
  periodEnd: "2026-08-09T23:59:59+08:00",
  version: { id: "ver-1", version: 1, generatedAt: "2026-08-10T10:00:00+08:00" },
  files: [],
  sections: [],
  content: {
    schemaVersion: "weekly-report/v1",
    managementSummary: {
      findings: [{ level: "confirmed", text: "产量较上周提升", evidenceStatus: "verified" }],
      actions: [],
    },
    metrics: [{ key: "output", name: "产量", value: 1200, previousValue: 1100, changeRate: 0.09, trend: "up", unit: "件" }],
    charts: [{
      key: "output-trend",
      type: "line",
      title: "产量趋势",
      description: "本周每日产量变化。",
      unit: "件",
      categoryLabel: "日期",
      valueLabel: "产量",
      points: [{ label: "周一", value: 150 }, { label: "周二", value: 180 }],
      evidenceArtifactIds: ["artifact-1"],
    }],
    anomalies: [],
    progress: [],
    recommendations: [],
    dataQuality: { coverage: 100, delayedSources: [], missingMetrics: [], failedSections: [] },
    appendix: { sources: ["MES"], definitions: [] },
  },
};

describe("ReportDetailPage", () => {
  beforeEach(() => {
    vi.mocked(getReport).mockResolvedValue(report);
  });

  test("keeps the report focused on evidence instead of a decorative topic illustration", async () => {
    render(
      <MemoryRouter initialEntries={["/workspace/reports/rpt-1"]}>
        <Routes>
          <Route path="/workspace/reports/:reportId" element={<ReportDetailPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByRole("heading", { name: "一眼看清本周变化" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "产量趋势" })).toBeTruthy();
    expect(screen.queryByText("主题示意图，非数据证据")).toBeNull();
    expect(screen.queryByRole("img", { name: /主题示意图/ })).toBeNull();
  });

  test("prioritizes the management summary and limits the KPI rail to four metrics", async () => {
    vi.mocked(getReport).mockResolvedValue({
      ...report,
      content: {
        ...report.content,
        managementSummary: {
          findings: report.content.managementSummary.findings,
          actions: [{ text: "复核二线排产节奏", priority: "P1", evidenceStatus: "verified" }],
        },
        metrics: [
          ...report.content.metrics,
          { key: "quality", name: "一次合格率", value: 98.2, previousValue: 97.8, changeRate: 0.004, trend: "up", unit: "%" },
          { key: "delivery", name: "准时交付率", value: 96.4, previousValue: 97.1, changeRate: -0.007, trend: "down", unit: "%" },
          { key: "inventory", name: "库存周转", value: 7.2, previousValue: 7, changeRate: 0.03, trend: "up", unit: "天" },
          { key: "utilization", name: "设备稼动率", value: 86, previousValue: 84, changeRate: 0.02, trend: "up", unit: "%" },
        ],
      },
    });

    render(
      <MemoryRouter initialEntries={["/workspace/reports/rpt-1"]}>
        <Routes>
          <Route path="/workspace/reports/:reportId" element={<ReportDetailPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByRole("heading", { name: "管理摘要" })).toBeTruthy();
    expect(screen.getByText("首要行动")).toBeTruthy();
    expect(screen.getByText("数据新鲜度")).toBeTruthy();
    expect(screen.getByText("库存周转")).toBeTruthy();
    expect(screen.queryByText("设备稼动率")).toBeNull();
  });

  test("does not expose markdown markers in legacy report summaries", async () => {
    vi.mocked(getReport).mockResolvedValue({
      ...report,
      content: {},
      title: "**旧版**周报",
      summary: "**重点结论**：良率提升。",
      sections: [{ id: "section-1", order: 1, title: "**质量**", summary: "**没有异常**", statusLabel: "已完成", content: {} }],
    });
    render(<MemoryRouter initialEntries={["/workspace/reports/rpt-1"]}><Routes><Route path="/workspace/reports/:reportId" element={<ReportDetailPage />} /></Routes></MemoryRouter>);

    expect(await screen.findByText("重点结论：良率提升。")).toBeTruthy();
    expect(screen.getByText("没有异常")).toBeTruthy();
    expect(screen.queryByText(/\*\*/)).toBeNull();
  });
});
