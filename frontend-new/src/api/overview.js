import { httpGet } from "./_http";
import { currentUserId } from "../lib/auth";

export async function getBusinessOverview(sourceId, userId = currentUserId()) {
  const params = new URLSearchParams();
  if (sourceId) params.set("sourceId", sourceId);
  params.set("userId", userId);
  return httpGet(`/api/overview?${params.toString()}`);
}

export async function getAdminOverview() {
  return httpGet("/api/overview/admin");
}
