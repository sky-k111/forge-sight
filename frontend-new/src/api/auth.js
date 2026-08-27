// 认证 API：注册 / 登录 / 邮箱验证 / 当前用户 / 登出 / 钉钉与飞书绑定。
import { clearSession, setSession } from "../lib/auth";
import { httpDelete, httpGet, httpPost, httpRequest } from "./_http";

export async function login(username, password) {
  const body = await httpPost("/api/auth/login", { username, password });
  setSession(body.token, body.user);
  return body;
}

export async function register({ username, password, displayName, email }) {
  const body = await httpPost("/api/auth/register", { username, password, displayName, email });
  setSession(body.token, body.user);
  return body;
}

export function requestEmailChallenge(email) {
  return httpPost("/api/auth/email/challenges", email ? { email } : {});
}

export function changeEmail(email) {
  return httpPost("/api/auth/email/change", { email });
}

export async function verifyEmail(email, code) {
  const body = await httpPost("/api/auth/email/verify", { email, code });
  setSession(body.token, body.user);
  return body;
}

export async function fetchMe() {
  const body = await httpGet("/api/auth/me");
  return body.user;
}

export function getDingTalkBinding() {
  return httpGet("/api/dingtalk/binding/me");
}

export function startDingTalkBind(redirectPath = "/login") {
  const suffix = redirectPath ? `?redirectPath=${encodeURIComponent(redirectPath)}` : "";
  return httpGet(`/api/dingtalk/oauth/start${suffix}`);
}

export function getDingTalkBindingSources() {
  return httpGet("/api/dingtalk/binding/sources");
}

export function selectDingTalkBindingSource(sourceId) {
  return httpRequest("/api/dingtalk/binding/source", {
    method: "PUT",
    body: JSON.stringify({ sourceId }),
  });
}

export function unbindDingTalk() {
  return httpDelete("/api/dingtalk/binding/me");
}

export function getFeishuBinding() {
  return httpGet("/api/feishu/binding/me");
}

export function startFeishuBind(redirectPath = "/login") {
  const suffix = redirectPath ? `?redirectPath=${encodeURIComponent(redirectPath)}` : "";
  return httpGet(`/api/feishu/oauth/start${suffix}`);
}

export function getFeishuBindingSources() {
  return httpGet("/api/feishu/binding/sources");
}

export function selectFeishuBindingSource(sourceId) {
  return httpRequest("/api/feishu/binding/source", {
    method: "PUT",
    body: JSON.stringify({ sourceId }),
  });
}

export function unbindFeishu() {
  return httpDelete("/api/feishu/binding/me");
}

export async function logout() {
  try {
    await httpPost("/api/auth/logout", {});
  } catch {
    // 服务端无状态，登出失败不影响本地清理
  }
  clearSession();
}
