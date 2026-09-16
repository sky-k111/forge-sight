import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, test, expect, vi } from "vitest";
import SettingsPage from "./SettingsPage";
import { getRuntimeConfiguration } from "../../api/security";

vi.mock("../../api/security", () => ({ getRuntimeConfiguration: vi.fn() }));
vi.mock("../../api/skills", () => ({ getSkillSettings: vi.fn().mockResolvedValue({ enabled: false }), updateSkillSettings: vi.fn() }));
vi.mock("../../api/agentSettings", () => ({ getPlannerSettings: vi.fn().mockResolvedValue({ enabled: false }), updatePlannerSettings: vi.fn() }));

beforeEach(() => getRuntimeConfiguration.mockReset());

test("shows the backend snapshot without fake switches or saves", async () => {
  getRuntimeConfiguration.mockResolvedValue({ dependencies: [{ name: "当前真实数据源", icon: "database", type: "MySQL", detail: "最近检查：未测试", status: "已登记", tone: "gray" }], policies: [{ title: "数据库只读", icon: "lock", description: "不可关闭", value: "强制" }], scope: "配置快照" });
  render(<SettingsPage />);
  await screen.findByText("当前真实数据源");
  expect(screen.queryByRole("button", { name: "保存策略" })).toBeNull();
  expect(screen.queryByRole("button", { name: "切换数据库只读" })).toBeNull();
  expect(screen.getByText("强制")).toBeTruthy();
});

test("runtime read failure remains explicit and retryable", async () => {
  getRuntimeConfiguration.mockRejectedValueOnce(new Error("暂时离线")).mockResolvedValue({ dependencies: [], policies: [], scope: "配置快照" });
  render(<SettingsPage />);
  expect((await screen.findByRole("alert")).textContent).toContain("暂时离线");
  fireEvent.click(screen.getByRole("button", { name: "刷新配置" }));
  await screen.findByText("配置快照");
  expect(screen.queryByRole("alert")).toBeNull();
});
