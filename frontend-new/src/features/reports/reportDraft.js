import { getCachedUser } from "../../lib/auth";
import { getActiveTenantId } from "../../lib/tenant";

export function reportDraftKey(sourceId) {
  const user = getCachedUser();
  return `forgesight:report-draft:v1:${encodeURIComponent(getActiveTenantId())}:${encodeURIComponent(user?.id ?? user?.username ?? "demo")}:${sourceId}`;
}

export function readReportDraft(sourceId) {
  try {
    const draft = JSON.parse(window.localStorage.getItem(reportDraftKey(sourceId)) ?? "null");
    return draft?.composition?.schemaVersion === "report-composition/v1" && Array.isArray(draft.composition.blocks) ? draft : null;
  } catch { return null; }
}

export function writeReportDraft(sourceId, draft) {
  try {
    window.localStorage.setItem(reportDraftKey(sourceId), JSON.stringify({ ...draft, updatedAt: new Date().toISOString() }));
    return true;
  } catch { return false; }
}

export function clearReportDraft(sourceId) {
  try { window.localStorage.removeItem(reportDraftKey(sourceId)); } catch { /* unavailable storage */ }
}
