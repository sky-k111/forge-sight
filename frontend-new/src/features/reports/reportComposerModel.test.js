import { describe, expect, test, vi } from "vitest";

import {
  addMetricBlock,
  BLOCK_LIMIT,
  CHART_OPTIONS,
  filterMetrics,
  fromStarter,
  groupMetricsByTopic,
  moveBlock,
  previewSeries,
  structureChecklist,
  topicOf,
  usageCounts,
} from "./reportComposerModel";

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

  test("offers the data-bar table as a governed breakdown display", () => {
    expect(CHART_OPTIONS.breakdown.map(([value]) => value)).toContain("table");
    expect(CHART_OPTIONS.trend.map(([value]) => value)).not.toContain("table");
    expect(CHART_OPTIONS.trend.map(([value]) => value)).toContain("calendar");
    expect(CHART_OPTIONS.breakdown.map(([value]) => value)).not.toContain("calendar");
  });

  test("adapts preview rows for chart components", () => {
    expect(previewSeries({ block: { kind: "trend" }, columns: ["周", "产量"], rows: [["W1", 12], ["W2", 15]] })).toEqual([
      { label: "W1", value: 12 }, { label: "W2", value: 15 },
    ]);
  });

  test("groups and searches metrics by topic", () => {
    const metrics = [
      { id: "M-001", name: "实际产量", topic: "生产", unit: "件", definition: "完工数量", dimensions: ["产线"], kinds: ["kpi", "trend", "rank"] },
      { id: "M-002", name: "工序良率", topic: "质量", unit: "%", definition: "合格数除以总数", dimensions: ["工序"], kinds: ["kpi", "trend"] },
      { id: "M-003", name: "未知主题指标", topic: "能耗", unit: "", definition: "", dimensions: [], kinds: [] },
    ];
    expect(groupMetricsByTopic(metrics).map((group) => group.topic)).toEqual(["生产", "质量", "其他"]);
    expect(topicOf(metrics[2])).toBe("其他");
    expect(topicOf({ name: "非计划停机时长" })).toBe("设备");
    expect(topicOf({ name: "合格数量" })).toBe("质量");
    expect(topicOf({ name: "可用库存" })).toBe("库存");
    expect(topicOf({ name: "计划达成率" })).toBe("生产");
    expect(topicOf({ name: "抽检不良率" })).toBe("质量");
    expect(topicOf({ name: "神秘指标" })).toBe("其他");
    expect(filterMetrics(metrics, "良率").map((item) => item.id)).toEqual(["M-002"]);
    expect(filterMetrics(metrics, "  ").length).toBe(3);
  });

  test("tracks metric usage and structure completeness", () => {
    const metricById = { "M-001": { id: "M-001", topic: "生产" }, "M-002": { id: "M-002", topic: "质量" } };
    let composition = fromStarter({ id: "starter-blank", blocks: [] });
    composition = addMetricBlock(composition, { id: "M-001", name: "实际产量" }, "kpi");
    composition = addMetricBlock(composition, { id: "M-001", name: "实际产量" }, "trend");
    composition = addMetricBlock(composition, { id: "M-002", name: "工序良率" }, "trend");
    expect([...usageCounts(composition).entries()]).toEqual([["M-001", 2], ["M-002", 1]]);
    const checklist = structureChecklist(composition, metricById);
    expect(checklist.find((item) => item.id === "kpi").done).toBe(true);
    expect(checklist.find((item) => item.id === "breakdown").done).toBe(false);
    expect(checklist.find((item) => item.id === "topics").done).toBe(true);
  });

  test("refuses to add blocks beyond the governed limit", () => {
    const blocks = Array.from({ length: BLOCK_LIMIT }, (_, index) => ({ id: `b-${index}`, kind: "kpi", title: "x", metricId: "M-001" }));
    const composition = { ...fromStarter({ id: "starter-blank", blocks }), blocks };
    expect(addMetricBlock(composition, { id: "M-002", name: "良率" }, "kpi")).toBe(composition);
  });
});
