import { describe, expect, test } from "vitest";

import { buildReportView, mergeTrendSeries, reportText } from "./weeklyReportView";

describe("structured weekly report view", () => {
  test("returns a safe loading view before report data arrives", () => {
    expect(buildReportView(null)).toMatchObject({
      headline: "本周期尚未形成总体结论。",
      metrics: [],
      anomalies: [],
    });
  });

  test("prioritizes confirmed findings, critical anomalies, and metrics", () => {
    const view = buildReportView({
      summary: "旧摘要",
      content: {
        schemaVersion: "weekly-report/v1",
        managementSummary: { findings: [{ level: "confirmed", text: "本周良率下降", evidenceStatus: "verified" }], actions: [] },
        metrics: [{ key: "yield", name: "良率", value: 96.2, previousValue: 98, changeRate: -0.0184, trend: "down", unit: "%" }],
        trends: [{ metricKey: "yield", label: "良率", unit: "%", points: [{ label: "周一", value: 98 }, { label: "周二", value: 96.2 }] }],
        anomalies: [{ title: "焊接波动", description: "焊接工序下降", severity: "critical", confidence: 92 }],
        progress: [], recommendations: [], dataQuality: { coverage: 96, delayedSources: [], missingMetrics: [], temporaryMetrics: [], failedSections: [] }, appendix: { sources: ["MES"], definitions: [] },
      },
    });

    expect(view.headline).toBe("本周良率下降");
    expect(view.topAnomaly.title).toBe("焊接波动");
    expect(view.metrics[0].changeLabel).toBe("-1.84%");
    expect(view.quality.coverage).toBe(96);
  });

  test("removes markdown decoration from report copy before it reaches the reading view", () => {
    const view = buildReportView({
      summary: "**本周重点**：良率改善",
      content: {
        schemaVersion: "weekly-report/v1",
        managementSummary: { findings: [{ level: "confirmed", text: "**良率**改善", evidenceStatus: "verified" }] },
        metrics: [], anomalies: [], progress: [], recommendations: [], dataQuality: {}, appendix: {},
      },
    });

    expect(view.headline).toBe("良率改善");
    expect(view.findings[0].text).toBe("良率改善");
  });

  test("hides physical data identifiers from user-facing report copy", () => {
    const text = reportText("请检查 `mes_process_output` 的 stat_date 字段");
    expect(text).not.toContain("mes_process_output");
    expect(text).not.toContain("stat_date");
  });

  test("merges trend series by label without losing sparse points", () => {
    expect(mergeTrendSeries([
      { metricKey: "a", points: [{ label: "周一", value: 2 }] },
      { metricKey: "b", points: [{ label: "周二", value: 4 }] },
    ])).toEqual([{ label: "周一", a: 2 }, { label: "周二", b: 4 }]);
  });

  test("uses controlled chart specs and falls back to one chart per legacy trend", () => {
    const controlled = buildReportView({
      content: {
        schemaVersion: "weekly-report/v1",
        charts: [{ key: "quality", type: "pareto", title: "缺陷 Pareto", points: [] }],
      },
    });
    const legacy = buildReportView({
      content: {
        schemaVersion: "weekly-report/v1",
        trends: [
          { metricKey: "output", label: "产量趋势", unit: "件", points: [{ label: "W1", value: 10 }, { label: "W2", value: 12 }] },
          { metricKey: "defect", label: "缺陷分布", unit: "件", points: [{ label: "划伤", value: 4 }, { label: "脏污", value: 2 }] },
        ],
      },
    });

    expect(controlled.charts[0].type).toBe("pareto");
    expect(legacy.charts).toHaveLength(2);
    expect(legacy.charts.map((chart) => chart.type)).toEqual(["line", "bar"]);
    expect(legacy.charts[1].points[0]).toEqual({ label: "划伤", value: 4 });
  });
});
