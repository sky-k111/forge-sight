import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { createMemoryRouter, MemoryRouter, RouterProvider } from "react-router-dom";
import { beforeEach, vi } from "vitest";

import { exportAuditLogs, listAuditLogs, reviewAuditLog, reviewAuditLogsBatch } from "../../api/security";
import SecurityPage from "./SecurityPage";

vi.mock("../../api/security", () => ({
  listAuditLogs: vi.fn(),
  reviewAuditLog: vi.fn(),
  reviewAuditLogsBatch: vi.fn(),
  exportAuditLogs: vi.fn(),
}));

beforeEach(() => {
  listAuditLogs.mockReset();
  reviewAuditLog.mockReset();
  reviewAuditLogsBatch.mockReset();
  exportAuditLogs.mockReset();
});

test("paginates a fixed tenant snapshot and offers explicitly scoped exports", async () => {
  listAuditLogs.mockResolvedValueOnce({ ...payload(29), page: { matchedTotal: 6947, snapshotId: 7000, hasMore: true, nextCursor: 3 } }).mockResolvedValue({ ...payload(29), page: { matchedTotal: 6947, snapshotId: 7000, hasMore: false, nextCursor: null } });
  render(<MemoryRouter><SecurityPage /></MemoryRouter>);
  // 导出统一为一个下拉，两个选项分别显示预计条数
  fireEvent.click(await screen.findByRole("button", { name: /导出 CSV/ }));
  expect(await screen.findByRole("button", { name: /^导出当前页/ })).toBeTruthy();
  expect(screen.getByRole("button", { name: /^导出全部筛选记录/ }).textContent).toContain("6947");
  fireEvent.click(screen.getByRole("button", { name: "下一页" }));
  await waitFor(() => expect(listAuditLogs).toHaveBeenLastCalledWith(expect.objectContaining({ cursor: 3, snapshotId: 7000 })));
  fireEvent.change(screen.getByLabelText("搜索审计记录"), { target: { value: "良率" } });
  await waitFor(() => expect(listAuditLogs).toHaveBeenLastCalledWith(expect.objectContaining({ cursor: null, snapshotId: null, keyword: "良率" })));
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
  render(<MemoryRouter><SecurityPage /></MemoryRouter>);

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
  render(<MemoryRouter><SecurityPage /></MemoryRouter>);
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
  render(<MemoryRouter><SecurityPage /></MemoryRouter>);

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
  render(<MemoryRouter><SecurityPage /></MemoryRouter>);

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

  render(<MemoryRouter><SecurityPage /></MemoryRouter>);
  fireEvent.click(await screen.findByRole("button", { name: "标记已复核" }));

  await waitFor(() => expect(reviewAuditLog).toHaveBeenCalledWith(3));
  await waitFor(() => expect(listAuditLogs).toHaveBeenCalledTimes(2));
  expect(
    dispatchSpy.mock.calls.some(([event]) => event.type === "forgesight:governance-changed"),
  ).toBe(true);
  expect(await screen.findByText("共 6947 条审计记录 · 28 条拦截待复核")).toBeTruthy();
});

test("audit filters persist to the URL and survive reload", async () => {
  listAuditLogs.mockResolvedValue(payload(29));
  const router = createMemoryRouter([{ path: "/admin/security", element: <SecurityPage /> }], { initialEntries: ["/admin/security"] });
  const first = render(<RouterProvider router={router} />);
  await screen.findByText("共 6947 条审计记录 · 29 条拦截待复核");
  fireEvent.click(screen.getByRole("button", { name: "已拦截" }));
  await waitFor(() => expect(router.state.location.search).toContain("result="));
  const search = router.state.location.search;
  first.unmount();
  router.dispose();
  // URL 已带筛选时，重新挂载即恢复该状态
  listAuditLogs.mockClear();
  const restored = createMemoryRouter([{ path: "/admin/security", element: <SecurityPage /> }], { initialEntries: [`/admin/security${search}`] });
  render(<RouterProvider router={restored} />);
  await screen.findByText("共 6947 条审计记录 · 29 条拦截待复核");
  await waitFor(() => expect(listAuditLogs).toHaveBeenLastCalledWith(expect.objectContaining({ result: "已拦截" })));
  restored.dispose();
});
