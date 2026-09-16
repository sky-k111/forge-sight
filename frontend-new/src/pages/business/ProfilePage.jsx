import { useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import {
  getDingTalkBinding,
  getFeishuBinding,
  startDingTalkBind,
  startFeishuBind,
  unbindDingTalk,
  unbindFeishu,
} from "../../api/auth";
import { getMemorySettings, listMemories } from "../../api/memory";
import { discoverLlmModels, getLlmSettings, updateLlmSettings } from "../../api/modelSettings";
import { Button, Card, EmptyState, ErrorNotice, Icon, Modal, PageFrame, RouteSkeleton, StatusPill } from "../../components/ui";
import feishuLogo from "../../assets/feishu-logo.svg";
import { getCachedUser } from "../../lib/auth";

const fallbackUser = {
  username: "local-test-user",
  displayName: "123",
  title: "业务人员",
  emailMasked: null,
  emailStatus: "unbound",
};

const providerMeta = {
  dingtalk: {
    label: "钉钉",
    description: "接收机器人消息、分析提醒与报告投递",
    tone: "blue",
  },
  feishu: {
    label: "飞书",
    description: "接收机器人消息、分析提醒与报告投递",
    tone: "green",
  },
  email: {
    label: "邮箱",
    description: "接收周报、公告和重要的安全通知",
    tone: "gray",
    mark: "@",
  },
};

const memoryKindLabels = {
  preference: "个人偏好",
  episodic: "调查经验",
  semantic: "事实记忆",
  procedural: "分析方法",
};

const emptyLlmSettings = {
  configured: false,
  baseUrl: "",
  model: "",
  maskedKey: "",
  activeProfileId: null,
  activeKeyId: null,
  protocol: "openai_compatible",
  authMode: "auto",
  profiles: [],
};

const llmProtocolLabels = {
  openai_compatible: "OpenAI Chat Completions",
  anthropic_messages: "Anthropic Messages",
};

const compactLlmProtocolLabels = {
  openai_compatible: "OpenAI Chat",
  anthropic_messages: "Anthropic",
};

function normalizeLlmSettings(value) {
  if (!value || typeof value !== "object") return emptyLlmSettings;
  const profiles = Array.isArray(value.profiles)
    ? value.profiles
      .filter((profile) => profile && typeof profile === "object" && profile.id)
      .map((profile) => ({ protocol: "openai_compatible", authMode: "auto", ...profile }))
    : [];
  return { ...emptyLlmSettings, ...value, profiles };
}

export default function ProfilePage() {
  const navigate = useNavigate();
  const location = useLocation();
  const [user] = useState(() => ({ ...fallbackUser, ...(getCachedUser() ?? {}) }));
  const [bindings, setBindings] = useState({ dingtalk: null, feishu: null });
  const [memories, setMemories] = useState([]);
  const [memorySettings, setMemorySettings] = useState({ enabled: false });
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [llmSetupOpen, setLlmSetupOpen] = useState(false);
  const [llmSettings, setLlmSettings] = useState(emptyLlmSettings);

  const load = async () => {
    setLoading(true);
    setError("");
    const [dingtalk, feishu, settings, memoryItems, llm] = await Promise.all([
      getDingTalkBinding().catch(() => null),
      getFeishuBinding().catch(() => null),
      getMemorySettings().catch(() => null),
      listMemories().catch(() => []),
      getLlmSettings().catch(() => null),
    ]);
    setBindings({ dingtalk: dingtalk ?? { bound: false }, feishu: feishu ?? { bound: false } });
    setMemorySettings(settings && !Array.isArray(settings) ? settings : { enabled: false });
    const normalizedMemories = Array.isArray(memoryItems) ? memoryItems : memoryItems?.items ?? [];
    setMemories(normalizedMemories.filter((item) => !["deleted", "superseded"].includes(item.status)));
    setLlmSettings(normalizeLlmSettings(llm));
    setLoading(false);
  };

  useEffect(() => {
    load();
  }, []);

  useEffect(() => {
    const params = new URLSearchParams(location.search);
    const provider = params.get("dingtalkBind") ? "钉钉" : params.get("feishuBind") ? "飞书" : "";
    const status = params.get("dingtalkBind") || params.get("feishuBind");
    if (!provider || !status) return;
    setNotice(status === "success" ? `${provider}账号绑定成功，已同步到个人主页。` : `${provider}账号绑定未完成：${params.get("message") || "请重新尝试授权。"}`);
    navigate("/workspace/profile", { replace: true });
  }, [location.search, navigate]);

  const memoryCounts = useMemo(() => memories.reduce((counts, item) => ({ ...counts, [item.kind]: (counts[item.kind] ?? 0) + 1 }), {}), [memories]);
  const displayName = user.displayName || user.username;
  const initials = displayName.charAt(0).toUpperCase();
  const activeLlmProfile = llmSettings.profiles.find((profile) => profile.id === llmSettings.activeProfileId)
    || llmSettings.profiles.find((profile) => profile.active);

  const bindProvider = async (provider) => {
    if (provider === "email") {
      navigate("/verify-email", { state: { from: "/workspace/profile", allowEmailChange: true } });
      return;
    }
    setBusy(provider);
    setError("");
    try {
      const body = provider === "feishu" ? await startFeishuBind("/workspace/profile") : await startDingTalkBind("/workspace/profile");
      if (!body?.authUrl) throw new Error(`未获取到${provider === "feishu" ? "飞书" : "钉钉"}授权地址，请检查后端配置`);
      window.location.href = body.authUrl;
    } catch (reason) {
      setError(reason.message || "暂时无法发起账号授权");
    } finally {
      setBusy("");
    }
  };

  const unbindProvider = async (provider) => {
    const label = providerMeta[provider].label;
    if (!window.confirm(`确认解绑${label}账号吗？解绑后将停止该渠道的消息投递。`)) return;
    setBusy(`unbind-${provider}`);
    setError("");
    try {
      if (provider === "feishu") await unbindFeishu();
      else await unbindDingTalk();
      setNotice(`${label}账号已解绑。`);
      await load();
    } catch (reason) {
      setError(reason.message || `${label}账号解绑失败`);
    } finally {
      setBusy("");
    }
  };

  if (loading) return <RouteSkeleton />;

  return (
    <PageFrame className="profile-page">
      <section className="profile-hero flex flex-wrap items-center gap-3 rounded-panel border border-line bg-surface px-4 py-3">
        <span className="profile-avatar" aria-hidden="true">{initials}</span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="truncate text-base font-semibold tracking-tight text-ink">{displayName}</h2>
            <StatusPill tone="green">账号正常</StatusPill>
          </div>
          <p className="mt-0.5 truncate text-[11px] text-muted">{user.title || "业务人员"} · <span className="font-mono text-faint">@{user.username}</span></p>
        </div>
        <span className="hidden shrink-0 text-[11px] text-faint sm:block">偏好和记忆仅对当前账号生效</span>
      </section>

      <section className="profile-grid">
        <Card className="profile-card profile-llm-card overflow-hidden xl:col-span-2">
          <div className="profile-llm-card__header">
            <div className="min-w-0">
              <p className="text-[11px] font-semibold tracking-[0.08em] text-primary">模型服务</p>
              <h3 className="mt-1 text-base font-semibold tracking-tight text-ink">模型配置</h3>
              <p className="mt-1 text-[11px] leading-5 text-muted">保存多个模型服务，发起分析时可按次选择。</p>
            </div>
            <StatusPill tone="blue">服务端托管</StatusPill>
          </div>
          <div className="profile-llm-card__summary">
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <span className={`profile-llm-card__status ${llmSettings.configured ? "is-ready" : ""}`} aria-hidden="true" />
                <strong className="truncate text-sm font-semibold text-ink">{activeLlmProfile?.name || "尚未配置模型服务"}</strong>
              </div>
              <p className="mt-1 truncate pl-3.5 text-[11px] text-muted">{activeLlmProfile?.model || "添加服务地址、模型和 API Key 后即可使用"}</p>
            </div>
            <dl className="profile-llm-card__facts">
              <div><dt>凭据</dt><dd>{llmSettings.configured ? llmSettings.maskedKey || "已配置" : "未配置"}</dd></div>
              <div><dt>模型数</dt><dd>{llmSettings.profiles?.length || 0}</dd></div>
            </dl>
            <Button variant="primary" onClick={() => setLlmSetupOpen(true)}>
              管理配置 <Icon name="arrow" size={14} />
            </Button>
          </div>
        </Card>
      </section>

      {notice && <p className="profile-notice" role="status">{notice}</p>}
      {error && <ErrorNotice title="个人设置暂时无法完成" detail={error} onRetry={load} />}

      <section className="profile-grid">
        <Card className="profile-card overflow-hidden">
          <ProfileCardHeader eyebrow="登录与投递" title="绑定方式" description="管理第三方账号和邮箱，换绑不会影响已有分析记录。" icon="link" />
          <div className="divide-y divide-line">
            <BindingRow provider="dingtalk" binding={bindings.dingtalk} busy={busy} onBind={bindProvider} onUnbind={unbindProvider} />
            <BindingRow provider="feishu" binding={bindings.feishu} busy={busy} onBind={bindProvider} onUnbind={unbindProvider} />
            <BindingRow provider="email" binding={{ bound: user.emailStatus === "verified", displayName: user.emailMasked }} busy={busy} onBind={bindProvider} onUnbind={null} />
          </div>
          <div className="border-t border-line bg-surface-2 px-4 py-2.5 text-[11px] leading-5 text-muted">
            换绑第三方账号会重新进行平台授权；邮箱换绑需要完成一次验证码验证。
          </div>
        </Card>

        <Card className="profile-card profile-memory-card overflow-hidden">
          <div className="flex items-start justify-between gap-4 border-b border-line px-4 py-3.5">
            <ProfileCardHeader eyebrow="分析上下文" title="个人记忆" description="只在与当前问题相关时被召回，随时可以纠正或遗忘。" icon="memory" compact />
            <Button size="sm" variant="ghost" onClick={() => navigate("/workspace/memory")}>管理记忆 <Icon name="arrow" size={13} /></Button>
          </div>
          <div className="profile-memory-summary">
            <div><strong>{memories.length}</strong><span>条已保存内容</span></div>
            <div><strong>{memoryCounts.preference ?? 0}</strong><span>条个人偏好</span></div>
            <div><strong>{memorySettings.enabled ? "ON" : "OFF"}</strong><span>自动学习</span></div>
          </div>
          {memories.length ? (
            <div className="divide-y divide-line">
              {memories.slice(0, 3).map((item) => <MemoryPreview key={item.id} item={item} />)}
            </div>
          ) : (
            <div className="px-5 py-8"><EmptyState title="还没有个人记忆" description="继续发起分析，或前往记忆页补充一条明确偏好。" /></div>
          )}
          <div className="profile-memory-boundary">
            <Icon name="shield" size={15} />
            <span>记忆作用域：账号 + 数据源 · 不会自动变成团队知识或数据库权限</span>
          </div>
        </Card>
      </section>
      <LlmKeySetupModal
        open={llmSetupOpen}
        settings={llmSettings}
        onClose={() => setLlmSetupOpen(false)}
        onSaved={(next, message, close = true) => {
          setLlmSettings(normalizeLlmSettings(next));
          setNotice(message || "LLM 配置已保存，当前 API 进程已立即生效。");
          if (close) setLlmSetupOpen(false);
        }}
      />
    </PageFrame>
  );
}

function ProfileCardHeader({ eyebrow, title, description, icon, compact = false }) {
  return (
    <div className={compact ? "min-w-0" : "px-4 py-3.5"}>
      <div className="flex items-center gap-2 text-[10px] font-semibold tracking-[0.08em] text-primary"><Icon name={icon} size={15} />{eyebrow}</div>
      <h3 className="mt-1 text-base font-semibold tracking-tight text-ink">{title}</h3>
      <p className="mt-1 text-[11px] leading-5 text-muted">{description}</p>
    </div>
  );
}

function LlmKeySetupModal({ open, settings: initialSettings, onClose, onSaved }) {
  const settings = normalizeLlmSettings(initialSettings);
  const [selectedId, setSelectedId] = useState(null);
  const [form, setForm] = useState({ profileName: "", protocol: "openai_compatible", authMode: "auto", baseUrl: "", model: "", apiKey: "", activeKeyId: null });
  const [saving, setSaving] = useState(false);
  const [switchingId, setSwitchingId] = useState(null);
  const [discovering, setDiscovering] = useState(false);
  const [discoveredModels, setDiscoveredModels] = useState([]);
  const [discoveryMessage, setDiscoveryMessage] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    if (!open) return;
    const active = settings.profiles.find((profile) => profile.id === settings.activeProfileId)
      || settings.profiles.find((profile) => profile.active)
      || settings.profiles[0];
    setSelectedId(active?.id || null);
    setForm({
      profileName: active?.name || "",
      protocol: active?.protocol || "openai_compatible",
      authMode: active?.authMode || "auto",
      baseUrl: active?.baseUrl || settings.baseUrl || "",
      model: active?.model || settings.model || "",
      apiKey: "",
      activeKeyId: active?.activeKeyId || null,
    });
    setDiscoveredModels([]);
    setDiscoveryMessage("");
    setError("");
  }, [open, settings.activeProfileId, settings.baseUrl, settings.model, settings.profiles.length]);

  const selectedProfile = settings.profiles.find((profile) => profile.id === selectedId) || null;
  const isNewProfile = !selectedProfile;

  const chooseProfile = (profile) => {
    setSelectedId(profile.id);
    setForm({ profileName: profile.name || "", protocol: profile.protocol || "openai_compatible", authMode: profile.authMode || "auto", baseUrl: profile.baseUrl || "", model: profile.model || "", apiKey: "", activeKeyId: profile.activeKeyId || null });
    setDiscoveredModels([]);
    setDiscoveryMessage("");
    setError("");
  };

  const startNewProfile = () => {
    setSelectedId(null);
    setForm({ profileName: "", protocol: "openai_compatible", authMode: "auto", baseUrl: "https://api.deepseek.com", model: "deepseek-chat", apiKey: "", activeKeyId: null });
    setDiscoveredModels([]);
    setDiscoveryMessage("");
    setError("");
  };

  const activateProfile = async (profile) => {
    chooseProfile(profile);
    if (profile.active) return;
    setSwitchingId(profile.id);
    setError("");
    try {
      const next = await updateLlmSettings({ profileId: profile.id, activate: true });
      onSaved(normalizeLlmSettings(next), `${profile.name || profile.model} 已切换并生效。`, false);
    } catch (reason) {
      setError(reason.message || "模型配置切换失败");
    } finally {
      setSwitchingId(null);
    }
  };

  const deleteProfile = async () => {
    if (!selectedProfile || selectedProfile.active) return;
    if (!window.confirm(`确认删除“${selectedProfile.name || selectedProfile.model}”吗？`)) return;
    setSaving(true);
    setError("");
    try {
      const next = await updateLlmSettings({ profileId: selectedProfile.id, delete: true });
      onSaved(normalizeLlmSettings(next), "模型配置已删除。", false);
    } catch (reason) {
      setError(reason.message || "模型配置删除失败");
    } finally {
      setSaving(false);
    }
  };

  const save = async (event) => {
    event.preventDefault();
    setSaving(true);
    setError("");
    try {
      if (!form.profileName.trim()) {
        setError("请给这个模型配置起一个容易识别的名称");
        setSaving(false);
        return;
      }
      const replacementKey = form.apiKey.trim();
      const payload = {
        profileId: selectedId || undefined,
        profileName: form.profileName.trim(),
        protocol: form.protocol,
        authMode: form.authMode,
        baseUrl: form.baseUrl,
        model: form.model,
        apiKey: replacementKey || undefined,
        clearKey: Boolean(selectedProfile && replacementKey),
        activeKeyId: replacementKey ? undefined : form.activeKeyId || undefined,
        activate: true,
      };
      const next = await updateLlmSettings(payload);
      onSaved(normalizeLlmSettings(next), `${form.profileName.trim()} 已保存并生效。`);
    } catch (reason) {
      setError(reason.message || "LLM 配置保存失败");
    } finally {
      setSaving(false);
    }
  };

  const detectModels = async () => {
    setDiscovering(true);
    setError("");
    setDiscoveryMessage("");
    try {
      const newKey = form.apiKey.trim();
      const result = await discoverLlmModels({
        baseUrl: form.baseUrl,
        protocol: form.protocol,
        authMode: form.authMode,
        profileId: selectedId || undefined,
        apiKey: newKey || undefined,
        activeKeyId: form.activeKeyId || undefined,
      });
      const models = Array.isArray(result?.models) ? result.models : [];
      setDiscoveredModels(models);
      setDiscoveryMessage(models.length ? `已检测到 ${models.length} 个模型，请选择或继续手动填写。` : (result?.message || "未检测到模型"));
      if (models.length === 1) setForm((current) => ({ ...current, model: models[0] }));
    } catch (reason) {
      setError(reason.message || "模型列表检测失败");
    } finally {
      setDiscovering(false);
    }
  };

  const copyTemplate = async () => {
    const template = [
      `LLM_BASE_URL=${form.baseUrl || "https://api.deepseek.com"}`,
      `LLM_MODEL=${form.model || "deepseek-v4-flash"}`,
      `LLM_PROTOCOL=${form.protocol}`,
      `LLM_AUTH_MODE=${form.authMode}`,
      "LLM_API_KEY=请填入你的服务商密钥",
    ].join("\n");
    try {
      await navigator.clipboard.writeText(template);
    } catch {
      // 浏览器剪贴板不可用时不阻断直接配置流程。
    }
  };

  return (
    <Modal open={open} onClose={onClose} label="管理模型配置" maxWidth={820} dismissOnBackdrop={false}>
      <form className="p-5 sm:p-6" onSubmit={save}>
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="text-lg font-semibold tracking-tight text-ink">管理模型配置</h2>
            <p className="mt-1 text-xs leading-5 text-muted">点击已保存配置即可切换；编辑完成后再保存。</p>
          </div>
          <button type="button" aria-label="关闭模型配置" onClick={onClose} className="grid h-8 w-8 shrink-0 cursor-pointer place-items-center rounded-control text-muted transition-colors hover:bg-surface-2 hover:text-ink"><Icon name="close" size={16} /></button>
        </div>
        <div className="profile-llm-modal__notice mt-4 flex items-start gap-2.5 border-y px-1 py-2.5 text-[11px] leading-5">
          <Icon name="shield" size={16} />
          <span>明文 Key 只用于本次提交，页面不会回显已保存的 Key；后端仅返回脱敏状态。</span>
        </div>
        <div className="mt-5 grid gap-5 md:grid-cols-[220px_minmax(0,1fr)]">
          <aside className="min-w-0 overflow-hidden rounded-card bg-surface-2 p-2.5">
            <div className="flex items-center justify-between gap-3">
              <span className="px-1 text-xs font-semibold text-ink">已保存模型</span>
              <Button size="sm" variant="ghost" type="button" onClick={startNewProfile}>新增</Button>
            </div>
            {settings.profiles.length ? <div className="mt-2 grid gap-1">
              {settings.profiles.map((profile) => <button key={profile.id} type="button" onClick={() => activateProfile(profile)} disabled={Boolean(switchingId)} className={`group flex w-full min-w-0 items-center gap-2 overflow-hidden rounded-control px-2.5 py-2.5 text-left transition-colors disabled:cursor-wait disabled:opacity-60 ${profile.id === selectedId ? "bg-surface text-ink shadow-[0_1px_3px_rgb(24_35_41_/8%)]" : "text-muted hover:bg-surface/70 hover:text-ink"}`}>
                <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${profile.active ? "bg-green" : "bg-line-strong"}`} aria-hidden="true" />
                <span className="min-w-0 flex-1 overflow-hidden"><strong className="block truncate text-xs">{profile.name || profile.model}</strong><span className="mt-0.5 block truncate text-[10px] text-faint" title={`${profile.model} · ${llmProtocolLabels[profile.protocol] || profile.protocol}`}>{profile.model} · {compactLlmProtocolLabels[profile.protocol] || profile.protocol}</span></span>
                <span className="shrink-0 text-[10px] font-semibold text-primary">{switchingId === profile.id ? "切换中" : profile.active ? "当前" : ""}</span>
              </button>)}
            </div> : <p className="mt-2 text-[11px] text-muted">还没有配置档，请新增一个模型服务。</p>}
          </aside>
          <div className="grid content-start gap-3">
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="grid gap-1.5 text-xs font-semibold text-ink">配置名称<input className="input-control" value={form.profileName} onChange={(event) => setForm((current) => ({ ...current, profileName: event.target.value }))} placeholder="例如：DeepSeek 生产" required /></label>
              <label className="grid gap-1.5 text-xs font-semibold text-ink">API 协议<select className="input-control" value={form.protocol} onChange={(event) => { const protocol = event.target.value; setForm((current) => ({ ...current, protocol, authMode: "auto" })); }}><option value="openai_compatible">OpenAI Chat Completions</option><option value="anthropic_messages">Anthropic Messages</option></select></label>
            </div>
            <label className="grid gap-1.5 text-xs font-semibold text-ink">服务地址<input className="input-control" value={form.baseUrl} onChange={(event) => setForm((current) => ({ ...current, baseUrl: event.target.value }))} placeholder={form.protocol === "anthropic_messages" ? "https://api.anthropic.com" : "https://api.deepseek.com"} required /></label>
            <div className="grid gap-1.5 text-xs font-semibold text-ink"><label htmlFor="llm-auth-mode">认证方式</label><select id="llm-auth-mode" className="input-control" value={form.authMode} onChange={(event) => { const authMode = event.target.value; setForm((current) => ({ ...current, authMode })); }}><option value="auto">自动（推荐）</option><option value="x_api_key">x-api-key</option><option value="bearer">Authorization: Bearer</option></select><span className="text-[11px] font-normal leading-5 text-muted">{form.protocol === "anthropic_messages" ? "直连 Anthropic 时自动使用 x-api-key；仅在网关明确要求时改为 Bearer。" : "OpenAI 兼容服务默认使用 Bearer；仅在服务商明确要求时改为 x-api-key。"}</span></div>
            <div className="grid gap-1.5">
              <div className="flex items-center justify-between gap-3"><label htmlFor="llm-model-name" className="text-xs font-semibold text-ink">模型名称</label><Button size="sm" variant="ghost" type="button" onClick={detectModels} disabled={discovering || !form.baseUrl.trim()}>{discovering ? "正在检测…" : "检测模型"}</Button></div>
              <input id="llm-model-name" className="input-control" value={form.model} onChange={(event) => setForm((current) => ({ ...current, model: event.target.value }))} placeholder="deepseek-v4-flash" required />
              {discoveredModels.length > 0 && (
                <div className="max-h-40 overflow-y-auto rounded-control border border-line bg-surface p-1" role="listbox" aria-label="检测到的模型">
                  {discoveredModels.map((model) => (
                    <button key={model} type="button" role="option" aria-selected={form.model === model} onClick={() => setForm((current) => ({ ...current, model }))} className={`flex w-full items-center justify-between rounded-control px-2.5 py-2 text-left text-xs transition-colors ${form.model === model ? "bg-primary-soft font-semibold text-ink" : "text-muted hover:bg-surface-2 hover:text-ink"}`}>
                      <span className="truncate">{model}</span>
                      {form.model === model && <Icon name="check" size={14} className="shrink-0 text-primary" />}
                    </button>
                  ))}
                </div>
              )}
              {discoveryMessage && <p className="text-[11px] leading-5 text-green" role="status">{discoveryMessage}</p>}
            </div>
            {selectedProfile?.configured && <div className="rounded-control bg-surface-2 px-3 py-2.5 text-xs"><span className="text-muted">当前凭据</span><strong className="ml-2 font-mono text-ink">{selectedProfile.maskedKey || "已安全配置"}</strong></div>}
            <label className="grid gap-1.5 text-xs font-semibold text-ink">{selectedProfile?.configured ? "替换 API Key（可选）" : "API Key"}<span className="text-[11px] font-normal leading-5 text-muted">每个模型配置保存一个凭据；留空即保留已有 Key。</span><input className="input-control font-mono" type="password" value={form.apiKey} onChange={(event) => setForm((current) => ({ ...current, apiKey: event.target.value }))} placeholder={selectedProfile?.configured ? "留空则保留现有 Key" : "sk-…"} autoComplete="new-password" /></label>
            {error && <p className="text-xs text-red" role="alert">{error}</p>}
            <details className="rounded-control border border-line bg-surface-2 px-3 py-2 text-[11px] text-muted"><summary className="cursor-pointer font-semibold text-ink-2">复制为服务端环境变量（可选）</summary><pre className="mt-2 overflow-x-auto font-mono leading-6 text-ink-2"><code>{`LLM_BASE_URL=${form.baseUrl || "https://api.deepseek.com"}\nLLM_MODEL=${form.model || "deepseek-v4-flash"}\nLLM_PROTOCOL=${form.protocol}\nLLM_AUTH_MODE=${form.authMode}\nLLM_API_KEY=请填入你的服务商密钥`}</code></pre></details>
          </div>
        </div>
        <div className="mt-6 flex flex-wrap justify-end gap-2 border-t border-line pt-4">
          <Button variant="ghost" type="button" onClick={copyTemplate}>复制配置模板</Button>
          {selectedProfile && !selectedProfile.active && selectedProfile.id !== "env" && <Button variant="ghost" type="button" onClick={deleteProfile} disabled={saving}>删除配置</Button>}
          <Button variant="secondary" type="button" onClick={onClose}>完成</Button>
          <Button variant="primary" type="submit" disabled={saving}>{saving ? "保存中…" : isNewProfile ? "新增并启用" : "保存并启用"}</Button>
        </div>
      </form>
    </Modal>
  );
}

function BindingRow({ provider, binding, busy, onBind, onUnbind }) {
  const meta = providerMeta[provider];
  const bound = Boolean(binding?.bound);
  const detail = binding?.displayName || binding?.email || binding?.mobile || (bound ? "已授权账号" : "尚未绑定");
  const busyThis = busy === provider || busy === `unbind-${provider}`;
  return (
    <article className="profile-binding-row">
      <ProviderMark provider={provider} tone={meta.tone} />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2"><h4 className="text-sm font-semibold text-ink">{meta.label}</h4><StatusPill tone={bound ? "green" : "gray"}>{bound ? "已绑定" : "未绑定"}</StatusPill></div>
        <p className="mt-1 truncate text-[11px] text-muted">{detail}</p>
        <p className="mt-1 truncate text-[11px] text-faint">{meta.description}</p>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        {bound && onUnbind && <Button size="sm" variant="ghost" onClick={() => onUnbind(provider)} disabled={busyThis}>解绑</Button>}
        <Button size="sm" onClick={() => onBind(provider)} disabled={busyThis}>{busyThis ? "处理中…" : bound ? "换绑" : "绑定"}</Button>
      </div>
    </article>
  );
}

function ProviderMark({ provider, tone }) {
  return (
    <span className={`profile-provider-mark profile-provider-mark--${tone}`} data-provider={provider} aria-hidden="true">
      {provider === "email" ? (
        <Icon name="mail" size={17} />
      ) : provider === "dingtalk" ? (
        <svg viewBox="0 0 1024 1024" className="profile-provider-logo profile-provider-logo--dingtalk" focusable="false">
          <path d="M573.7 252.5C422.5 197.4 201.3 96.7 201.3 96.7c-15.7-4.1-17.9 11.1-17.9 11.1-5 61.1 33.6 160.5 53.6 182.8 19.9 22.3 319.1 113.7 319.1 113.7S326 357.9 270.5 341.9c-55.6-16-37.9 17.8-37.9 17.8 11.4 61.7 64.9 131.8 107.2 138.4 42.2 6.6 220.1 4 220.1 4s-35.5 4.1-93.2 11.9c-42.7 5.8-97 12.5-111.1 17.8-33.1 12.5 24 62.6 24 62.6 84.7 76.8 129.7 50.5 129.7 50.5 33.3-10.7 61.4-18.5 85.2-24.2L565 743.1h84.6L603 928l205.3-271.9H700.8l22.3-38.7c.3.5.4.8.4.8S799.8 496.1 829 433.8l.6-1h-.1c5-10.8 8.6-19.7 10-25.8 17-71.3-114.5-99.4-265.8-154.5z" />
        </svg>
      ) : (
        <img src={feishuLogo} alt="" className="profile-provider-logo profile-provider-logo--feishu" />
      )}
    </span>
  );
}

function MemoryPreview({ item }) {
  return (
    <article className="profile-memory-item">
      <span className="grid h-8 w-8 shrink-0 place-items-center rounded-control bg-primary-soft text-primary"><Icon name={item.kind === "preference" ? "settings" : "memory"} size={15} /></span>
      <div className="min-w-0"><div className="flex items-center gap-2"><h4 className="truncate text-xs font-semibold text-ink">{item.title}</h4><span className="shrink-0 text-[10px] text-faint">{memoryKindLabels[item.kind] || "记忆"}</span></div><p className="mt-1 line-clamp-2 text-[11px] leading-5 text-muted">{item.content}</p></div>
    </article>
  );
}
