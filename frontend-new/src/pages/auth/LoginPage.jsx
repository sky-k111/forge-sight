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
  unbindDingTalk,
  unbindFeishu,
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
        "flex h-9 w-9 shrink-0 items-center justify-center rounded-control text-sm font-bold text-white",
        isDingTalk ? "bg-[#1677ff]" : "bg-[#00b96b]"
      )}
      aria-hidden="true"
    >
      {isDingTalk ? "钉" : "飞"}
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

  const handleUnbindProvider = async () => {
    const providerName = selectedProvider === "feishu" ? "飞书" : "钉钉";
    if (
      !window.confirm(`解绑后，${providerName}机器人将无法继续读取该账号的会话上下文。确定解绑吗？`)
    ) {
      return;
    }
    setBindingSubmitting(true);
    setError(null);
    try {
      if (selectedProvider === "feishu") {
        await unbindFeishu();
        setFeishuBinding({ bound: false });
      } else {
        await unbindDingTalk();
        setDingTalkBinding({ bound: false });
      }
      setNotice({ type: "success", text: `${providerName}账号已解绑` });
    } catch (unbindError) {
      setError(unbindError.message || `${providerName}账号解绑失败`);
    } finally {
      setBindingSubmitting(false);
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

  return (
    <div className="flex min-h-full items-center justify-center bg-canvas px-4 py-10">
      <div className="w-full max-w-[460px]">
        <div className="mb-6 flex flex-col items-center gap-2.5">
          <img
            className="h-14 w-14 rounded-control border border-line bg-surface object-contain p-1.5 shadow-card"
            src={forgeSightLogo}
            alt="ForgeSight logo"
          />
          <h1 className="m-0 text-xl font-semibold tracking-tight text-ink">
            ForgeSight 企业数据底座
          </h1>
          <p className="m-0 text-xs text-muted">面向制造业的智能问析与数据治理平台</p>
        </div>

        <Card className="p-5">
          <div
            className="mb-4 flex items-center gap-0.5 rounded-control border border-line bg-surface-2 p-0.5"
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

              {cachedUser && mode === "login" && (
                <div className="rounded-control border border-line bg-surface-2 px-3 py-3">
                  <div className="text-xs font-semibold text-ink">第三方账号绑定</div>
                  <p className="mt-1 text-[11px] leading-5 text-muted">
                    绑定后，机器人会按当前账号的数据权限执行问析，并为每个会话保留连续上下文。
                  </p>
                  <div className="mt-2 flex gap-2">
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
                  <p className="mt-2 text-[11px] text-faint">{bindingText}</p>
                  {binding?.bound && (
                    <label className="mt-2 flex flex-col gap-1 text-[11px] font-medium text-ink">
                      机器人使用的数据源
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
                  <button
                    type="button"
                    className="mt-2 h-8 w-full rounded-tag border border-primary/25 bg-primary/5 text-xs font-semibold text-primary transition hover:bg-primary/10 disabled:cursor-not-allowed disabled:opacity-60"
                    onClick={() => handleBindProvider()}
                    disabled={bindingSubmitting || !selectedProvider}
                  >
                    {bindingSubmitting
                      ? "正在打开授权页面…"
                      : binding?.bound
                        ? `重新绑定${selectedProvider === "feishu" ? "飞书" : "钉钉"}账号`
                        : selectedProvider
                          ? `绑定${selectedProvider === "feishu" ? "飞书" : "钉钉"}账号`
                          : "请先选择绑定平台"}
                  </button>
                  {binding?.bound && (
                    <button
                      type="button"
                      className="mt-2 h-8 w-full rounded-tag border border-line bg-surface text-xs font-medium text-muted transition hover:text-red disabled:opacity-60"
                      onClick={handleUnbindProvider}
                      disabled={bindingSubmitting}
                    >
                      解绑{selectedProvider === "feishu" ? "飞书" : "钉钉"}账号
                    </button>
                  )}
                </div>
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
        </Card>

        {activeUser && (
          <p className="mt-4 text-center text-[11px] text-faint">
            当前已登录 <b className="text-ink">{activeUser.displayName || activeUser.username}</b>
            ，重新登录将切换账号
          </p>
        )}
      </div>
    </div>
  );
}
