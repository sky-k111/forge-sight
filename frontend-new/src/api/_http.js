// 真实后端 HTTP 访问层。默认连接当前联调后端 8000，可用 VITE_API_BASE 覆盖。
import { AUTH_EXPIRED_EVENT, clearSession, getToken } from "../lib/auth";
import { getActiveTenantId } from "../lib/tenant";

const BASE = import.meta.env.VITE_API_BASE ?? "http://127.0.0.1:8000";

export function apiUrl(path) {
  return `${BASE}${path}`;
}

async function request(path, options = {}) {
  const { headers: optionHeaders, responseType, ...requestOptions } = options;
  const token = getToken();
  const response = await fetch(apiUrl(path), {
    ...requestOptions,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      "X-Tenant-ID": getActiveTenantId(),
      ...(optionHeaders ?? {}),
    },
  });
  if (!response.ok) {
    // 仅当本次请求带过 token 时清理会话；演示模式（无 token）下的 401 不动任何状态。
    // /api/auth/me 的 401 是挂载探测的正常路径，由 fetchMe 静默处理，不广播登出事件。
    if (response.status === 401 && token) {
      clearSession();
      if (typeof window !== "undefined" && !path.startsWith("/api/auth/me")) {
        window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT));
      }
    }
    let detail = `HTTP ${response.status}`;
    let errorCode = `HTTP_${response.status}`;
    let retryAfterSeconds;
    try {
      const body = await response.json();
      if (body?.detail) detail = typeof body.detail === "string" ? body.detail : detail;
      if (body?.errorCode) errorCode = body.errorCode;
      retryAfterSeconds = body?.retryAfterSeconds;
    } catch {
      // 保留默认错误信息
    }
    const error = new Error(detail);
    error.code = errorCode;
    error.status = response.status;
    if (retryAfterSeconds !== undefined) error.retryAfterSeconds = retryAfterSeconds;
    throw error;
  }
  return responseType === "blob" ? response.blob() : response.json();
}

export function httpRequest(path, options = {}) {
  return request(path, options);
}

export function httpGet(path) {
  return request(path);
}

export function httpPost(path, payload) {
  return request(path, {
    method: "POST",
    body: payload === undefined ? undefined : JSON.stringify(payload),
  });
}

export function httpPatch(path, payload) {
  return request(path, {
    method: "PATCH",
    body: JSON.stringify(payload ?? {}),
  });
}

export function httpDelete(path) {
  return request(path, { method: "DELETE" });
}
