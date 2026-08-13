import { beforeEach, describe, expect, test, vi } from "vitest";

import {
  changeEmail,
  login,
  register,
  requestEmailChallenge,
  verifyEmail,
} from "./auth";
import { httpPost } from "./_http";
import { setSession } from "../lib/auth";


vi.mock("./_http", () => ({
  httpGet: vi.fn(),
  httpPost: vi.fn(),
}));

vi.mock("../lib/auth", () => ({
  clearSession: vi.fn(),
  setSession: vi.fn(),
}));


describe("email identity API contract", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  test("preserves restricted login state and next action", async () => {
    httpPost.mockResolvedValue({
      token: "restricted-token",
      user: { username: "demo", emailStatus: "unbound", emailMasked: null },
      nextAction: "bind_email",
    });

    const result = await login("demo", "pass1234");

    expect(result.nextAction).toBe("bind_email");
    expect(setSession).toHaveBeenCalledWith("restricted-token", result.user);
  });

  test("includes email when registering", async () => {
    httpPost.mockResolvedValue({
      token: "restricted-token",
      user: { username: "demo", emailStatus: "pending_verification" },
      nextAction: "verify_email",
    });

    await register({
      username: "demo",
      password: "pass1234",
      displayName: "演示用户",
      email: "demo@example.com",
    });

    expect(httpPost).toHaveBeenCalledWith("/api/auth/register", {
      username: "demo",
      password: "pass1234",
      displayName: "演示用户",
      email: "demo@example.com",
    });
  });

  test("uses the challenge, change, and verify endpoints", async () => {
    httpPost
      .mockResolvedValueOnce({ ok: true, purpose: "bind" })
      .mockResolvedValueOnce({ ok: true, purpose: "change" })
      .mockResolvedValueOnce({
        token: "full-token",
        user: { username: "demo", emailStatus: "verified" },
        nextAction: null,
      });

    await requestEmailChallenge("demo@example.com");
    await changeEmail("next@example.com");
    const verified = await verifyEmail("next@example.com", "012345");

    expect(httpPost).toHaveBeenNthCalledWith(1, "/api/auth/email/challenges", {
      email: "demo@example.com",
    });
    expect(httpPost).toHaveBeenNthCalledWith(2, "/api/auth/email/change", {
      email: "next@example.com",
    });
    expect(httpPost).toHaveBeenNthCalledWith(3, "/api/auth/email/verify", {
      email: "next@example.com",
      code: "012345",
    });
    expect(setSession).toHaveBeenCalledWith("full-token", verified.user);
  });
});
