import { describe, expect, it } from "vitest";

import { filterReportRows, reportListMeta } from "./reportListFilters";

const report = {
  id: "r1",
  title: "华东制造周报",
  reportType: "weekly",
  status: "published",
  periodStart: "2026-08-03T00:00:00",
  periodEnd: "2026-08-09T23:59:59",
  scope: { audience: { organizationIds: ["org-east"] } },
  isRead: false,
  reportOverview: {
    managementSummary: { findings: [{ text: "本周产量稳定。", level: "confirmed" }] },
    anomalies: [{ title: "焊接良率下降", severity: "high" }],
    dataQuality: { coverage: 97 },
  },
};

describe("report list filters", () => {
  it("derives the decision summary shown on a report row", () => {
    expect(reportListMeta(report)).toMatchObject({
      headline: "本周产量稳定。",
      highestSeverity: "high",
      anomalyCount: 1,
      coverage: 97,
      scopeLabel: "org-east",
    });
  });

  it("combines period, report type, scope, severity and read filters", () => {
    expect(filterReportRows([report], {
      periodFrom: "2026-08-01",
      periodTo: "2026-08-31",
      reportType: "weekly",
      scope: "org-east",
      severity: "high",
      read: "unread",
    })).toEqual([report]);
    expect(filterReportRows([report], { severity: "critical" })).toEqual([]);
    expect(filterReportRows([report], { read: "read" })).toEqual([]);
  });
});
