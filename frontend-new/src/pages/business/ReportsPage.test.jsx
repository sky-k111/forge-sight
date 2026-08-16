import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
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

    await screen.findByRole("heading", { name: "周报中心" });
    fireEvent.click(screen.getByRole("button", { name: "创建报告" }));
    fireEvent.click(await screen.findByRole("button", { name: /继续/ }));

    const checkbox = await screen.findByRole("checkbox", { name: /发布后发送到绑定邮箱/ });
    expect(checkbox.checked).toBe(true);
    fireEvent.click(checkbox);
    await waitFor(() => expect(checkbox.checked).toBe(false));
  });

  test("sends the opt-out choice to the report creation API", async () => {
    vi.mocked(createReport).mockResolvedValue({ id: "rpt-1" });
    render(
      <MemoryRouter initialEntries={["/workspace/reports"]}>
        <ReportsPage />
      </MemoryRouter>,
    );

    await screen.findByRole("heading", { name: "周报中心" });
    fireEvent.click(screen.getByRole("button", { name: "创建报告" }));
    fireEvent.click(await screen.findByRole("button", { name: /继续/ }));
    fireEvent.click(await screen.findByRole("checkbox", { name: /发布后发送到绑定邮箱/ }));
    fireEvent.click(screen.getByRole("button", { name: /继续/ }));
    fireEvent.click(screen.getByRole("button", { name: /继续/ }));
    fireEvent.click(screen.getByRole("button", { name: "开始生成" }));

    await waitFor(() => expect(createReport).toHaveBeenCalled());
    expect(vi.mocked(createReport).mock.calls.at(-1)[0].emailEnabled).toBe(false);
  });
});
