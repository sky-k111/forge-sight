import { adminHealth, governanceQueue } from "../mocks/data";
import { httpGet } from "./_http";
import { respond } from "./_client";
import { currentUserId } from "../lib/auth";

export async function getBusinessOverview(sourceId, userId = currentUserId()) {
  const params = new URLSearchParams();
  if (sourceId) params.set("sourceId", sourceId);
  params.set("userId", userId);
  return httpGet(`/api/overview?${params.toString()}`);
}

export async function getAdminOverview() {
  return respond({
    health: adminHealth,
    queue: governanceQueue,
  });
}
