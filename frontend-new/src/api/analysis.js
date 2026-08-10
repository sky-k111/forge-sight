import { listSources } from "./datasources";
import {
  createAnalysisSession,
  deleteAnalysisSession,
  listAnalysisSessions,
} from "../features/analysis/api";

const USER_ID = "local-test-user";
const ACTIVE_SOURCE_KEY = "forgesight:active-source-id";
const ANALYSIS_PATH = "/workspace/analysis";

function normalizeSession(session) {
  return {
    ...session,
    title: session.displayTitle || session.title || "未命名分析",
    path: ANALYSIS_PATH,
  };
}

async function resolveSourceId(sourceId) {
  if (sourceId) return Number(sourceId);

  const stored = Number(window.sessionStorage?.getItem(ACTIVE_SOURCE_KEY));
  const sources = await listSources();
  const selected = sources.find((item) => item.id === stored)
    ?? sources.find((item) => item.isDefault)
    ?? sources[0];
  if (!selected) throw new Error("尚未配置可用数据源");
  window.sessionStorage?.setItem(ACTIVE_SOURCE_KEY, String(selected.id));
  return selected.id;
}

export async function listSessions(userId = USER_ID) {
  const sessions = await listAnalysisSessions(userId);
  return sessions.map(normalizeSession);
}

export async function createSession({
  title = "未命名分析",
  sourceId,
  userId = USER_ID,
} = {}) {
  const selectedSourceId = await resolveSourceId(sourceId);
  const created = await createAnalysisSession({
    title,
    sourceId: selectedSourceId,
    userId,
  });
  return normalizeSession(created);
}

export function deleteSession(sessionId, userId = USER_ID) {
  return deleteAnalysisSession(sessionId, userId);
}
