import { useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import Icon from "../../components/ui/Icon";
import { Button, Card } from "../../components/ui";
import forgeSightLogo from "../../assets/forgesight-logo.png";
import cn from "../../lib/cn";
import { login, register } from "../../api/auth";
import { getCachedUser } from "../../lib/auth";

const INPUT_CLASS =
  "h-8 w-full rounded-tag border border-line bg-surface px-2.5 text-xs text-ink outline-none placeholder:text-faint focus:border-primary/40";

export default function LoginPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const [mode, setMode] = useState("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [email, setEmail] = useState("");
  const [error, setError] = useState(null);
  const [submitting, setSubmitting] = useState(false);

  const switchMode = (next) => {
    setMode(next);
    setError(null);
  };

  const afterAuth = (user) => {
    const from = location.state?.from;
    const fallback = user.role === "admin" ? "/admin/overview" : "/workspace/overview";
    const destination = typeof from === "string" ? from : fallback;
    navigate(user.emailStatus && user.emailStatus !== "verified" ? "/verify-email" : destination, {
      replace: true,
      state: { from: destination },
    });
  };

  const handleSubmit = async (event) => {
    event.preventDefault();
    setError(null);
    if (!/^[a-zA-Z0-9_]{3,30}$/.test(username.trim())) {
      setError("用户名需为 3-30 位字母、数字或下划线");
      return;
    }
    if (password.length < 6) {
      setError("密码长度至少 6 位");
      return;
    }
    if (mode === "register" && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim())) {
      setError("请输入可以接收周报的有效邮箱");
      return;
    }
    setSubmitting(true);
    try {
      const body =
        mode === "login"
          ? await login(username.trim(), password)
          : await register({ username: username.trim(), password, displayName: displayName.trim(), email: email.trim() });
      afterAuth(body.user);
    } catch (submitError) {
      setError(submitError.message || "操作失败，请稍后重试");
    } finally {
      setSubmitting(false);
    }
  };

  const cachedUser = getCachedUser();

  return (
    <div className="flex min-h-full items-center justify-center bg-canvas px-4 py-10">
      <div className="w-full max-w-[380px]">
        <div className="mb-6 flex flex-col items-center gap-2.5">
          <img
            className="h-14 w-14 rounded-control border border-line bg-surface object-contain p-1.5 shadow-card"
            src={forgeSightLogo}
            alt="ForgeSight logo"
          />
          <h1 className="m-0 text-xl font-semibold tracking-tight text-ink">ForgeSight 企业数据底座</h1>
          <p className="m-0 text-xs text-muted">面向制造业的智能问析与数据治理平台</p>
        </div>

        <Card className="p-5">
          <div className="mb-4 flex items-center gap-0.5 rounded-control border border-line bg-surface-2 p-0.5" role="tablist" aria-label="切换登录方式">
            <button
              type="button"
              role="tab"
              aria-selected={mode === "login"}
              className={cn(
                "h-7 flex-1 cursor-pointer rounded-tag text-xs font-medium transition-colors",
                mode === "login" ? "bg-surface text-ink shadow-card" : "text-muted hover:text-ink",
              )}
              onClick={() => switchMode("login")}
            >
              登录
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={mode === "register"}
              className={cn(
                "h-7 flex-1 cursor-pointer rounded-tag text-xs font-medium transition-colors",
                mode === "register" ? "bg-surface text-ink shadow-card" : "text-muted hover:text-ink",
              )}
              onClick={() => switchMode("register")}
            >
              注册
            </button>
          </div>

          <form className="flex flex-col gap-3" onSubmit={handleSubmit}>
            <label className="flex flex-col gap-1.5 text-xs font-semibold text-ink">
              用户名
              <input
                className={INPUT_CLASS}
                value={username}
                autoComplete="username"
                placeholder="3-30 位字母、数字或下划线"
                onChange={(event) => setUsername(event.target.value)}
              />
            </label>
            {mode === "register" && (
              <>
                <label className="flex flex-col gap-1.5 text-xs font-semibold text-ink">
                  姓名（可选）
                  <input
                    className={INPUT_CLASS}
                    value={displayName}
                    autoComplete="name"
                    placeholder="展示用姓名，留空默认用户名"
                    onChange={(event) => setDisplayName(event.target.value)}
                  />
                </label>
                <label className="flex flex-col gap-1.5 text-xs font-semibold text-ink">
                  接收邮箱
                  <input
                    className={INPUT_CLASS}
                    type="email"
                    value={email}
                    autoComplete="email"
                    placeholder="用于接收公告、周报摘要与 PDF"
                    onChange={(event) => setEmail(event.target.value)}
                  />
                </label>
              </>
            )}
            <label className="flex flex-col gap-1.5 text-xs font-semibold text-ink">
              密码
              <input
                className={INPUT_CLASS}
                type="password"
                value={password}
                autoComplete={mode === "login" ? "current-password" : "new-password"}
                placeholder="至少 6 位"
                onChange={(event) => setPassword(event.target.value)}
              />
            </label>

            {error && (
              <div className="rounded-tag border border-red/30 bg-red/10 px-2.5 py-1.5 text-xs text-red" role="alert">
                {error}
              </div>
            )}

            <Button variant="primary" size="md" type="submit" className="mt-1 w-full" disabled={submitting}>
              {submitting ? "请稍候…" : mode === "login" ? "登录" : "注册并登录"}
            </Button>
          </form>

          <div className="mt-4 flex items-center justify-between border-t border-line pt-3 text-[11px] text-faint">
            <span>{mode === "login" ? "还没有账号？" : "已有账号？"}</span>
            <button
              type="button"
              className="cursor-pointer font-semibold text-primary hover:underline"
              onClick={() => switchMode(mode === "login" ? "register" : "login")}
            >
              {mode === "login" ? "立即注册" : "返回登录"}
            </button>
          </div>
        </Card>

        {cachedUser && (
          <p className="mt-4 text-center text-[11px] text-faint">
            当前已登录 <b className="text-ink">{cachedUser.displayName || cachedUser.username}</b>，重新登录将切换账号
          </p>
        )}
      </div>
    </div>
  );
}
