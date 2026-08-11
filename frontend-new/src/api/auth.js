// 认证 API：注册 / 登录 / 当前用户 / 登出。
import { clearSession, setSession } from "../lib/auth";
import { httpGet, httpPost } from "./_http";

export async function login(username, password) {
  const body = await httpPost("/api/auth/login", { username, password });
  setSession(body.token, body.user);
  return body;
}

export async function register({ username, password, displayName }) {
  const body = await httpPost("/api/auth/register", { username, password, displayName });
  setSession(body.token, body.user);
  return body;
}

export async function fetchMe() {
  const body = await httpGet("/api/auth/me");
  return body.user;
}

export async function logout() {
  try {
    await httpPost("/api/auth/logout", {});
  } catch {
    // 服务端无状态，登出失败不影响本地清理
  }
  clearSession();
}
