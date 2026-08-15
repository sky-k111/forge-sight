import { render, screen, waitFor } from "@testing-library/react";
import { vi } from "vitest";

import {
  getOperationalMetrics,
  listDeliveries,
} from "../../api/messages";
import DeliveriesPage from "./DeliveriesPage";


vi.mock("../../api/messages", () => ({
  getOperationalMetrics: vi.fn(),
  listDeliveries: vi.fn(),
  retryDelivery: vi.fn(),
}));


test("keeps delivery records usable when optional operations metrics are unavailable", async () => {
  listDeliveries.mockResolvedValue([]);
  getOperationalMetrics.mockRejectedValue(new Error("监控暂时不可用"));

  render(<DeliveriesPage />);

  expect(await screen.findByText("当前筛选没有投递记录")).toBeTruthy();
  await waitFor(() => expect(getOperationalMetrics).toHaveBeenCalledTimes(1));
  expect(screen.queryByText("投递记录加载失败")).toBeNull();
});
