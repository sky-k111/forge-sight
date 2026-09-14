import { describe, expect, test, vi } from "vitest";

import { addMetricBlock, fromStarter, moveBlock, previewSeries } from "./reportComposerModel";

describe("reportComposerModel", () => {
  test("keeps starter blocks editable without mutating the catalog", () => {
    const starter = { id: "starter-production", name: "生产起始模板", blocks: [{ id: "a", title: "产量" }] };
    const composition = fromStarter(starter);
    composition.blocks[0].title = "新标题";
    expect(starter.blocks[0].title).toBe("产量");
  });

  test("adds a governed dimension block and supports reordering", () => {
    vi.spyOn(Date, "now").mockReturnValue(1);
    const metric = { id: "M-001", name: "产量" };
    let composition = addMetricBlock(fromStarter({ id: "starter-blank", blocks: [] }), metric, "breakdown", "产线");
    composition = addMetricBlock(composition, metric, "trend");
    expect(composition.blocks[0]).toMatchObject({ kind: "breakdown", dimension: "产线", chart: "bar" });
    expect(moveBlock(composition, 1, 0).blocks[0].kind).toBe("trend");
  });

  test("adapts preview rows for chart components", () => {
    expect(previewSeries({ block: { kind: "trend" }, columns: ["周", "产量"], rows: [["W1", 12], ["W2", 15]] })).toEqual([
      { label: "W1", value: 12 }, { label: "W2", value: 15 },
    ]);
  });
});
