import { render, screen } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";
import { Line, ReferenceLine } from "recharts";
import MonitoringEvidenceChart from "./MonitoringEvidenceChart";

vi.mock("recharts", () => ({
  ResponsiveContainer: ({ children }) => <div>{children}</div>,
  LineChart: ({ children }) => <div>{children}</div>,
  Line: vi.fn(() => null),
  ReferenceLine: vi.fn(() => null),
  CartesianGrid: () => null,
  XAxis: () => null,
  YAxis: () => null,
  Tooltip: () => null,
}));
beforeEach(() => vi.clearAllMocks());

test("draws genuine zero thresholds and never connects missing evidence", () => {
  render(
    <MonitoringEvidenceChart
      items={[
        { logicalPeriod: "2026-08-01", currentValue: -3, baselineValue: null },
        { logicalPeriod: "2026-08-02", currentValue: null, baselineValue: -1 },
      ]}
      rule={{ condition: { openThreshold: 0, closeThreshold: 2 } }}
    />
  );
  expect(screen.getByRole("img", { name: "异常指标历史趋势" })).toBeTruthy();
  expect(vi.mocked(ReferenceLine).mock.calls.map(([props]) => props.y)).toEqual([0, 2]);
  expect(
    vi
      .mocked(Line)
      .mock.calls.every(([props]) => props.connectNulls === false && props.type === "linear")
  ).toBe(true);
});
test("does not invent dynamic control bounds from a baseline center", () => {
  render(
    <MonitoringEvidenceChart
      items={[{ logicalPeriod: "2026-08-01", currentValue: 0.82, baselineValue: 0.95 }]}
      rule={{ condition: { baselineMethod: "median_mad", openThreshold: 0.9 } }}
    />
  );
  expect(ReferenceLine).not.toHaveBeenCalled();
  expect(screen.getByText(/控制上下界未由此接口提供/)).toBeTruthy();
});
test("shows missing numeric evidence as empty instead of drawing a zero chart", () => {
  render(
    <MonitoringEvidenceChart
      items={[{ logicalPeriod: "2026-08-01", currentValue: null, baselineValue: null }]}
    />
  );
  expect(screen.getByRole("status").textContent).toContain("缺失值不会按 0");
  expect(Line).not.toHaveBeenCalled();
});
