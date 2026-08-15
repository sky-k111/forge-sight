import { describe, expect, it } from "vitest";

import { roleMenus } from "./navigation";
import { routeByPage } from "./routeConfig";

function messageItem(role) {
  return roleMenus[role].flatMap((group) => group.items).find((item) => item.label === "消息中心");
}

describe("role-aware message navigation", () => {
  it("keeps business and administrator inbox routes in their own workspaces", () => {
    expect(routeByPage[messageItem("business").id]).toBe("/workspace/messages");
    expect(routeByPage[messageItem("admin").id]).toBe("/admin/messages");
  });
});
