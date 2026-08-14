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
        ]}
      />,
    );

    expect(screen.getByRole("heading", { name: "产线排行" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "缺陷 Pareto" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "产量构成" })).toBeTruthy();
    expect(screen.getByText("横向排行")).toBeTruthy();
    expect(screen.getByText("帕累托分析")).toBeTruthy();
    expect(screen.getByText("构成占比")).toBeTruthy();
    expect(screen.getAllByText("查看精确数据")).toHaveLength(3);
    expect(screen.getAllByText(/证据 1 项/)).toHaveLength(3);
  });
});
