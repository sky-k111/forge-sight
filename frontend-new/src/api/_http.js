// 真实后端 HTTP 访问层。默认连接当前联调后端 8000，可用 VITE_API_BASE 覆盖。
const BASE = import.meta.env.VITE_API_BASE ?? "http://127.0.0.1:8000";

export function apiUrl(path) {
  return `${BASE}${path}`;
}

async function request(path, options = {}) {
  const { headers: optionHeaders, ...requestOptions } = options;
  const response = await fetch(apiUrl(path), {
    ...requestOptions,
    headers: { "Content-Type": "application/json", ...(optionHeaders ?? {}) },
  });
  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try {
      const body = await response.json();
      if (body?.detail) detail = typeof body.detail === "string" ? body.detail : detail;
    } catch {
      // 保留默认错误信息
    }
    const error = new Error(detail);
    error.code = `HTTP_${response.status}`;
    throw error;
  }
  return response.json();
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
