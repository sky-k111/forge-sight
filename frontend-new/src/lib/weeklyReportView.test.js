import { describe, expect, test } from "vitest";

import { buildReportView, mergeTrendSeries } from "./weeklyReportView";

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

  test("merges trend series by label without losing sparse points", () => {
    expect(mergeTrendSeries([
      { metricKey: "a", points: [{ label: "周一", value: 2 }] },
      { metricKey: "b", points: [{ label: "周二", value: 4 }] },
    ])).toEqual([{ label: "周一", a: 2 }, { label: "周二", b: 4 }]);
  });
});
