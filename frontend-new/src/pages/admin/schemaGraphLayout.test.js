import { describe, expect, test } from "vitest";
import { buildSchemaLinkPath, getSchemaNodeStyle } from "./schemaGraphLayout";

describe("schemaGraphLayout", () => {
  test("builds resource graph links from node edge to node edge instead of hardcoded loose coordinates", () => {
    const path = buildSchemaLinkPath(
      { x: 18, y: 82 },
      { x: 246, y: 26 },
    );

    expect(path.startsWith("M178 112")).toBe(true);
    expect(path.endsWith("246 56")).toBe(true);
    expect(path).toContain("C");
  });

  test("maps resource graph node layout into the shared responsive canvas percentages", () => {
    const style = getSchemaNodeStyle({ x: 246, y: 26 });

    expect(style.left).toBe("26.74%");
    expect(style.top).toBe("10.61%");
    expect(style.width).toBe("17.39%");
    expect(style.height).toBe("24.49%");
  });
});
