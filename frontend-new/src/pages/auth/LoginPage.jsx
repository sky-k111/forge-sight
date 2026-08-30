import { useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { Card, Button } from "../../components/ui";
import forgeSightLogo from "../../assets/forgesight-logo.png";
import cn from "../../lib/cn";
import {
  getDingTalkBinding,
  getDingTalkBindingSources,
  getFeishuBinding,
  getFeishuBindingSources,
  fetchMe,
  login,
  register,
  selectDingTalkBindingSource,
  selectFeishuBindingSource,
  startDingTalkBind,
  startFeishuBind,
} from "../../api/auth";
import {
  clearPendingRegistrationBinding,
  getCachedUser,
  getPendingRegistrationBinding,
  getToken,
  setSession,
  setPendingRegistrationBinding,
} from "../../lib/auth";

const INPUT_CLASS =
  "h-8 w-full rounded-tag border border-line bg-surface px-2.5 text-xs text-ink outline-none placeholder:text-faint focus:border-primary/40";

function ProviderMark({ provider }) {
  const isDingTalk = provider === "dingtalk";
  return (
    <span
      className={cn(
        "flex h-9 w-9 shrink-0 items-center justify-center rounded-control text-white",
        isDingTalk ? "bg-[#1677ff]" : "bg-[#00b96b]"
      )}
      aria-hidden="true"
    >
      {isDingTalk ? (
        <svg viewBox="0 0 1024 1024" className="h-5 w-5 fill-current" focusable="false">
          <path d="M573.7 252.5C422.5 197.4 201.3 96.7 201.3 96.7c-15.7-4.1-17.9 11.1-17.9 11.1-5 61.1 33.6 160.5 53.6 182.8 19.9 22.3 319.1 113.7 319.1 113.7S326 357.9 270.5 341.9c-55.6-16-37.9 17.8-37.9 17.8 11.4 61.7 64.9 131.8 107.2 138.4 42.2 6.6 220.1 4 220.1 4s-35.5 4.1-93.2 11.9c-42.7 5.8-97 12.5-111.1 17.8-33.1 12.5 24 62.6 24 62.6 84.7 76.8 129.7 50.5 129.7 50.5 33.3-10.7 61.4-18.5 85.2-24.2L565 743.1h84.6L603 928l205.3-271.9H700.8l22.3-38.7c.3.5.4.8.4.8S799.8 496.1 829 433.8l.6-1h-.1c5-10.8 8.6-19.7 10-25.8 17-71.3-114.5-99.4-265.8-154.5z" />
        </svg>
      ) : (
        <svg viewBox="0 0 48 48" className="h-5 w-5 fill-current" focusable="false">
          <path d="M41.072 5.994 3.311 16.519l9.075 9.293 8.414.147 9.683-9.44c-.257-.525-.385-.964-.385-1.318 0-.794.311-1.423.796-1.868.829-.763 1.827-.878 2.994-.343l7.184-6.996Z" />
          <path d="m42.103 6.728-10.525 37.761-9.294-9.075-.147-8.414 9.375-9.518c.508.364 1.063.528 1.665.495.902-.05 1.486-.595 1.759-.916.274-.321.594-.855.568-1.649-.018-.529-.192-1.017-.522-1.464l7.121-7.22Z" />
        </svg>
      )}
    </span>
  );
}

function ProviderOption({ provider, selected, disabled = false, onSelect }) {
  const isDingTalk = provider === "dingtalk";
  return (
    <button
      type="button"
      className={cn(
        "flex min-h-[72px] flex-1 items-center gap-2.5 rounded-control border px-3 py-2.5 text-left transition",
        selected
          ? "border-primary bg-primary/5 ring-1 ring-primary/15"
          : "border-line bg-surface hover:border-primary/30",
        disabled && "cursor-not-allowed opacity-60 hover:border-line"
      )}
      aria-pressed={selected}
      aria-label={`${isDingTalk ? "钉钉" : "飞书"}账号绑定${disabled ? "，暂未开放" : ""}`}
      onClick={() => !disabled && onSelect(provider)}
      disabled={disabled}
    >
      <ProviderMark provider={provider} />
      <span className="min-w-0">
        <span className="flex items-center gap-1.5 text-xs font-semibold text-ink">
          {isDingTalk ? "钉钉" : "飞书"}
          <span
            className={cn(
              "rounded-full px-1.5 py-0.5 text-[9px] font-medium",
              disabled ? "bg-surface-2 text-faint" : "bg-green-500/10 text-green-700"
            )}
          >
            {disabled ? "待接入" : "可绑定"}
          </span>
        </span>
        <span className="mt-1 block text-[10px] leading-4 text-muted">
          {isDingTalk ? "授权账号、机器人会话与周报投递" : "授权账号、机器人会话与周报投递"}
        </span>
      </span>
    </button>
  );
}

export default function LoginPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const cachedUser = getCachedUser();

  const [mode, setMode] = useState("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [email, setEmail] = useState("");
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const [registerStage, setRegisterStage] = useState("account");
  const [selectedProvider, setSelectedProvider] = useState("");
  const [registrationUser, setRegistrationUser] = useState(null);

  const [dingtalkBinding, setDingTalkBinding] = useState(null);
  const [feishuBinding, setFeishuBinding] = useState(null);
  const [bindingLoading, setBindingLoading] = useState(false);
  const [bindingSubmitting, setBindingSubmitting] = useState(false);
  const [bindingSources, setBindingSources] = useState([]);
  const [sourceSubmitting, setSourceSubmitting] = useState(false);
  const [introComplete, setIntroComplete] = useState(false);
  const activeUser = registrationUser || cachedUser;
  const registrationBindingRequired = mode === "register" && registerStage === "binding";
  const binding =
    selectedProvider === "feishu"
      ? feishuBinding
      : selectedProvider === "dingtalk"
        ? dingtalkBinding
        : null;

  const handleProviderSelection = (provider) => {
    setSelectedProvider(provider);
    setError(null);
    if (registrationBindingRequired) {
      setPendingRegistrationBinding(provider);
    }
  };

  const switchMode = (next) => {
    if (registerStage === "binding" && registrationUser && next !== "register") {
      setError("请先完成第三方账号绑定，当前注册流程不能跳过");
      return;
    }
    setMode(next);
    setError(null);
    if (next === "register" && !registrationUser) {
      setRegisterStage("account");
    }
  };

  useEffect(() => {
    const params = new URLSearchParams(location.search);
    const dingtalkBindStatus = params.get("dingtalkBind");
    const feishuBindStatus = params.get("feishuBind");
    const bindStatus = dingtalkBindStatus || feishuBindStatus;
    const callbackProvider = feishuBindStatus ? "feishu" : dingtalkBindStatus ? "dingtalk" : "";
    const message = params.get("message");
    const pendingProvider = getPendingRegistrationBinding();

    if (pendingProvider && cachedUser) {
      setMode("register");
      setRegisterStage("binding");
      setSelectedProvider(pendingProvider);
      setRegistrationUser(cachedUser);
    }

    if (callbackProvider) {
      setSelectedProvider(callbackProvider);
    }

    if (!bindStatus) {
      return;
    }

    if (bindStatus === "success") {
      setNotice({
        type: "success",
        text: message || `${callbackProvider === "feishu" ? "飞书" : "钉钉"}账号绑定成功。`,
      });
      setError(null);
    } else if (bindStatus === "failed") {
      setNotice({
        type: "error",
        text:
          message || `${callbackProvider === "feishu" ? "飞书" : "钉钉"}账号绑定失败，请稍后重试。`,
      });
    }

    navigate(location.pathname, { replace: true, state: location.state });
  }, [location.pathname, location.search, location.state, navigate]);

  useEffect(() => {
    let cancelled = false;

    if (!activeUser) {
      setDingTalkBinding(null);
      setFeishuBinding(null);
      setBindingLoading(false);
      return undefined;
    }

    setBindingLoading(true);
    const sourceRequest =
      selectedProvider === "feishu"
        ? getFeishuBindingSources()
        : getDingTalkBindingSources();
    Promise.all([getDingTalkBinding(), sourceRequest, getFeishuBinding()])
      .then(([dingtalkBody, sourceBody, feishuBody]) => {
        if (!cancelled) {
          setDingTalkBinding(dingtalkBody);
          setFeishuBinding(feishuBody);
          setBindingSources(sourceBody?.items ?? []);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setDingTalkBinding(null);
          setFeishuBinding(null);
          setBindingSources([]);
        }
      })
      .finally(() => {
        if (!cancelled) {
          setBindingLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [activeUser?.username, selectedProvider]);

  const afterAuth = (user) => {
    const from = location.state?.from;
    const fallback = user.role === "admin" ? "/admin/overview" : "/workspace/overview";
    const destination = typeof from === "string" ? from : fallback;
    navigate(user.emailStatus && user.emailStatus !== "verified" ? "/verify-email" : destination, {
      replace: true,
      state: { from: destination },
    });
  };

  const continueAuthFlow = (body) => {
    const user = body.user;
    if (user.externalIdentityRequired && !user.externalIdentityBound) {
      setMode("register");
      setRegisterStage("binding");
      setRegistrationUser(user);
      setSelectedProvider(getPendingRegistrationBinding() || "");
      setNotice({
        type: "success",
        text: "账号验证成功，请绑定钉钉或飞书后继续。",
      });
      return;
    }
    afterAuth(user);
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

    if (mode === "register" && !selectedProvider) {
      setError("注册前请选择并绑定钉钉或飞书账号");
      return;
    }

    setSubmitting(true);
    try {
      const body =
        mode === "login"
          ? await login(username.trim(), password)
          : await register({
              username: username.trim(),
              password,
              displayName: displayName.trim(),
              email: email.trim(),
            });
      if (mode === "register") {
        setPendingRegistrationBinding(selectedProvider);
        setRegistrationUser(body.user);
        setRegisterStage("binding");
        setNotice({
          type: "success",
          text: "平台账号已创建。完成第三方账号绑定后，注册流程才会结束。",
        });
      } else {
        continueAuthFlow(body);
      }
    } catch (submitError) {
      setError(submitError.message || "操作失败，请稍后重试");
    } finally {
      setSubmitting(false);
    }
  };

  const handleBindProvider = async (provider = selectedProvider || "dingtalk") => {
    setError(null);

    if (!activeUser) {
      setError("当前后端要求先登录平台账号，再发起第三方账号绑定");
      return;
    }

    setBindingSubmitting(true);
    try {
      setPendingRegistrationBinding(provider);
      setSelectedProvider(provider);
      const body =
        provider === "feishu" ? await startFeishuBind("/login") : await startDingTalkBind("/login");
      if (!body?.authUrl) {
        throw new Error(
          `未获取到${provider === "feishu" ? "飞书" : "钉钉"}授权地址，请检查后端配置`
        );
      }
      window.location.href = body.authUrl;
    } catch (bindError) {
      setError(
        bindError.message || `拉起${provider === "feishu" ? "飞书" : "钉钉"}授权失败，请稍后重试`
      );
    } finally {
      setBindingSubmitting(false);
    }
  };

  const handleSelectBindingSource = async (event) => {
    const sourceId = Number(event.target.value);
    if (!sourceId) return;
    setSourceSubmitting(true);
    setError(null);
    try {
      const result =
        selectedProvider === "feishu"
          ? await selectFeishuBindingSource(sourceId)
          : await selectDingTalkBindingSource(sourceId);
      const updateBinding = (current) => ({ ...current, sourceId: result.sourceId });
      if (selectedProvider === "feishu") {
        setFeishuBinding(updateBinding);
      } else {
        setDingTalkBinding(updateBinding);
      }
      setNotice({
        type: "success",
        text: `${selectedProvider === "feishu" ? "飞书" : "钉钉"}机器人已绑定数据源：${result.sourceName}`,
      });
    } catch (sourceError) {
      setError(sourceError.message || "机器人数据源选择失败");
    } finally {
      setSourceSubmitting(false);
    }
  };

  const handleFinishRegistration = async () => {
    if (!activeUser || !binding?.bound) {
      setError("请先完成钉钉或飞书账号绑定");
      return;
    }
    if (!binding.sourceId) {
      setError("请先选择机器人可使用的数据源");
      return;
    }
    setBindingSubmitting(true);
    setError(null);
    try {
      const freshUser = await fetchMe();
      if (!freshUser.externalIdentityBound) {
        throw new Error("后端尚未确认第三方账号绑定，请重新授权");
      }
      const token = getToken();
      if (token) setSession(token, freshUser);
      clearPendingRegistrationBinding();
      setRegistrationUser(freshUser);
      afterAuth(freshUser);
    } catch (finishError) {
      setError(finishError.message || "确认第三方账号绑定失败");
    } finally {
      setBindingSubmitting(false);
    }
  };

  const bindingText = !selectedProvider
    ? "请选择一个平台继续"
    : !activeUser
      ? "当前未登录，需先完成平台注册并保持登录后再绑定第三方账号"
      : bindingLoading
        ? "正在读取绑定状态..."
        : binding?.bound
          ? `已绑定：${binding.displayName || binding.mobile || binding.unionId || `${selectedProvider === "feishu" ? "飞书" : "钉钉"}账号`}`
          : `当前未绑定${selectedProvider === "feishu" ? "飞书" : "钉钉"}账号`;

  useEffect(() => {
    if (window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches) {
      setIntroComplete(true);
      return undefined;
    }

    const timer = window.setTimeout(() => setIntroComplete(true), 3200);
    return () => window.clearTimeout(timer);
  }, []);

  return (
    <main className={cn("login-page", introComplete && "is-ready")}>
      <div className="login-page__grain" aria-hidden="true" />
      <div className="login-page__curtain" aria-hidden="true" />
      {!introComplete && (
        <button
          type="button"
          className="login-page__skip"
          onClick={() => setIntroComplete(true)}
          aria-label="跳过开场动画"
        >
          跳过开场 <span aria-hidden="true">↗</span>
        </button>
      )}

      <header className="login-page__topbar">
        <span className="login-page__eyebrow">FORGESIGHT / 01</span>
        <span className="login-page__status"><i aria-hidden="true" /> SYSTEM READY</span>
      </header>

      <div className="login-page__layout">
        <section className="login-page__brand-column" aria-labelledby="forgesight-title">
          <div className="login-page__brand-index" aria-hidden="true">
            <span>智能制造 · 数据底座</span>
          </div>
          <div className="login-page__brand-lockup">
            <div className="login-page__orbit login-page__orbit--one" aria-hidden="true" />
            <div className="login-page__orbit login-page__orbit--two" aria-hidden="true" />
            <div className="login-page__logo-frame">
              <img src={forgeSightLogo} alt="ForgeSight logo" />
              <span className="login-page__logo-caption" aria-hidden="true">precision / intelligence</span>
            </div>
            <div className="login-page__brand-copy">
              <p className="login-page__brand-kicker">THE OPERATING LAYER FOR INDUSTRY</p>
              <h1
                id="forgesight-title"
                className="login-page__title"
                aria-label="ForgeSight 企业数据底座"
              >
                <span className="login-page__title-word" aria-hidden="true">
                  {Array.from("Forge").map((letter, index) => (
                    <span
                      className="login-page__title-letter"
                      key={`forge-${letter}-${index}`}
                      style={{ "--letter-index": index }}
                    >
                      {letter}
                    </span>
                  ))}
                </span>
                <span className="login-page__title-word" aria-hidden="true">
                  {Array.from("Sight").map((letter, index) => (
                    <span
                      className="login-page__title-letter"
                      key={`sight-${letter}-${index}`}
                      style={{ "--letter-index": index + 5 }}
                    >
                      {letter}
                    </span>
                  ))}
                </span>
              </h1>
              <p className="login-page__subtitle">面向制造业的智能问析与数据治理平台</p>
            </div>
          </div>
        </section>

        <section className="login-page__auth-column" aria-label="登录与注册">
          <div className="login-page__auth-heading">
            <p className="login-page__auth-kicker">WELCOME BACK</p>
            <h2>{mode === "login" ? "进入你的数据现场" : "建立你的工作空间"}</h2>
          </div>

          <Card className={cn("login-card p-5", `login-card--${mode}`)}>
          <div className="login-card__content">
            <div
              className="login-card__tabs mb-4 flex items-center gap-0.5 rounded-control border border-line bg-surface-2 p-0.5"
              role="tablist"
              aria-label="切换登录方式"
            >
            <button
              type="button"
              role="tab"
              aria-selected={mode === "login"}
              className={cn(
                "h-7 flex-1 cursor-pointer rounded-tag text-xs font-medium transition-colors",
                mode === "login" ? "bg-surface text-ink shadow-card" : "text-muted hover:text-ink"
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
                mode === "register"
                  ? "bg-surface text-ink shadow-card"
                  : "text-muted hover:text-ink"
              )}
              onClick={() => switchMode("register")}
            >
              注册
            </button>
            </div>

            {notice && (
            <div
              className={cn(
                "mb-3 rounded-tag px-2.5 py-2 text-xs",
                notice.type === "success"
                  ? "border border-green-500/30 bg-green-500/10 text-green-700"
                  : "border border-red/30 bg-red/10 text-red"
              )}
              role="status"
            >
              {notice.text}
            </div>
            )}

            {!registrationBindingRequired ? (
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

                  <fieldset className="rounded-control border border-line bg-surface-2 px-3 py-3">
                    <legend className="px-1 text-xs font-semibold text-ink">
                      第三方账号绑定 <span className="text-red">*</span>
                    </legend>
                    <p className="mb-2 mt-0 text-[10px] leading-4 text-muted">
                      注册后必须完成其中一种绑定，用于机器人身份识别、会话上下文和周报投递。
                    </p>
                    <div className="flex gap-2">
                      <ProviderOption
                        provider="dingtalk"
                        selected={selectedProvider === "dingtalk"}
                        onSelect={handleProviderSelection}
                      />
                      <ProviderOption
                        provider="feishu"
                        selected={selectedProvider === "feishu"}
                        onSelect={handleProviderSelection}
                      />
                    </div>
                    <p className="mb-0 mt-2 text-[10px] leading-4 text-faint">
                      创建平台账号后会跳转到所选平台授权；授权凭证不会保存在浏览器中。
                    </p>
                  </fieldset>
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
                <div
                  className="rounded-tag border border-red/30 bg-red/10 px-2.5 py-1.5 text-xs text-red"
                  role="alert"
                >
                  {error}
                </div>
              )}

              <Button
                variant="primary"
                size="md"
                type="submit"
                className="mt-1 w-full"
                disabled={submitting}
              >
                {submitting ? "请稍候…" : mode === "login" ? "登录" : "创建账号并继续绑定"}
              </Button>
            </form>
            ) : (
            <section aria-labelledby="registration-binding-title">
              <div className="mb-4 flex items-start justify-between gap-3">
                <div>
                  <p className="m-0 text-[10px] font-semibold uppercase tracking-[0.14em] text-primary">
                    注册步骤 2 / 2
                  </p>
                  <h2
                    id="registration-binding-title"
                    className="mb-0 mt-1 text-base font-semibold text-ink"
                  >
                    完成第三方账号绑定
                  </h2>
                </div>
                <span className="rounded-full bg-amber-500/10 px-2 py-1 text-[10px] font-medium text-amber-700">
                  必须完成
                </span>
              </div>

              <div className="rounded-control border border-primary/25 bg-primary/5 px-3 py-3">
                <div className="flex gap-2">
                  <ProviderOption
                    provider="dingtalk"
                    selected={selectedProvider === "dingtalk"}
                    onSelect={handleProviderSelection}
                  />
                  <ProviderOption
                    provider="feishu"
                    selected={selectedProvider === "feishu"}
                    onSelect={handleProviderSelection}
                  />
                </div>
                <p className="mb-0 mt-2 text-[10px] text-muted">{bindingText}</p>
                <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-surface">
                  <div
                    className={cn(
                      "h-full rounded-full transition-all",
                      binding?.bound ? "w-full bg-green-500" : "w-1/2 bg-primary"
                    )}
                  />
                </div>
              </div>

              <p className="mt-3 text-[11px] leading-5 text-muted">
                授权成功后，机器人将使用当前平台账号的数据权限，并可识别该账号的历史会话与周报订阅。
              </p>

              {binding?.bound && (
                <label className="mt-3 flex flex-col gap-1 text-[11px] font-medium text-ink">
                  机器人使用的数据源（必选）
                  <select
                    className={INPUT_CLASS}
                    value={binding.sourceId ?? ""}
                    onChange={handleSelectBindingSource}
                    disabled={sourceSubmitting || bindingSources.length === 0}
                  >
                    <option value="">请选择已授权数据源</option>
                    {bindingSources.map((source) => (
                      <option key={source.id} value={source.id}>
                        {source.name}
                      </option>
                    ))}
                  </select>
                </label>
              )}

              {error && (
                <div
                  className="mb-2 rounded-tag border border-red/30 bg-red/10 px-2.5 py-1.5 text-xs text-red"
                  role="alert"
                >
                  {error}
                </div>
              )}

              <button
                type="button"
                className="mt-1 h-9 w-full rounded-tag border border-primary/25 bg-primary/5 text-xs font-semibold text-primary transition hover:bg-primary/10 disabled:cursor-not-allowed disabled:opacity-60"
                onClick={() => handleBindProvider()}
                disabled={bindingSubmitting || bindingLoading || !selectedProvider}
              >
                {bindingSubmitting
                  ? "正在打开授权页面…"
                  : binding?.bound
                    ? `重新授权${selectedProvider === "feishu" ? "飞书" : "钉钉"}账号`
                    : selectedProvider
                      ? `前往${selectedProvider === "feishu" ? "飞书" : "钉钉"}完成授权`
                      : "请先选择绑定平台"}
              </button>
              <Button
                variant="primary"
                size="md"
                type="button"
                className="mt-2 w-full"
                disabled={
                  !binding?.bound ||
                  !binding?.sourceId ||
                  bindingLoading ||
                  bindingSubmitting ||
                  sourceSubmitting
                }
                onClick={handleFinishRegistration}
              >
                {bindingLoading || bindingSubmitting ? "正在确认绑定状态…" : "完成注册"}
              </Button>
              {!binding?.bound && (
                <p className="mb-0 mt-2 text-center text-[10px] text-faint">
                  在第三方账号绑定成功前，无法进入业务工作台。
                </p>
              )}
            </section>
            )}

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
          </div>
          </Card>

          {activeUser && (
            <p className="login-page__active-user">
              当前已登录 <b>{activeUser.displayName || activeUser.username}</b>，重新登录将切换账号
            </p>
          )}
        </section>
      </div>
    </main>
  );
}
