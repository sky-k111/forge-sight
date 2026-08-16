import { describe, expect, test } from "vitest";

import { mergeInboxWithGeneratingReports } from "./MessagesPage";

describe("message inbox report generation state", () => {
  test("adds an unopened generating weekly report before published messages", () => {
    const items = mergeInboxWithGeneratingReports(
      [{ id: "msg-1", title: "已发布公告", publishedAt: "2026-08-10T10:00:00" }],
      [{ id: "rpt-1", title: "经营周报", status: "queued", createdAt: "2026-08-11T10:00:00" }],
    );

    expect(items[0]).toMatchObject({ id: "generating-report-rpt-1", reportId: "rpt-1", isGenerating: true });
    expect(items[0].summaryLines[0]).toContain("暂不可打开");
  });

  test("does not duplicate a report after it has been published", () => {
    expect(mergeInboxWithGeneratingReports([], [{ id: "rpt-1", status: "published" }])).toEqual([]);
  });
});
