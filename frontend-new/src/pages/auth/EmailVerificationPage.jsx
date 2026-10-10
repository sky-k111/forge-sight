import { useEffect, useMemo, useState } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";

import {
  changeEmail,
  fetchMe,
  requestEmailChallenge,
  verifyEmail,
} from "../../api/auth";
import forgeSightLogo from "../../assets/forgesight-logo.png";
import { Button, Card } from "../../components/ui";
import Icon from "../../components/ui/Icon";
import { getCachedUser, getToken, setSession } from "../../lib/auth";

const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export default function EmailVerificationPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const [user, setUser] = useState(getCachedUser);
  const initialEmail = user?.email ?? "";
  const isEmailChange = Boolean(location.state?.allowEmailChange);
  const [email, setEmail] = useState(initialEmail);
  const [code, setCode] = useState("");
  const [sentTo, setSentTo] = useState("");
  const [countdown, setCountdown] = useState(0);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    if (!user || typeof user.emailVerificationRequired === "boolean") return undefined;
    let active = true;
    fetchMe()
      .then((freshUser) => {
        if (!active) return;
        const token = getToken();
        if (token) setSession(token, freshUser);
        setUser(freshUser);
      })
      .catch(() => {
        // 网络故障时保留现有验证页，不放行未知的邮箱门禁状态。
      });
    return () => {
      active = false;
    };
  }, [user]);

  useEffect(() => {
    if (!countdown) return undefined;
    const timer = window.setInterval(
      () => setCountdown((value) => Math.max(0, value - 1)),
      1000,
    );
    return () => window.clearInterval(timer);
  }, [countdown]);

  const destination = useMemo(() => {
    const from = location.state?.from;
    return typeof from === "string" && from.startsWith("/")
      ? from
      : user?.role === "admin"
        ? "/admin/overview"
        : "/workspace/overview";
  }, [location.state, user?.role]);

  if (!user) {
    return <Navigate to="/login" replace />;
  }
  if ((user.emailVerificationRequired === false || user.emailStatus === "verified") && !location.state?.allowEmailChange) {
    return <Navigate to={destination} replace />;
  }

  const sendCode = async () => {
    setError("");
    setNotice("");
    if (!EMAIL_PATTERN.test(email.trim())) {
      setError("请输入可以正常接收邮件的邮箱地址");
      return;
    }
    setBusy("send");
    try {
      const response =
        isEmailChange || (initialEmail && email.trim() !== initialEmail)
          ? await changeEmail(email.trim())
          : await requestEmailChallenge(email.trim());
      setSentTo(email.trim());
      setCountdown(response.retryAfterSeconds ?? 60);
      setNotice(
        `验证码已发送至 ${response.emailMasked ?? email.trim()}，有效期 10 分钟。`,
      );
    } catch (nextError) {
      setError(nextError.message || "验证码发送失败，请稍后重试");
      if (nextError.retryAfterSeconds)
        setCountdown(nextError.retryAfterSeconds);
    } finally {
      setBusy("");
    }
  };

  const submit = async (event) => {
    event.preventDefault();
    setError("");
    if (!sentTo) {
      setError("请先发送验证码");
      return;
    }
    if (!/^\d{6}$/.test(code)) {
      setError("请输入邮件中的 6 位数字验证码");
      return;
    }
    setBusy("verify");
    try {
      await verifyEmail(sentTo, code);
      navigate(destination, { replace: true });
    } catch (nextError) {
      setError(nextError.message || "验证失败，请检查验证码后重试");
    } finally {
      setBusy("");
    }
  };

  return (
    <main className="email-gate min-h-full bg-canvas px-4 py-8 sm:py-14">
      <div className="mx-auto grid w-full max-w-[980px] overflow-hidden rounded-overlay border border-line bg-paper shadow-[0_30px_90px_rgb(24_35_41_/10%)] lg:grid-cols-[0.9fr_1.1fr]">
        <section className="relative overflow-hidden bg-ink p-7 text-white sm:p-10">
          <div
            className="absolute -right-20 -top-16 h-64 w-64 rounded-full border border-white/10"
            aria-hidden="true"
          />
          <img
            src={forgeSightLogo}
            alt="ForgeSight"
            className="h-12 w-12 rounded-control bg-white/95 object-contain p-1.5"
          />
          <p className="mt-12 text-[13px] font-semibold tracking-[0.12em] text-white/60">
            消息与周报送达
          </p>
          <h1 className="mt-3 max-w-sm font-report text-[31px] font-semibold leading-tight">
            先确认一个真正属于你的邮箱
          </h1>
          <p className="mt-5 max-w-md text-base leading-7 text-white/72">
            验证后，Agent
            周报和管理员公告会同步进入消息中心，并发送摘要、报告链接和 PDF
            附件到此邮箱。
          </p>
          <ul className="mt-9 grid gap-4 text-sm text-white/78">
            <GateBenefit
              icon="report"
              text="自动接收最新周报，不需要人工发布"
            />
            <GateBenefit icon="bell" text="重要公告与异常提醒不会错过" />
            <GateBenefit
              icon="shield"
              text="验证邮箱后才开放工作台和报告数据"
            />
          </ul>
        </section>

        <section className="p-6 sm:p-10 lg:p-12">
          <span className="text-[13px] font-semibold text-primary">
            账号安全 · 最后一步
          </span>
          <h2 className="mt-2 text-[25px] font-semibold tracking-tight text-ink">
            绑定并验证接收邮箱
          </h2>
          <p className="mt-2 text-base leading-6 text-muted">
            支持 QQ、163、企业邮箱等常用邮箱。新地址验证成功前，原邮箱不会改变。
          </p>

          <form onSubmit={submit} className="mt-8 grid gap-5">
            <label className="grid gap-2 text-sm font-semibold text-ink">
              邮箱地址
              <div className="flex gap-2">
                <input
                  type="email"
                  autoComplete="email"
                  value={email}
                  onChange={(event) => {
                    setEmail(event.target.value);
                    setSentTo("");
                    setCode("");
                  }}
                  className="input-control h-11 flex-1 text-base"
                  placeholder="name@example.com"
                />
                <Button
                  type="button"
                  className="h-11"
                  onClick={sendCode}
                  disabled={Boolean(busy) || countdown > 0}
                >
                  {busy === "send"
                    ? "正在发送…"
                    : countdown
                      ? `${countdown}s 后重发`
                      : "发送验证码"}
                </Button>
              </div>
            </label>
            <label className="grid gap-2 text-sm font-semibold text-ink">
              6 位验证码
              <input
                inputMode="numeric"
                autoComplete="one-time-code"
                value={code}
                onChange={(event) =>
                  setCode(event.target.value.replace(/\D/g, "").slice(0, 6))
                }
                className="input-control h-12 font-mono text-xl tracking-[0.35em]"
                placeholder="000000"
              />
            </label>
            {notice && (
              <p
                className="rounded-control border border-success/25 bg-success-soft px-3 py-2.5 text-sm text-success"
                role="status"
              >
                {notice}
              </p>
            )}
            {error && (
              <p
                className="rounded-control border border-critical/25 bg-critical-soft px-3 py-2.5 text-sm text-critical"
                role="alert"
              >
                {error}
              </p>
            )}
            <Button
              variant="primary"
              type="submit"
              className="h-11 w-full text-sm"
              disabled={Boolean(busy)}
            >
              {busy === "verify" ? "正在验证…" : "验证并进入工作台"}
            </Button>
          </form>
          <p className="mt-5 text-[13px] leading-5 text-muted">
            没有收到？请检查垃圾邮件，确认地址无误后等待倒计时结束再重发。
          </p>
        </section>
      </div>
    </main>
  );
}

function GateBenefit({ icon, text }) {
  return (
    <li className="flex items-center gap-3">
      <span className="grid h-9 w-9 place-items-center rounded-control bg-white/8">
        <Icon name={icon} size={17} />
      </span>
      <span>{text}</span>
    </li>
  );
}
