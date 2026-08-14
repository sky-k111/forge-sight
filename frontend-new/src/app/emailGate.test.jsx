import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, expect, test, vi } from "vitest";

vi.mock("../api/auth", () => ({
  fetchMe: vi.fn(),
}));

import { EmailGate } from "./AppRouter";
import { fetchMe } from "../api/auth";

beforeEach(() => window.localStorage.clear());

test("redirects an authenticated unverified user before rendering workspace content", async () => {
  window.localStorage.setItem("forgesight:auth-token", "restricted-token");
  window.localStorage.setItem(
    "forgesight:auth-user",
    JSON.stringify({
      username: "demo",
      role: "business",
      emailStatus: "pending_verification",
    }),
  );

  render(
    <MemoryRouter initialEntries={["/workspace/reports"]}>
      <Routes>
        <Route element={<EmailGate allowDemo={false} />}>
          <Route path="/workspace/reports" element={<div>敏感工作台</div>} />
        </Route>
        <Route path="/verify-email" element={<div>验证邮箱</div>} />
      </Routes>
    </MemoryRouter>,
  );

  expect(await screen.findByText("验证邮箱")).toBeTruthy();
  expect(screen.queryByText("敏感工作台")).toBeNull();
});

test("redirects an anonymous user to login when demo access is disabled", async () => {
  render(
    <MemoryRouter initialEntries={["/workspace/reports"]}>
      <Routes>
        <Route element={<EmailGate allowDemo={false} />}>
          <Route path="/workspace/reports" element={<div>敏感工作台</div>} />
        </Route>
        <Route path="/login" element={<div>登录页面</div>} />
      </Routes>
    </MemoryRouter>,
  );

  expect(await screen.findByText("登录页面")).toBeTruthy();
  expect(screen.queryByText("敏感工作台")).toBeNull();
});

test("redirects an anonymous user to login by default in development", async () => {
  render(
    <MemoryRouter initialEntries={["/workspace/reports"]}>
      <Routes>
        <Route element={<EmailGate />}>
          <Route path="/workspace/reports" element={<div>敏感工作台</div>} />
        </Route>
        <Route path="/login" element={<div>登录页面</div>} />
      </Routes>
    </MemoryRouter>,
  );

  expect(await screen.findByText("登录页面")).toBeTruthy();
  expect(screen.queryByText("敏感工作台")).toBeNull();
});

test("refreshes a legacy cached user before deciding email access", async () => {
  window.localStorage.setItem("forgesight:auth-token", "legacy-token");
  window.localStorage.setItem(
    "forgesight:auth-user",
    JSON.stringify({ username: "legacy", role: "business" }),
  );
  fetchMe.mockResolvedValueOnce({
    username: "legacy",
    role: "business",
    emailStatus: "pending_verification",
  });

  render(
    <MemoryRouter initialEntries={["/workspace/reports"]}>
      <Routes>
        <Route element={<EmailGate allowDemo={false} />}>
          <Route path="/workspace/reports" element={<div>敏感工作台</div>} />
        </Route>
        <Route path="/verify-email" element={<div>验证邮箱</div>} />
      </Routes>
    </MemoryRouter>,
  );

  expect(screen.getByText("正在确认邮箱状态…")).toBeTruthy();
  expect(await screen.findByText("验证邮箱")).toBeTruthy();
  expect(fetchMe).toHaveBeenCalledTimes(1);
  expect(screen.queryByText("敏感工作台")).toBeNull();
});
