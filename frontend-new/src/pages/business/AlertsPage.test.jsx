import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, test, vi } from "vitest";

import { listMonitoringAlerts } from "../../api/monitoring";
import AlertsPage from "./AlertsPage";

vi.mock("../../api/monitoring", () => ({ listMonitoringAlerts: vi.fn() }));

describe("AlertsPage", () => {
  beforeEach(() => {
    vi.mocked(listMonitoringAlerts).mockResolvedValue([
      {
        id: "alt-1",
        title: "总装检测工序良率异常",
        entityName: "总装检测",
        severity: "high",
        status: "firing",
        currentValue: 0.84,
        sampleSize: 120,
        lastSeenAt: "2026-08-27T08:00:00",
      },
    ]);
  });

  test("shows read-only alert status and evidence entry", async () => {
    render(<MemoryRouter><AlertsPage /></MemoryRouter>);
    expect(await screen.findByText("总装检测工序良率异常")).toBeTruthy();
    expect(screen.getAllByText("告警中").length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: /关闭|验收|处理/ })).toBeNull();
  });
});
