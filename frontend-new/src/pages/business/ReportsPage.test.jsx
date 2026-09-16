import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createMemoryRouter, MemoryRouter, RouterProvider } from "react-router-dom";
import { beforeEach, describe, expect, test, vi } from "vitest";

import ReportsPage from "./ReportsPage";
import { listSources } from "../../api/datasources";
import {
  createReport,
  listReportSubscriptions,
  listReportTemplates,
  listReports,
} from "../../api/reports";

vi.mock("../../api/datasources", () => ({
  listSources: vi.fn(),
}));

vi.mock("../../api/reports", () => ({
  createReport: vi.fn(),
  downloadReportFile: vi.fn(),
  listReportSubscriptions: vi.fn(),
  listReportTemplates: vi.fn(),
  listReports: vi.fn(),
}));

describe("ReportsPage", () => {
  beforeEach(() => {
    vi.mocked(listSources).mockResolvedValue([
      { id: 1, name: "制造数据底座", isDefault: true },
    ]);
    vi.mocked(listReports).mockResolvedValue([]);
    vi.mocked(listReportTemplates).mockResolvedValue([
      {
        id: "tpl-operations-weekly",
        name: "制造运营综合周报",
        audience: "生产经理、厂长",
        description: "综合运营",
        coverage: { ready: true, score: 100 },
        sections: [{ code: "production", title: "生产达成", question: "统计产量" }],
      },
    ]);
    vi.mocked(listReportSubscriptions).mockResolvedValue([]);
  });

  test("defaults new report email delivery to enabled and allows opting out", async () => {
    render(
      <MemoryRouter initialEntries={["/workspace/reports"]}>
        <ReportsPage />
      </MemoryRouter>,
    );

    await screen.findByRole("heading", { name: "本周期报告" });
    fireEvent.click(screen.getByRole("button", { name: "创建报告" }));
    fireEvent.click(await screen.findByRole("button", { name: /继续/ }));

    const checkbox = await screen.findByRole("checkbox", { name: /发布后发送到绑定邮箱/ });
    expect(checkbox.checked).toBe(true);
    fireEvent.click(checkbox);
    await waitFor(() => expect(checkbox.checked).toBe(false));
  });

  test("merges status into the header and keeps the template rail collapsed by default", async () => {
    vi.mocked(listReports).mockResolvedValue([
      {
        id: "rpt-1", title: "九月第 2 周综合周报", status: "published", statusLabel: "已发布",
        periodStart: "2026-09-07T00:00:00", periodEnd: "2026-09-13T00:00:00",
        version: { version: 1 }, templateName: "综合周报", files: [],
        reportOverview: { anomalies: [] },
      },
    ]);
    render(
      <MemoryRouter initialEntries={["/workspace/reports"]}>
        <ReportsPage />
      </MemoryRouter>,
    );

    const heading = await screen.findByRole("heading", { name: "本周期报告" });
    const header = heading.closest("header");
    // 单一页头承载结论、本周期与最近发布，不再另设状态横条
    expect(header.textContent).toContain("本周期运行正常");
    expect(header.textContent).toContain("最近发布");
    // 自动送达并入筛选工具条
    expect(screen.getByText(/自动送达 0 个订阅/)).toBeTruthy();
    // 模板与覆盖默认收起，工具条开关可展开/收起
    const railToggle = screen.getByRole("button", { name: /^模板与覆盖/ });
    expect(railToggle.getAttribute("aria-pressed")).toBe("false");
    fireEvent.click(railToggle);
    expect(screen.getByRole("button", { name: "收起模板与覆盖" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "收起模板与覆盖" }));
    expect(
      screen.getByRole("button", { name: /^模板与覆盖/ }).getAttribute("aria-pressed"),
    ).toBe("false");
  });

  test("sends the opt-out choice to the report creation API", async () => {
    vi.mocked(createReport).mockResolvedValue({ id: "rpt-1" });
    render(
      <MemoryRouter initialEntries={["/workspace/reports"]}>
        <ReportsPage />
      </MemoryRouter>,
    );

    await screen.findByRole("heading", { name: "本周期报告" });
    fireEvent.click(screen.getByRole("button", { name: "创建报告" }));
    fireEvent.click(await screen.findByRole("button", { name: /继续/ }));
    fireEvent.click(await screen.findByRole("checkbox", { name: /发布后发送到绑定邮箱/ }));
    fireEvent.click(screen.getByRole("button", { name: /继续/ }));
    fireEvent.click(screen.getByRole("button", { name: /继续/ }));
    fireEvent.click(screen.getByRole("button", { name: "开始生成" }));

    await waitFor(() => expect(createReport).toHaveBeenCalled());
    expect(vi.mocked(createReport).mock.calls.at(-1)[0].emailEnabled).toBe(false);
  });

  test("status filter and search persist to the URL and survive reload", async () => {
    vi.mocked(listReports).mockResolvedValue([
      {
        id: "rpt-1", title: "九月第 2 周综合周报", status: "published", statusLabel: "已发布",
        periodStart: "2026-09-07T00:00:00", periodEnd: "2026-09-13T00:00:00",
        version: { version: 1 }, templateName: "综合周报", files: [], reportOverview: { anomalies: [] },
      },
      {
        id: "rpt-2", title: "设备停机专题", status: "failed", statusLabel: "生成失败",
        periodStart: "2026-09-07T00:00:00", periodEnd: "2026-09-13T00:00:00",
        version: { version: 1 }, templateName: "专题报告", files: [], reportOverview: { anomalies: [{ severity: "high" }] },
      },
    ]);
    const router = createMemoryRouter([{ path: "/workspace/reports", element: <ReportsPage /> }], { initialEntries: ["/workspace/reports"] });
    const first = render(<RouterProvider router={router} />);
    await screen.findByRole("heading", { name: "本周期报告" });
    fireEvent.click(screen.getByRole("button", { name: /^需关注/ }));
    fireEvent.change(screen.getByLabelText("搜索报告"), { target: { value: "设备" } });
    await waitFor(() => expect(router.state.location.search).toContain("status=attention"));
    expect(router.state.location.search).toContain("q=");
    const search = router.state.location.search;
    first.unmount();
    router.dispose();

    const restored = createMemoryRouter([{ path: "/workspace/reports", element: <ReportsPage /> }], { initialEntries: [`/workspace/reports${search}`] });
    const { container } = render(<RouterProvider router={restored} />);
    await screen.findByRole("heading", { name: "本周期报告" });
    // 恢复后列表只应看到命中筛选的那一条（页头“最近发布”仍会引用已发布报告，故按行断言）
    const rows = container.querySelectorAll("article");
    expect(rows.length).toBe(1);
    expect(rows[0].textContent).toContain("设备停机专题");
    restored.dispose();
  });
});
