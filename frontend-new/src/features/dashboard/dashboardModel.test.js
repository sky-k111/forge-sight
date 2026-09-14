import { describe, expect, it } from "vitest";
import { addWidget, applyWidgetSizePreset, removeWidget, updateWidgetAppearance, updateWidgetLayouts } from "./dashboardModel";

const base = {
  version: 2,
  period: "last30",
  showContext: true,
  widgets: [
    { id: "a", kind: "recent", layout: { x: 0, y: 0, w: 4, h: 4, minW: 2, minH: 2 } },
  ],
};

describe("dashboard model", () => {
  it("updates layout without mutating saved state", () => {
    const next = updateWidgetLayouts(base, [{ i: "a", x: 3, y: 2, w: 5, h: 6 }]);
    expect(next.widgets[0].layout).toMatchObject({ x: 3, y: 2, w: 5, h: 6 });
    expect(base.widgets[0].layout.x).toBe(0);
  });

  it("adds unique governed widgets and supports removal", () => {
    const next = addWidget(base, { kind: "kpi", metricId: "M-001", title: "产量" });
    expect(next.widgets).toHaveLength(2);
    expect(next.widgets[1].id).toMatch(/^kpi-M-001/);
    expect(removeWidget(next, next.widgets[1].id)).toEqual(base);
  });

  it("restores a removed workspace widget once and gives it a useful default size", () => {
    const withoutRecent = removeWidget(base, "a");
    const restored = addWidget(withoutRecent, { kind: "recent", title: "近期调查" });
    expect(restored.widgets).toHaveLength(1);
    expect(restored.widgets[0]).toMatchObject({
      id: "recent-system",
      kind: "recent",
      layout: { w: 5, h: 6 },
    });
    expect(addWidget(restored, { kind: "recent", title: "近期调查" })).toBe(restored);
  });

  it("applies restrained appearance and size presets without mutating saved state", () => {
    const colored = updateWidgetAppearance(base, "a", { accent: "violet" });
    expect(colored.widgets[0].appearance).toEqual({ accent: "violet" });
    expect(base.widgets[0].appearance).toBeUndefined();

    const visualized = updateWidgetAppearance(colored, "a", { chartType: "table" });
    expect(visualized.widgets[0].appearance).toEqual({ accent: "violet", chartType: "table" });

    const emphasized = applyWidgetSizePreset(visualized, "a", "emphasis");
    expect(emphasized.widgets[0].layout).toMatchObject({ x: 0, w: 12, h: 7 });
  });
});
