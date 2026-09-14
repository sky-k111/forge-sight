import { apiUrl, httpDelete, httpGet, httpPatch, httpPost, httpRequest } from "../../api/_http";
import { currentUserId, getToken } from "../../lib/auth";

export function createAnalysisSession({ title, sourceId, userId = currentUserId() }) {
  return httpPost("/api/analysis/sessions", { title, sourceId, userId });
}

export function listAnalysisSessions(userId = currentUserId()) {
  return httpGet(`/api/analysis/sessions?userId=${encodeURIComponent(userId)}`);
}

export function getAnalysisSession(sessionId) {
  return httpGet(`/api/analysis/sessions/${sessionId}`);
}

export function deleteAnalysisSession(sessionId, userId = currentUserId()) {
  return httpDelete(`/api/analysis/sessions/${sessionId}?userId=${encodeURIComponent(userId)}`);
}

export function renameAnalysisSession(sessionId, title) {
  return httpPatch(`/api/analysis/sessions/${sessionId}`, { title });
}

export function deleteAnalysisSessions(sessionIds) {
  return httpPost("/api/analysis/sessions/bulk-delete", { sessionIds });
}

export function submitAnalysisRun(sessionId, question, idempotencyKey = crypto.randomUUID(), modelProfileId = null) {
  return httpRequest(`/api/analysis/sessions/${sessionId}/runs`, {
    method: "POST",
    headers: { "Idempotency-Key": idempotencyKey },
    body: JSON.stringify({ question, ...(modelProfileId ? { modelProfileId } : {}) }),
  });
}

export function getAnalysisRun(runId) {
  return httpGet(`/api/analysis/runs/${runId}`);
}

export function getAnalysisUsage(runId, includeAttempts = false) {
  return httpGet(`/api/analysis/runs/${runId}/usage?includeAttempts=${includeAttempts}`);
}

export function cancelAnalysisRun(runId) {
  return httpPost(`/api/analysis/runs/${runId}/cancel`);
}

export function respondToClarification(interactionId, answer) {
  return httpPost(`/api/analysis/interactions/${interactionId}/responses`, { answer });
}

export function getAnalysisCheckpoints(runId) {
  return httpGet(`/api/analysis/runs/${runId}/checkpoints`);
}

export function createAnalysisBranch(
  checkpointId,
  {
    title,
    userId = currentUserId(),
    instruction,
    adjustments = {},
    reuseArtifacts = "auto",
  } = {},
) {
  return httpPost(`/api/analysis/checkpoints/${checkpointId}/branches`, {
    title,
    userId,
    instruction,
    adjustments,
    reuseArtifacts,
  });
}

const TERMINAL_EVENT_TYPES = new Set(["run.completed", "run.failed", "run.blocked", "run.cancelled"]);
const RECONNECT_DELAY_MS = 1000;

function parseSseFrame(raw) {
  const frame = { id: null, event: null, data: null };
  for (const line of raw.split("\n")) {
    if (line.startsWith(":")) continue;
    if (line.startsWith("event:")) frame.event = line.slice(6).trim();
    else if (line.startsWith("data:")) frame.data = (frame.data == null ? "" : frame.data + "\n") + line.slice(5).trimStart();
    else if (line.startsWith("id:")) frame.id = line.slice(3).trim();
  }
  return frame;
}

// 通过 fetch + ReadableStream 消费后端真 SSE（/runs/{id}/stream）。
// 原生 EventSource 无法携带 Authorization 头，故手动解析 event/data 帧。
export function subscribeAnalysisRun(runId, { onEvent, onError, includeAdmin = false } = {}) {
  let stopped = false;
  let controller = null;
  let reconnectTimer = null;
  let lastSeq = 0;

  const stop = () => {
    stopped = true;
    if (reconnectTimer != null) window.clearTimeout(reconnectTimer);
    controller?.abort();
  };

  const scheduleReconnect = () => {
    if (stopped) return;
    reconnectTimer = window.setTimeout(connect, RECONNECT_DELAY_MS);
  };

  const readStream = async (body) => {
    const reader = body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    try {
      while (!stopped) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let separatorIndex;
        while ((separatorIndex = buffer.indexOf("\n\n")) >= 0) {
          const frame = parseSseFrame(buffer.slice(0, separatorIndex));
          buffer = buffer.slice(separatorIndex + 2);
          if (!frame.event || frame.event === "heartbeat") continue;
          if (frame.id) lastSeq = Math.max(lastSeq, Number(frame.id) || 0);
          if (frame.data == null) continue;
          let event;
          try {
            event = JSON.parse(frame.data);
          } catch {
            continue;
          }
          if (TERMINAL_EVENT_TYPES.has(event.type)) stopped = true;
          onEvent?.(event);
        }
      }
    } finally {
      try { reader.releaseLock(); } catch { /* 已释放或已取消 */ }
    }
    // 终态由事件置 stopped，不再重连；其余流结束/中断则退避重连。
    if (!stopped) scheduleReconnect();
  };

  const connect = async () => {
    if (stopped) return;
    controller = new AbortController();
    try {
      const token = getToken();
      const suffix = includeAdmin ? "?includeAdmin=true" : "";
      const headers = {
        Accept: "text/event-stream",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(lastSeq > 0 ? { "Last-Event-ID": String(lastSeq) } : {}),
      };
      const response = await fetch(apiUrl(`/api/analysis/runs/${runId}/stream${suffix}`), {
        headers,
        signal: controller.signal,
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      if (!response.body) throw new Error("当前环境不支持流式响应");
      await readStream(response.body);
    } catch (error) {
      if (stopped || error?.name === "AbortError") return;
      onError?.(error);
      scheduleReconnect();
    }
  };

  connect();
  return stop;
}
