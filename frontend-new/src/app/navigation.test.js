import { describe, expect, it } from "vitest";

import { roleMenus } from "./navigation";
import { routeByPage } from "./routeConfig";

function flattenMenu(role) {
  return roleMenus[role].flatMap((group) => group.items.flatMap((item) => [item, ...(item.children ?? [])]));
}

describe("role-aware navigation", () => {
  it("organizes the business workspace around daily work and investigations", () => {
    const items = flattenMenu("business");
    expect(items.find((item) => item.id === "overview")?.label).toBe("今日工作台");
    expect(items.find((item) => item.id === "analysis")?.label).toBe("发起分析");
    expect(items.find((item) => item.id === "sessions")?.label).toBe("调查记录");
    expect(items.find((item) => item.id === "reports")?.label).toBe("报告与交接");
  });

  it("keeps notification routes available outside the primary sidebar", () => {
    expect(routeByPage.messages).toBe("/workspace/messages");
    expect(routeByPage["admin-messages"]).toBe("/admin/messages");
  });
});
