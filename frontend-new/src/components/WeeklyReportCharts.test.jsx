import { render, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";

import { WeeklyReportCharts } from "./WeeklyReportCharts";

const base = {
  description: "由 Agent 根据数据语义自动选择。",
  unit: "件",
  categoryLabel: "类别",
  valueLabel: "数量",
  evidenceArtifactIds: ["artifact-1"],
  colorRole: "quality",
};

describe("weekly report chart orchestration", () => {
  test("renders separate accessible visual blocks for different chart semantics", () => {
    render(
      <WeeklyReportCharts
        sourceName="生产数据库"
        charts={[
          { ...base, key: "rank", type: "bar", title: "产线排行", points: [{ label: "二线", value: 12 }, { label: "一线", value: 10 }] },
          { ...base, key: "pareto", type: "pareto", title: "缺陷 Pareto", points: [{ label: "划伤", value: 6, secondaryValue: 60 }, { label: "脏污", value: 4, secondaryValue: 100 }] },
          { ...base, key: "share", type: "donut", title: "产量构成", points: [{ label: "A", value: 7 }, { label: "B", value: 3 }] },
          { ...base, key: "relation", type: "scatter", title: "运行与停机关系", secondaryValueLabel: "停机时长", secondaryUnit: "分钟", points: [{ label: "A01", value: 120, secondaryValue: 8 }, { label: "A02", value: 150, secondaryValue: 4 }] },
        ]}
      />,
    );

    expect(screen.getByRole("heading", { name: "产线排行" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "缺陷 Pareto" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "产量构成" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "运行与停机关系" })).toBeTruthy();
    expect(screen.getByText("横向排行")).toBeTruthy();
    expect(screen.getByText("帕累托分析")).toBeTruthy();
    expect(screen.getByText("构成占比")).toBeTruthy();
    expect(screen.getByText("相关性散点")).toBeTruthy();
    expect(screen.getAllByText("查看精确数据")).toHaveLength(4);
    expect(screen.getAllByText(/证据 1 项/)).toHaveLength(4);
    expect(screen.getByText("停机时长（分钟）")).toBeTruthy();
  });

  test("renders a data-bar table without the redundant exact-data table", () => {
    render(
      <WeeklyReportCharts
        sourceName="生产数据库"
        charts={[
          { ...base, key: "defect", type: "table", title: "缺陷明细", categoryLabel: "缺陷类型", points: [{ label: "尺寸", value: 70 }, { label: "外观", value: 30 }] },
        ]}
      />,
    );
    expect(screen.getByText("明细数据表")).toBeTruthy();
    expect(screen.getByText("尺寸")).toBeTruthy();
    expect(screen.getByText("占比")).toBeTruthy();
    expect(screen.getByText("累计")).toBeTruthy();
    expect(screen.queryByText("查看精确数据")).toBeNull();
  });

  test("renders a calendar heatmap and a two-period trend line", () => {
    render(
      <WeeklyReportCharts
        sourceName="生产数据库"
        charts={[
          { ...base, key: "cal", type: "calendar", title: "日产量日历", categoryLabel: "日期", points: [
            { label: "2026-06-01", value: 100 }, { label: "2026-06-02", value: 120 }, { label: "2026-06-03", value: 90 },
          ] },
          { ...base, key: "cmp", type: "line", title: "产量趋势", categoryLabel: "周", comparisonLabel: "上一周期", points: [
            { label: "2026-23", value: 120, previousValue: 90 }, { label: "2026-24", value: 135, previousValue: 100 },
          ] },
        ]}
      />,
    );
    expect(screen.getByText("日历热力")).toBeTruthy();
    expect(screen.getAllByText("日").length).toBeGreaterThan(0);
    expect(screen.getByText("低")).toBeTruthy();
    expect(screen.getByText("高")).toBeTruthy();
    expect(screen.getByText("上一周期")).toBeTruthy();
  });
});
