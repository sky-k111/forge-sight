import { beforeEach, expect, test, vi } from "vitest";

import { httpGet } from "./_http";


vi.mock("../lib/auth", () => ({
  AUTH_EXPIRED_EVENT: "forgesight:auth-expired",
  clearSession: vi.fn(),
  getToken: vi.fn(() => "restricted-token"),
}));


beforeEach(() => {
  vi.unstubAllGlobals();
  window.localStorage.clear();
});


test("preserves backend email gate error code for restricted-state routing", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: false,
      status: 403,
      json: async () => ({
        detail: "请先完成邮箱验证",
        errorCode: "EMAIL_VERIFICATION_REQUIRED",
      }),
    }),
  );

  await expect(httpGet("/api/reports")).rejects.toMatchObject({
    message: "请先完成邮箱验证",
    code: "EMAIL_VERIFICATION_REQUIRED",
    status: 403,
  });
});

test("sends the active enterprise boundary with every API request", async () => {
  window.localStorage.setItem("forgesight:active-tenant-id", "tenant-factory-a");
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({ ok: true }),
  });
  vi.stubGlobal("fetch", fetchMock);

  await httpGet("/api/datasources");

  expect(fetchMock.mock.calls[0][1].headers).toMatchObject({
    Authorization: "Bearer restricted-token",
    "X-Tenant-ID": "tenant-factory-a",
  });
});
