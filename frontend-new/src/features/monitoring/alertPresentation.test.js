import { describe, expect, test } from "vitest";
import {
  evidenceRows,
  filterAlerts,
  formatNumber,
  formatTime,
  recoveryCondition,
  ruleCondition,
  summarizeAlerts,
} from "./alertPresentation";

describe("monitoring presentation", () => {
  test("preserves zero, null, negative values and invalid dates", () => {
    expect(formatNumber(0)).toBe("0");
    expect(formatNumber(null)).toBe("—");
    expect(formatNumber(-3.5)).toBe("-3.5");
    expect(formatNumber(0.00000001)).toBe("0.00000001");
    expect(formatNumber(false)).toBe("—");
    expect(formatNumber(" ")).toBe("—");
    expect(formatTime("not a date")).toBe("—");
  });
  test("counts affected objects only among unresolved alerts", () => {
    expect(
      summarizeAlerts([
        { status: "firing", sourceId: 1, entityId: "A" },
        { status: "recovering", sourceId: 1, entityId: "A" },
        { status: "resolved", sourceId: 1, entityId: "B" },
      ])
    ).toEqual({ firing: 1, recovering: 1, resolved: 1, affected: 1 });
  });
  test("combines filters and sorts by priority without comparing unlike metric values", () => {
    const rows = [
      { id: "a", title: "质量", metricName: "良率", status: "firing", severity: "high" },
      { id: "b", title: "质量", metricName: "良率", status: "firing", severity: "critical" },
      { id: "c", title: "产量", status: "resolved", severity: "high" },
    ];
    expect(
      filterAlerts(rows, { status: "firing", search: "良率", sort: "severity" }).map(
        (row) => row.id
      )
    ).toEqual(["b", "a"]);
  });
  test("keeps missing evidence as gaps and sorts observations chronologically", () => {
    const rows = evidenceRows([
      { id: "b", logicalPeriod: "2026-08-02", currentValue: null, baselineValue: -2 },
      { id: "a", logicalPeriod: "2026-08-01", currentValue: -3, baselineValue: null },
    ]);
    expect(rows.map((row) => row.id)).toEqual(["a", "b"]);
    expect(rows[1].currentValue).toBeNull();
    expect(rows[0].baselineValue).toBeNull();
  });
  test("explains fixed and dynamic rule contracts without inventing thresholds", () => {
    expect(
      ruleCondition({ condition: { operator: "lt", openThreshold: 0.9, pendingPeriods: 2 } })
    ).toContain("低于 0.9");
    expect(
      ruleCondition({ condition: { baselineMethod: "median_mad", pendingPeriods: 2 } })
    ).toContain("历史中位数");
    expect(ruleCondition(null)).toBe("未提供规则条件");
  });
  test("respects one-sided dynamic recovery instead of requiring both bounds", () => {
    expect(
      recoveryCondition({ condition: { baselineMethod: "shewhart", direction: "lower" } })
    ).toContain("不低于动态下界");
    expect(
      recoveryCondition({ condition: { baselineMethod: "shewhart", direction: "upper" } })
    ).toContain("不高于动态上界");
    expect(recoveryCondition({ condition: { baselineMethod: "shewhart" } })).toContain(
      "回到控制界限内"
    );
  });
});
