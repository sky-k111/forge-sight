import { describe, expect, it } from "vitest";

import { roleMenus } from "./navigation";
import { routeByPage } from "./routeConfig";

function flattenMenu(role) {
  return roleMenus[role].flatMap((group) => group.items.flatMap((item) => [item, ...(item.children ?? [])]));
}

describe("role-aware navigation", () => {
  it("organizes the business workspace around daily work and intelligent analysis", () => {
    const items = flattenMenu("business");
    expect(items.find((item) => item.id === "overview")?.label).toBe("今日工作台");
    expect(items.find((item) => item.id === "analysis")?.label).toBe("智能分析");
    expect(items.find((item) => item.id === "analysis")?.children).toBeUndefined();
    expect(items.find((item) => item.id === "modeling")?.label).toBe("智能建模");
    expect(items.find((item) => item.id === "modeling")?.children).toBeUndefined();
    expect(items.find((item) => item.id === "sessions")).toBeUndefined();
    expect(items.find((item) => item.id === "reports")?.label).toBe("报告与交接");
    expect(items.find((item) => item.id === "alerts")?.label).toBe("智能异常监测");
    expect(items.find((item) => item.id === "memory")).toBeUndefined();
  });

  it("keeps notification routes available outside the primary sidebar", () => {
    expect(routeByPage.messages).toBe("/workspace/messages");
    expect(routeByPage["admin-messages"]).toBe("/admin/messages");
    expect(routeByPage.memory).toBe("/workspace/memory");
    expect(routeByPage.profile).toBe("/workspace/profile");
    expect(routeByPage.alerts).toBe("/workspace/alerts");
    expect(routeByPage["monitoring-rules"]).toBe("/admin/monitoring-rules");
  });
});
