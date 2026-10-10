import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, expect, test, vi } from "vitest";

vi.mock("../../api/auth", () => ({
  getDingTalkBinding: vi.fn(),
  getDingTalkBindingSources: vi.fn(),
  getFeishuBinding: vi.fn(),
  getFeishuBindingSources: vi.fn(),
  fetchMe: vi.fn(),
  login: vi.fn(),
  register: vi.fn(),
  selectDingTalkBindingSource: vi.fn(),
  selectFeishuBindingSource: vi.fn(),
  startDingTalkBind: vi.fn(),
  startFeishuBind: vi.fn(),
}));

import LoginPage from "./LoginPage";
import {
  fetchMe,
  getDingTalkBinding,
  getDingTalkBindingSources,
  getFeishuBinding,
  getFeishuBindingSources,
  selectFeishuBindingSource,
} from "../../api/auth";
import { setPendingRegistrationBinding, setSession } from "../../lib/auth";
import { getActiveTenantId, setActiveTenantId } from "../../lib/tenant";

const privateTenant = { id: "tenant-private", name: "个人空间", isDefault: true };
const demoTenant = { id: "tenant-default", name: "演示企业", isDefault: false };
const user = {
  username: "cyk",
  role: "business",
  emailStatus: "pending_verification",
  emailVerificationRequired: false,
  externalIdentityBound: true,
  defaultTenantId: privateTenant.id,
  tenants: [privateTenant, demoTenant],
};

function mount() {
  render(
    <MemoryRouter initialEntries={["/login"]}>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/workspace/overview" element={<div>业务工作台</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  setSession("test-token", user);
  setActiveTenantId(privateTenant.id);
  setPendingRegistrationBinding("feishu");
  fetchMe.mockResolvedValue(user);
  getDingTalkBinding.mockResolvedValue({ bound: false });
  getDingTalkBindingSources.mockResolvedValue({ items: [] });
  getFeishuBinding.mockResolvedValue({ bound: true, displayName: "kkk", sourceId: null });
  getFeishuBindingSources.mockImplementation(async () => ({
    items: getActiveTenantId() === demoTenant.id ? [{ id: 1, name: "演示数据源" }] : [],
  }));
  selectFeishuBindingSource.mockResolvedValue({ sourceId: 1, sourceName: "演示数据源" });
});

test("selects an authorized tenant source after Feishu binding and finishes registration", async () => {
  mount();
  expect(await screen.findByText("已绑定：kkk")).toBeTruthy();
  fireEvent.change(screen.getByLabelText("使用哪个企业空间的数据源"), {
    target: { value: demoTenant.id },
  });
  const sourceSelect = await screen.findByLabelText("机器人使用的数据源（必选）");
  await waitFor(() => expect(sourceSelect.disabled).toBe(false));
  fireEvent.change(sourceSelect, { target: { value: "1" } });
  await waitFor(() => expect(selectFeishuBindingSource).toHaveBeenCalledWith(1));
  fireEvent.click(await screen.findByRole("button", { name: "完成注册" }));
  expect(await screen.findByText("业务工作台")).toBeTruthy();
});

test("allows registration with a bound account when its private tenant has no source", async () => {
  fetchMe.mockResolvedValue({ ...user, tenants: [privateTenant] });
  mount();
  expect(await screen.findByText(/当前企业空间还没有可用的数据源/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "完成注册" }));
  expect(await screen.findByText("业务工作台")).toBeTruthy();
});
