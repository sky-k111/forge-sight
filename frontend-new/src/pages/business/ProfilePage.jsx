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
import { Button, Card, EmptyState, ErrorNotice, Icon, Modal, PageFrame, RouteSkeleton, StatusPill } from "../../components/ui";
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
    mark: "钉",
  },
  feishu: {
    label: "飞书",
    description: "接收机器人消息、分析提醒与报告投递",
    tone: "green",
    mark: "飞",
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

  const load = async () => {
    setLoading(true);
    setError("");
    const [dingtalk, feishu, settings, memoryItems] = await Promise.all([
      getDingTalkBinding().catch(() => null),
      getFeishuBinding().catch(() => null),
      getMemorySettings().catch(() => null),
      listMemories().catch(() => []),
    ]);
    setBindings({ dingtalk: dingtalk ?? { bound: false }, feishu: feishu ?? { bound: false } });
    setMemorySettings(settings && !Array.isArray(settings) ? settings : { enabled: false });
    const normalizedMemories = Array.isArray(memoryItems) ? memoryItems : memoryItems?.items ?? [];
    setMemories(normalizedMemories.filter((item) => !["deleted", "superseded"].includes(item.status)));
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
      <section className="profile-hero overflow-hidden rounded-panel border border-line bg-surface shadow-card">
        <div className="profile-hero__identity">
          <span className="profile-hero__eyebrow">PERSONAL SPACE / 01</span>
          <div className="mt-5 flex items-center gap-4">
            <span className="profile-avatar" aria-hidden="true">{initials}</span>
            <div className="min-w-0">
              <h2 className="truncate text-2xl font-semibold tracking-tight text-ink">{displayName}</h2>
              <p className="mt-1 text-sm text-muted">{user.title || "业务人员"}</p>
              <p className="mt-1 font-mono text-[11px] text-faint">@{user.username}</p>
            </div>
          </div>
          <div className="mt-6 flex flex-wrap items-center gap-2">
            <StatusPill tone="green">账号正常</StatusPill>
            <span className="text-[11px] text-faint">偏好和记忆仅对当前账号生效</span>
          </div>
        </div>
        <div className="profile-hero__signal" aria-hidden="true">
          <span className="profile-hero__signal-ring profile-hero__signal-ring--outer" />
          <span className="profile-hero__signal-ring profile-hero__signal-ring--inner" />
          <Icon name="user" size={28} />
          <small>ACCOUNT / PRIVATE</small>
        </div>
      </section>

      <section className="profile-grid">
        <Card className="profile-card profile-llm-card overflow-hidden xl:col-span-2">
          <div className="flex flex-wrap items-start justify-between gap-4 border-b border-line px-5 py-5">
            <ProfileCardHeader eyebrow="模型服务" title="LLM Key 配置" description="管理智能分析使用的模型服务。密钥由服务端托管，不会在浏览器或分析记录中展示。" icon="cpu" compact />
            <StatusPill tone="blue">服务端托管</StatusPill>
          </div>
          <div className="grid gap-5 px-5 py-5 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center">
            <div className="min-w-0">
              <div className="flex items-start gap-3">
                <span className="profile-llm-card__icon" aria-hidden="true"><Icon name="cpu" size={19} /></span>
                <div className="min-w-0">
                  <strong className="block text-sm font-semibold text-ink">OpenAI Compatible 模型服务</strong>
                  <p className="mt-1 text-[11px] leading-5 text-muted">配置完成后，智能分析、语义理解与结果解释会使用该服务。</p>
                </div>
              </div>
              <div className="profile-llm-card__meta mt-4 grid gap-2 sm:grid-cols-2">
                <div><span>密钥状态</span><strong>不在浏览器展示</strong></div>
                <div><span>生效方式</span><strong>保存后重启后端</strong></div>
              </div>
            </div>
            <Button variant="primary" onClick={() => setLlmSetupOpen(true)} className="justify-self-start lg:justify-self-end">
              配置 LLM Key <Icon name="arrow" size={14} />
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
          <div className="border-t border-line bg-surface-2 px-5 py-3 text-[11px] leading-5 text-muted">
            换绑第三方账号会重新进行平台授权；邮箱换绑需要完成一次验证码验证。
          </div>
        </Card>

        <Card className="profile-card profile-memory-card overflow-hidden">
          <div className="flex items-start justify-between gap-4 border-b border-line px-5 py-5">
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
      <LlmKeySetupModal open={llmSetupOpen} onClose={() => setLlmSetupOpen(false)} onCopied={() => setNotice("LLM 配置模板已复制，请在服务端环境中完成配置。")} />
    </PageFrame>
  );
}

function ProfileCardHeader({ eyebrow, title, description, icon, compact = false }) {
  return (
    <div className={compact ? "min-w-0" : "px-5 py-5"}>
      <div className="flex items-center gap-2 text-[10px] font-semibold tracking-[0.08em] text-primary"><Icon name={icon} size={15} />{eyebrow}</div>
      <h3 className="mt-1 text-base font-semibold tracking-tight text-ink">{title}</h3>
      <p className="mt-1 text-[11px] leading-5 text-muted">{description}</p>
    </div>
  );
}

function LlmKeySetupModal({ open, onClose, onCopied }) {
  const copyTemplate = async () => {
    const template = [
      "LLM_BASE_URL=https://api.deepseek.com",
      "LLM_MODEL=deepseek-chat",
      "LLM_API_KEY=请填入你的服务商密钥",
    ].join("\n");
    try {
      await navigator.clipboard.writeText(template);
      onCopied();
    } catch {
      // 剪贴板不可用时仍保留可复制的文本块，避免阻断配置流程。
    }
  };

  return (
    <Modal open={open} onClose={onClose} label="配置 LLM Key" maxWidth={560}>
      <div className="p-5 sm:p-6">
        <div className="flex items-start justify-between gap-4">
          <div className="flex items-start gap-3">
            <span className="profile-llm-modal__icon" aria-hidden="true"><Icon name="cpu" size={18} /></span>
            <div>
              <span className="text-[10px] font-semibold tracking-[0.12em] text-primary">MODEL SERVICE / SECURE SETUP</span>
              <h2 className="mt-1 text-lg font-semibold tracking-tight text-ink">配置 LLM Key</h2>
              <p className="mt-1 text-xs leading-5 text-muted">LLM Key 属于服务端凭据，请在部署环境中配置并由后端统一使用。</p>
            </div>
          </div>
          <button type="button" aria-label="关闭配置 LLM Key" onClick={onClose} className="grid h-8 w-8 shrink-0 cursor-pointer place-items-center rounded-control text-muted transition-colors hover:bg-surface-2 hover:text-ink"><Icon name="close" size={16} /></button>
        </div>
        <div className="profile-llm-modal__notice mt-5 flex items-start gap-2.5 rounded-control border px-3 py-3 text-xs leading-5">
          <Icon name="shield" size={16} />
          <span>为避免密钥进入浏览器缓存、日志或分析记录，个人主页不直接收集明文 Key。配置只在后端生效。</span>
        </div>
        <div className="mt-5 grid gap-3">
          <div className="flex items-center justify-between gap-3">
            <div>
              <strong className="block text-sm font-semibold text-ink">服务端环境变量</strong>
              <span className="mt-0.5 block text-[11px] text-muted">编辑 <code className="rounded-tag bg-surface-2 px-1 py-0.5 font-mono text-[10px] text-ink-2">backend/.env</code> 后重启 API 服务</span>
            </div>
            <StatusPill tone="gray">仅服务端可见</StatusPill>
          </div>
          <pre className="overflow-x-auto rounded-control border border-line bg-surface-2 px-3 py-3 font-mono text-[11px] leading-6 text-ink-2"><code>{"LLM_BASE_URL=https://api.deepseek.com\nLLM_MODEL=deepseek-chat\nLLM_API_KEY=请填入你的服务商密钥"}</code></pre>
        </div>
        <div className="mt-6 flex flex-wrap justify-end gap-2 border-t border-line pt-4">
          <Button variant="ghost" onClick={copyTemplate}>复制配置模板</Button>
          <Button variant="primary" onClick={onClose}>完成</Button>
        </div>
      </div>
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
      <ProviderMark provider={provider} tone={meta.tone} mark={meta.mark} />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2"><h4 className="text-sm font-semibold text-ink">{meta.label}</h4><StatusPill tone={bound ? "green" : "gray"}>{bound ? "已绑定" : "未绑定"}</StatusPill></div>
        <p className="mt-1 truncate text-[11px] text-muted">{detail}</p>
        <p className="mt-1 truncate text-[10px] text-faint">{meta.description}</p>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        {bound && onUnbind && <Button size="sm" variant="ghost" onClick={() => onUnbind(provider)} disabled={busyThis}>解绑</Button>}
        <Button size="sm" onClick={() => onBind(provider)} disabled={busyThis}>{busyThis ? "处理中…" : bound ? "换绑" : "绑定"}</Button>
      </div>
    </article>
  );
}

function ProviderMark({ provider, tone, mark }) {
  return <span className={`profile-provider-mark profile-provider-mark--${tone}`} data-provider={provider}>{provider === "email" ? <Icon name="mail" size={17} /> : mark}</span>;
}

function MemoryPreview({ item }) {
  return (
    <article className="profile-memory-item">
      <span className="grid h-8 w-8 shrink-0 place-items-center rounded-control bg-primary-soft text-primary"><Icon name={item.kind === "preference" ? "settings" : "memory"} size={15} /></span>
      <div className="min-w-0"><div className="flex items-center gap-2"><h4 className="truncate text-xs font-semibold text-ink">{item.title}</h4><span className="shrink-0 text-[10px] text-faint">{memoryKindLabels[item.kind] || "记忆"}</span></div><p className="mt-1 line-clamp-2 text-[11px] leading-5 text-muted">{item.content}</p></div>
    </article>
  );
}
