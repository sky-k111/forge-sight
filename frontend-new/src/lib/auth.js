// 认证会话存储：token 与当前用户（localStorage）。
// 纯存储层，不 import _http，避免循环依赖。
const TOKEN_KEY = "forgesight:auth-token";
const USER_KEY = "forgesight:auth-user";

export const AUTH_EXPIRED_EVENT = "forgesight:auth-expired";

export function getToken() {
  try {
    return window.localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function getCachedUser() {
  try {
    const raw = window.localStorage.getItem(USER_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" && parsed.username ? parsed : null;
  } catch {
    return null;
  }
}

export function setSession(token, user) {
  try {
    window.localStorage.setItem(TOKEN_KEY, token);
    window.localStorage.setItem(USER_KEY, JSON.stringify(user));
  } catch {
    // 存储不可用时静默降级为演示模式
  }
}

export function clearSession() {
  try {
    window.localStorage.removeItem(TOKEN_KEY);
    window.localStorage.removeItem(USER_KEY);
  } catch {
    // 忽略
  }
}

// 全部业务接口的用户归属入口：登录用户优先，未登录回退演示用户。
export function currentUserId() {
  return getCachedUser()?.username ?? "local-test-user";
}
