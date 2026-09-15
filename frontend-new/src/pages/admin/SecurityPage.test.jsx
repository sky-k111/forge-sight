import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, vi } from "vitest";

import { listAuditLogs, reviewAuditLog, reviewAuditLogsBatch } from "../../api/security";
import SecurityPage from "./SecurityPage";

vi.mock("../../api/security", () => ({
  listAuditLogs: vi.fn(),
  reviewAuditLog: vi.fn(),
  reviewAuditLogsBatch: vi.fn(),
}));

beforeEach(() => {
  listAuditLogs.mockReset();
  reviewAuditLog.mockReset();
  reviewAuditLogsBatch.mockReset();
});

const payload = (unreviewed) => ({
  items: [
    {
      id: 3, time: "09-15 10:04", at: "2026-09-15 10:04:12", user: "admin", role: "业务人员",
      action: "登录失败", resource: "admin", result: "已拦截", tone: "red",
      detail: "连续 5 次密码错误，触发登录保护拦截",
      reviewedAt: "", reviewedBy: "",
    },
    {
      id: 2, time: "09-15 09:00", at: "2026-09-15 09:00:03", user: "王敏", role: "业务人员",
      action: "查询工序良率", resource: "fact_quality", result: "允许", tone: "green",
      detail: "",
      reviewedAt: "", reviewedBy: "",
    },
    {
      id: 1, time: "09-14 18:00", at: "2026-09-14 18:00:41", user: "林涛", role: "管理员",
      action: "DELETE FROM dim_process", resource: "dim_process", result: "已拦截", tone: "red",
      detail: "只读校验失败：检测到 DELETE 语句",
      reviewedAt: "09-14 19:00", reviewedBy: "admin",
    },
  ],
  stats: { total: 6947, allowed: 6782, blocked: 29, recorded: 136, unreviewed },
  users: ["admin", "王敏", "林涛"],
});

test("renders real statistics and filter controls for the audit log", async () => {
  listAuditLogs.mockResolvedValue(payload(29));
  render(<SecurityPage />);

  expect(await screen.findByText("共 6947 条审计记录 · 29 条拦截待复核")).toBeTruthy();
  expect(screen.getByRole("button", { name: "已拦截" })).toBeTruthy();
  expect(screen.getByRole("button", { name: /仅看待复核/ })).toBeTruthy();
  expect(screen.getByLabelText("搜索审计记录")).toBeTruthy();
  expect(screen.getByLabelText("按操作人筛选")).toBeTruthy();
  // 已复核行展示复核人与时间，未复核行提供复核入口
  expect(screen.getByText("已复核 · admin 09-14 19:00")).toBeTruthy();
  expect(screen.getAllByRole("button", { name: "标记已复核" }).length).toBe(1);
});

test("result filter is sent to the backend", async () => {
  listAuditLogs.mockResolvedValue(payload(29));
  render(<SecurityPage />);
  await screen.findByText("共 6947 条审计记录 · 29 条拦截待复核");

  fireEvent.click(screen.getByRole("button", { name: "已拦截" }));
  await waitFor(() =>
    expect(listAuditLogs).toHaveBeenCalledWith(
      expect.objectContaining({ result: "已拦截" }),
    ),
  );
});

test("detail modal shows the full record and can review from inside", async () => {
  listAuditLogs.mockResolvedValue(payload(29));
  reviewAuditLog.mockResolvedValue({ id: 3, reviewedAt: "09-15 12:00", reviewedBy: "admin" });
  render(<SecurityPage />);

  const detailButtons = await screen.findAllByRole("button", { name: "详情" });
  fireEvent.click(detailButtons[0]);
  expect(await screen.findByText("审计记录 #3")).toBeTruthy();
  expect(screen.getByText("连续 5 次密码错误，触发登录保护拦截")).toBeTruthy();
  expect(screen.getByText("待复核")).toBeTruthy();

  const dialog = screen.getByRole("dialog", { name: "审计记录详情" });
  fireEvent.click(within(dialog).getByRole("button", { name: "标记已复核" }));
  await waitFor(() => expect(reviewAuditLog).toHaveBeenCalledWith(3));
});

test("batch review confirms inline then posts the current pending ids", async () => {
  listAuditLogs.mockResolvedValueOnce(payload(29)).mockResolvedValue(payload(28));
  reviewAuditLogsBatch.mockResolvedValue({ reviewed: 1, requested: 1 });
  const dispatchSpy = vi.spyOn(window, "dispatchEvent");
  render(<SecurityPage />);

  fireEvent.click(await screen.findByRole("button", { name: /批量复核/ }));
  // 两段式确认：第一次点击只进入确认态，不直接调接口
  expect(reviewAuditLogsBatch).not.toHaveBeenCalled();
  fireEvent.click(await screen.findByRole("button", { name: /确认复核这 1 条/ }));

  await waitFor(() => expect(reviewAuditLogsBatch).toHaveBeenCalledWith([3]));
  await waitFor(() => expect(listAuditLogs).toHaveBeenCalledTimes(2));
  expect(
    dispatchSpy.mock.calls.some(([event]) => event.type === "forgesight:governance-changed"),
  ).toBe(true);
});

test("reviewing an intercepted row calls the API, reloads and notifies the shell", async () => {
  listAuditLogs.mockResolvedValueOnce(payload(29)).mockResolvedValue(payload(28));
  reviewAuditLog.mockResolvedValue({ id: 3, reviewedAt: "09-15 12:00", reviewedBy: "admin" });
  const dispatchSpy = vi.spyOn(window, "dispatchEvent");

  render(<SecurityPage />);
  fireEvent.click(await screen.findByRole("button", { name: "标记已复核" }));

  await waitFor(() => expect(reviewAuditLog).toHaveBeenCalledWith(3));
  await waitFor(() => expect(listAuditLogs).toHaveBeenCalledTimes(2));
  expect(
    dispatchSpy.mock.calls.some(([event]) => event.type === "forgesight:governance-changed"),
  ).toBe(true);
  expect(await screen.findByText("共 6947 条审计记录 · 28 条拦截待复核")).toBeTruthy();
});
