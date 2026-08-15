import { beforeEach, describe, expect, test, vi } from "vitest";

import {
  createAnnouncement,
  getMessage,
  getUnreadCount,
  listAnnouncements,
  listDeliveries,
  listMessages,
  markMessageRead,
  publishAnnouncement,
  retryDelivery,
  withdrawAnnouncement,
} from "./messages";
import { httpGet, httpPatch, httpPost } from "./_http";

vi.mock("./_http", () => ({
  httpGet: vi.fn(),
  httpPatch: vi.fn(),
  httpPost: vi.fn(),
}));

describe("message center API contract", () => {
  beforeEach(() => vi.clearAllMocks());

  test("serializes inbox filters and exposes read operations", async () => {
    httpGet.mockResolvedValueOnce({ items: [], nextCursor: null });
    httpGet.mockResolvedValueOnce({ count: 3 });
    httpGet.mockResolvedValueOnce({ id: "msg-1" });
    httpPost.mockResolvedValueOnce({ messageId: "msg-1", isRead: true });

    await listMessages({ type: "weekly_report", read: false, priority: "urgent", keyword: "良率" });
    await getUnreadCount();
    await getMessage("msg-1");
    await markMessageRead("msg-1");

    expect(httpGet).toHaveBeenNthCalledWith(
      1,
      "/api/v1/messages?type=weekly_report&read=false&priority=urgent&keyword=%E8%89%AF%E7%8E%87",
    );
    expect(httpGet).toHaveBeenNthCalledWith(2, "/api/v1/messages/unread-count");
    expect(httpGet).toHaveBeenNthCalledWith(3, "/api/v1/messages/msg-1");
    expect(httpPost).toHaveBeenCalledWith("/api/v1/messages/msg-1/read");
  });

  test("maps announcement and delivery administrator actions", async () => {
    httpGet.mockResolvedValue({ items: [] });
    httpPost.mockResolvedValue({ id: "result" });
    httpPatch.mockResolvedValue({ id: "ann-1" });

    await listAnnouncements("draft");
    await createAnnouncement({ title: "公告" });
    await publishAnnouncement("ann-1");
    await withdrawAnnouncement("ann-1", "窗口取消");
    await listDeliveries({ status: "failed", recipientUserId: "u-1" });
    await retryDelivery("del-1", true);

    expect(httpGet).toHaveBeenNthCalledWith(1, "/api/v1/announcements?status=draft");
    expect(httpPost).toHaveBeenCalledWith("/api/v1/announcements", { title: "公告" });
    expect(httpPost).toHaveBeenCalledWith("/api/v1/announcements/ann-1/publish");
    expect(httpPost).toHaveBeenCalledWith("/api/v1/announcements/ann-1/withdraw", { reason: "窗口取消" });
    expect(httpGet).toHaveBeenNthCalledWith(
      2,
      "/api/v1/deliveries?status=failed&recipientUserId=u-1",
    );
    expect(httpPost).toHaveBeenCalledWith("/api/v1/deliveries/del-1/retry?confirmUnknown=true");
  });
});
