import { describe, expect, test } from "vitest";
import { buildTracePath, getTraceFitTransform } from "./traceGraphLayout";

describe("traceGraphLayout", () => {
  test("builds a path string from node coordinates instead of hardcoded svg data", () => {
    const path = buildTracePath([[160, 26], [160, 82], [105, 155], [105, 218], [160, 370]]);

    expect(path.startsWith("M160 26")).toBe(true);
    expect(path).toContain("105 155");
    expect(path).toContain("160 370");
    expect(path).toContain("C");
  });

  test("computes a fit transform that scales and recenters the graph inside the canvas", () => {
    const transform = getTraceFitTransform({
      positions: [[160, 26], [160, 82], [105, 155], [222, 155], [105, 218], [190, 270], [160, 370]],
      viewportWidth: 320,
      viewportHeight: 430,
    });

    expect(transform.scale).not.toBe(1);
    expect(Number.isFinite(transform.x)).toBe(true);
    expect(Number.isFinite(transform.y)).toBe(true);
  });
});
