import { useEffect, useMemo, useState } from "react";
import { useOutletContext } from "react-router-dom";
import Icon from "../../components/ui/Icon";
import cn from "../../lib/cn";
import { Button, Card, EmptyState, ErrorNotice, PageFrame, RouteSkeleton, StatusPill } from "../../components/ui";
import { listSources } from "../../api/datasources";
import { getBusinessOverview } from "../../api/overview";
import { currentUserId } from "../../lib/auth";

const ACTIVE_SOURCE_KEY = "forgesight:active-source-id";
const ANALYSIS_DRAFT_KEY = "forgesight:analysis-draft";
const ANALYSIS_SESSION_KEY = "forgesight:analysis-session-id";
const USER_ID = currentUserId;

const topicTones = { blue: "bg-primary", teal: "bg-teal", amber: "bg-amber", violet: "bg-violet" };
const capabilityTones = { ready: "green", catalog_only: "blue", offline: "red", empty: "amber" };
const snapshotTones = {
  green: { tile: "bg-success-soft text-success", value: "text-success", surface: "bg-success-soft/35" },
  blue: { tile: "bg-primary-soft text-primary", value: "text-primary", surface: "bg-primary-soft/30" },
  violet: { tile: "bg-violet/10 text-violet", value: "text-violet", surface: "bg-violet/5" },
  amber: { tile: "bg-amber-soft text-amber", value: "text-amber", surface: "bg-amber-soft/35" },
};
const quickStartTones = [
  { icon: "cpu", className: "bg-primary-soft text-primary" },
  { icon: "database", className: "bg-teal-soft text-teal" },
  { icon: "insight", className: "bg-amber-soft text-amber" },
  { icon: "shield", className: "bg-violet/10 text-violet" },
];
const runLabels = {
  queued: "排队中", running: "分析中", waiting_user: "待补充", succeeded: "已完成",
  failed: "失败", blocked: "未执行", cancelled: "已取消", "未开始": "未开始",
};

function formatTime(value) {
  if (!value) return "暂无记录";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function sourceTypeLabel(type) {
  return {
    mysql: "MySQL",
    postgresql: "PostgreSQL",
    mariadb: "MariaDB",
    sqlserver: "SQL Server",
    oracle: "Oracle",
  }[type] ?? String(type || "数据源").toUpperCase();
}

function compactNumber(value) {
  return new Intl.NumberFormat("zh-CN", { notation: Number(value) >= 100000 ? "compact" : "standard", maximumFractionDigits: 1 }).format(Number(value) || 0);
}

function buildAttentionItems({ capability, snapshot, metrics, recent }) {
  const items = [];
  recent.items.filter((item) => ["failed", "blocked", "waiting_user"].includes(item.status)).slice(0, 2).forEach((item) => {
    items.push({
      id: `run-${item.id}`,
      tone: item.status === "waiting_user" ? "amber" : "red",
      icon: item.status === "waiting_user" ? "message" : "alert",
      eyebrow: item.status === "waiting_user" ? "等待补充条件" : "调查未完成",
      title: item.title,
      detail: `${formatTime(item.updatedAt)} · 已执行 ${item.runCount} 次`,
      action: "继续调查",
      sessionId: item.id,
    });
  });

  if (!capability.canAnalyze) {
    items.push({ id: "source-unavailable", tone: "red", icon: "database", eyebrow: "当前范围不可分析", title: capability.label, detail: capability.detail });
  } else if (capability.state === "catalog_only") {
    items.push({ id: "catalog-only", tone: "amber", icon: "knowledge", eyebrow: "分析口径尚不完整", title: "当前可探索原始数据，正式指标结论需谨慎使用", detail: capability.detail });
  }

  if (snapshot.registeredTables > 0 && snapshot.datedTables < snapshot.registeredTables) {
    items.push({
      id: "date-coverage", tone: "amber", icon: "clock", eyebrow: "数据时间范围待确认",
      title: `${snapshot.registeredTables - snapshot.datedTables} 张表尚未识别最近数据日期`,
      detail: "涉及跨期比较时，系统会在结果中披露数据覆盖限制。",
    });
  }

  if (metrics.pendingBindings > 0) {
    items.push({
      id: "metric-pending", tone: "blue", icon: "knowledge", eyebrow: "部分指标暂未开放",
      title: `${metrics.pendingBindings} 个指标口径仍在验证`, detail: "已发布且完成数据绑定的指标仍可正常问析。",
    });
  }

  if (!items.length) {
    items.push({
      id: "all-ready", tone: "green", icon: "check", eyebrow: "当前工作状态",
      title: "没有需要立即处理的数据准备问题", detail: "数据源、可信指标和分析范围均可正常使用。",
    });
  }
  return items.slice(0, 4);
}

export default function OverviewPage() {
  const { navigatePage } = useOutletContext();
  const [data, setData] = useState(null);
  const [sources, setSources] = useState([]);
  const [sourceId, setSourceId] = useState(() => Number(window.sessionStorage.getItem(ACTIVE_SOURCE_KEY)) || null);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [error, setError] = useState(null);

  const load = async () => {
    setError(null);
    setData(null);
    try {
      const availableSources = await listSources();
      const preferred = availableSources.find((item) => item.id === sourceId) ?? availableSources.find((item) => item.isDefault) ?? availableSources[0];
      if (!preferred) throw new Error("尚未配置可用数据源");
      const overview = await getBusinessOverview(preferred.id, USER_ID());
      setSources(availableSources);
      setSourceId(preferred.id);
      window.sessionStorage.setItem(ACTIVE_SOURCE_KEY, String(preferred.id));
      setData(overview);
    } catch (requestError) {
      setError(requestError);
    }
  };

  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const selectSource = async (nextSource) => {
    setPickerOpen(false);
    if (nextSource.id === sourceId) return;
    window.sessionStorage.setItem(ACTIVE_SOURCE_KEY, String(nextSource.id));
    window.sessionStorage.removeItem(ANALYSIS_SESSION_KEY);
    setSourceId(nextSource.id);
    setError(null);
    setData(null);
    try { setData(await getBusinessOverview(nextSource.id, USER_ID())); } catch (requestError) { setError(requestError); }
  };

  const startAnalysis = (prompt = "", sessionId = "") => {
    if (!data?.capability?.canAnalyze) return;
    window.sessionStorage.setItem(ACTIVE_SOURCE_KEY, String(data.source.id));
    if (prompt) window.sessionStorage.setItem(ANALYSIS_DRAFT_KEY, prompt); else window.sessionStorage.removeItem(ANALYSIS_DRAFT_KEY);
    if (sessionId) window.sessionStorage.setItem(ANALYSIS_SESSION_KEY, sessionId); else window.sessionStorage.removeItem(ANALYSIS_SESSION_KEY);
    navigatePage("analysis");
  };

  const attentionItems = useMemo(() => (data ? buildAttentionItems(data) : []), [data]);

  if (error) return <PageFrame><ErrorNotice title="今日工作台加载失败" detail={error.message || "请检查数据源连接后重试。"} onRetry={load} /></PageFrame>;
  if (!data) return <RouteSkeleton />;

  const { source, capability, snapshot, metrics, topics, suggestions, recent } = data;
  const statusTone = capabilityTones[capability.state] ?? "gray";

  return (
    <PageFrame className="gap-5">
      <section className="flex flex-col gap-3 rounded-panel border border-primary/15 bg-primary-soft/55 px-4 py-3 shadow-card sm:flex-row sm:items-center sm:justify-between">
        <div className="flex min-w-0 items-center gap-3">
          <span className="grid h-9 w-9 shrink-0 place-items-center rounded-control bg-surface text-primary shadow-card"><Icon name="database" size={17} /></span>
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <strong className="truncate text-sm text-ink">当前业务数据范围</strong>
              <StatusPill tone={statusTone}>{capability.label}</StatusPill>
            </div>
            <p className="mt-0.5 truncate text-[11px] text-muted">{source.name} · {sourceTypeLabel(source.type)} · 数据截至 {snapshot.latestData || "待识别"} · 只读</p>
          </div>
        </div>
        <div className="flex w-full items-center gap-2 sm:w-auto">
          <SourcePicker source={source} sources={sources} open={pickerOpen} onToggle={() => setPickerOpen((value) => !value)} onSelect={selectSource} />
          <Button variant="primary" disabled={!capability.canAnalyze} onClick={() => startAnalysis()}><Icon name="analysis" size={15} />发起分析</Button>
        </div>
      </section>

      <section className="overflow-hidden rounded-panel border border-line bg-surface shadow-card">
        <div className="grid lg:grid-cols-[minmax(0,1.35fr)_minmax(420px,0.9fr)]">
          <div className="border-b border-primary/10 bg-primary-soft/50 px-6 py-6 lg:border-b-0 lg:border-r">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-[10px] font-semibold tracking-[0.1em] text-primary">TODAY AT A GLANCE</span>
              <span className="inline-flex items-center gap-1 rounded-full bg-success-soft px-2 py-1 text-[10px] font-semibold text-success"><Icon name="check" size={11} />数据范围已就绪</span>
            </div>
            <h2 className="mt-2 max-w-[720px] text-[26px] font-semibold leading-tight tracking-[-0.03em] text-ink">从当前数据状态出发，继续今天的分析工作</h2>
            <p className="mt-2 max-w-[700px] text-xs leading-6 text-muted">页面只展示当前数据源真实可用的指标、调查记录和数据覆盖情况。业务异常接入后，也会统一进入下方待关注队列。</p>
            <div className="mt-5 flex flex-wrap items-center gap-x-5 gap-y-2 text-[11px] text-muted">
              <span className="inline-flex items-center gap-1.5"><Icon name="clock" size={13} />最近扫描 {formatTime(snapshot.lastScan)}</span>
              <span className="inline-flex items-center gap-1.5"><Icon name="shield" size={13} />只读查询边界已启用</span>
            </div>
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-2">
            <SnapshotFact icon="shield" tone="green" label="可信指标" value={metrics.trusted} caption={metrics.pendingBindings ? `${metrics.pendingBindings} 个待验证` : "均可直接问析"} />
            <SnapshotFact icon="knowledge" tone="blue" label="业务主题" value={topics.length} caption="已识别分析范围" />
            <SnapshotFact icon="database" tone="violet" label="登记数据表" value={snapshot.registeredTables} caption={`${snapshot.datedTables} 张已识别日期`} />
            <SnapshotFact icon="insight" tone="amber" label="可用记录" value={compactNumber(snapshot.records)} caption="当前数据源统计" />
          </div>
        </div>
      </section>

      <section className="grid items-start gap-5 lg:grid-cols-[minmax(0,1.25fr)_minmax(300px,0.75fr)]">
        <AttentionPanel items={attentionItems} onOpen={(item) => item.sessionId ? startAnalysis("", item.sessionId) : null} />

        <Card className="overflow-hidden">
          <div className="bg-primary-soft/50">
            <SectionTitle eyebrow="快速开始" title="从已有业务口径发起分析" caption="问题会带着当前数据范围进入智能问析。" />
          </div>
          <div className="border-t border-line p-2.5">
            {suggestions.slice(0, 4).map((item, index) => (
              <button key={item.id} type="button" disabled={!capability.canAnalyze} onClick={() => startAnalysis(item.prompt)} className="group flex w-full cursor-pointer items-center gap-3 rounded-control px-3 py-3 text-left transition-colors hover:bg-surface-3 disabled:cursor-not-allowed disabled:opacity-50">
                <span className={cn("grid h-8 w-8 shrink-0 place-items-center rounded-control", quickStartTones[index % quickStartTones.length].className)}><Icon name={quickStartTones[index % quickStartTones.length].icon} size={15} /></span>
                <span className="min-w-0 flex-1"><strong className="block truncate text-xs font-semibold text-ink">{item.title}</strong><small className="mt-0.5 block truncate text-[10px] text-faint">{item.caption}</small></span>
                <Icon name="arrow" size={14} className="shrink-0 text-faint transition-transform group-hover:translate-x-0.5 group-hover:text-primary" />
              </button>
            ))}
          </div>
        </Card>
      </section>

      <Card className="overflow-hidden">
        <div className="flex flex-wrap items-center justify-between gap-3 bg-teal-soft/50 px-5 py-4">
          <div className="flex min-w-0 items-center gap-3">
            <span className="grid h-9 w-9 shrink-0 place-items-center rounded-control bg-surface text-teal shadow-card"><Icon name="history" size={16} /></span>
            <div><span className="text-[10px] font-semibold tracking-[0.08em] text-teal">持续调查</span><h3 className="mt-0.5 text-base font-semibold text-ink">最近调查</h3><p className="mt-0.5 text-[11px] text-muted">回到已有问题，继续补充条件或查看最新结论。</p></div>
          </div>
          <button type="button" onClick={() => navigatePage("sessions")} className="inline-flex cursor-pointer items-center gap-1 text-xs font-semibold text-primary hover:opacity-75">查看全部 {recent.total} 项<Icon name="arrow" size={13} /></button>
        </div>
        {recent.items.length ? <div className="border-t border-line">{recent.items.map((task) => <InvestigationRow key={task.id} task={task} onOpen={() => startAnalysis("", task.id)} />)}</div> : <div className="border-t border-line px-5 py-9"><EmptyState title="还没有调查记录" description="发起第一次分析后，问题、结论和证据会保存在这里。" /></div>}
      </Card>

      <details className="group overflow-hidden rounded-panel border border-primary/15 bg-surface shadow-card">
        <summary className="flex cursor-pointer list-none items-center justify-between gap-4 bg-primary-soft/30 px-5 py-4 marker:content-none hover:bg-primary-soft/55">
          <div className="flex min-w-0 items-center gap-3">
            <span className="grid h-8 w-8 shrink-0 place-items-center rounded-control bg-primary-soft text-primary"><Icon name="knowledge" size={15} /></span>
            <div className="min-w-0"><strong className="block text-sm text-ink">查看当前可分析范围</strong><span className="mt-0.5 block truncate text-[10px] text-muted">{topics.length} 个业务主题 · {metrics.trusted} 个可信指标 · 首次使用或切换数据源时查看</span></div>
          </div>
          <Icon name="chevron" size={15} className="shrink-0 text-muted transition-transform group-open:rotate-90" />
        </summary>
        <div className="border-t border-line">{topics.length ? topics.map((topic) => <TopicRow key={`${topic.code}-${topic.name}`} topic={topic} />) : <div className="px-5 py-8"><EmptyState title="还没有可分析的业务范围" description="当前数据范围暂未开放业务自助分析。" /></div>}</div>
      </details>
    </PageFrame>
  );
}

function SourcePicker({ source, sources, open, onToggle, onSelect }) {
  const online = source.status === "连接正常";
  return (
    <div className="relative min-w-0 flex-1 sm:flex-none">
      <button type="button" aria-expanded={open} onClick={onToggle} className="flex h-9 w-full min-w-0 cursor-pointer items-center gap-2 rounded-control border border-line bg-surface px-3 text-left transition-colors hover:border-line-strong sm:w-[210px]">
        <span className={cn("h-2 w-2 rounded-full", online ? "bg-green" : "bg-red")} /><span className="min-w-0 flex-1 truncate text-xs font-medium text-ink">{source.name}</span><Icon name="chevron" size={14} className={cn("transition-transform", open && "rotate-90")} />
      </button>
      {open && (
        <div role="listbox" aria-label="切换分析数据源" className="motion-soft-rise absolute right-0 top-full z-30 mt-2 w-[min(320px,calc(100vw-32px))] rounded-panel border border-line bg-surface p-1.5 shadow-card-hover">
          <p className="px-2 pb-1 pt-1 text-[10px] font-semibold text-faint">当前业务数据范围</p>
          {sources.map((item) => (
            <button key={item.id} type="button" role="option" aria-selected={item.id === source.id} onClick={() => onSelect(item)} className={cn("flex w-full cursor-pointer items-center gap-2.5 rounded-control px-2.5 py-2.5 text-left transition-colors", item.id === source.id ? "bg-primary-soft" : "hover:bg-surface-3")}>
              <span className={cn("h-1.5 w-1.5 rounded-full", item.status === "连接正常" ? "bg-green" : "bg-red")} /><span className="min-w-0 flex-1"><strong className="block truncate text-xs text-ink">{item.name}</strong><small className="mt-0.5 block truncate text-[10px] text-faint">{item.status} · {item.typeLabel || sourceTypeLabel(item.type)}</small></span>{item.id === source.id && <Icon name="check" size={14} className="text-primary" />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function SnapshotFact({ icon, tone = "blue", label, value, caption }) {
  const styles = snapshotTones[tone] ?? snapshotTones.blue;
  return <div className={cn("min-w-0 border-b border-r border-line px-5 py-5 even:border-r-0 sm:even:border-r lg:[&:nth-child(2n)]:border-r-0 lg:[&:nth-last-child(-n+2)]:border-b-0", styles.surface)}><div className="flex items-center gap-2"><span className={cn("grid h-7 w-7 shrink-0 place-items-center rounded-control", styles.tile)}><Icon name={icon} size={14} /></span><small className="block text-[10px] font-medium text-muted">{label}</small></div><strong className={cn("mt-2 block truncate font-mono text-xl font-semibold tracking-tight", styles.value)}>{value}</strong><span className="mt-1 block truncate text-[10px] text-muted">{caption}</span></div>;
}

function SectionTitle({ eyebrow, title, caption }) {
  return <div className="px-5 pb-4 pt-5"><span className="text-[10px] font-semibold tracking-[0.08em] text-primary">{eyebrow}</span><h3 className="mt-1 text-base font-semibold text-ink">{title}</h3><p className="mt-1 text-[11px] leading-5 text-muted">{caption}</p></div>;
}

function AttentionPanel({ items, onOpen }) {
  const [primary, ...remaining] = items;
  if (!primary) return null;

  return (
    <Card className="overflow-hidden">
      <div className="flex items-start justify-between gap-4 bg-critical-soft/35 px-5 pb-4 pt-5">
        <div>
          <span className="text-[10px] font-semibold tracking-[0.08em] text-primary">需要关注</span>
          <h3 className="mt-1 text-base font-semibold text-ink">今天的工作队列</h3>
          <p className="mt-1 text-[11px] leading-5 text-muted">优先展示最需要继续处理的一项，其余状态按需展开。</p>
        </div>
        <StatusPill tone={primary.tone === "red" ? "red" : primary.tone === "amber" ? "amber" : "blue"}>{items.length} 项</StatusPill>
      </div>

      <div className="border-t border-line p-3">
        <AttentionFocus item={primary} onOpen={primary.sessionId ? () => onOpen(primary) : null} />
      </div>

      {remaining.length > 0 && (
        <details className="group border-t border-line">
          <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-5 py-3 marker:content-none hover:bg-surface-2">
            <span className="min-w-0">
              <strong className="block text-xs font-semibold text-ink">另外 {remaining.length} 项待查看</strong>
              <span className="mt-0.5 block truncate text-[10px] text-muted">{remaining.map((item) => item.eyebrow).join(" · ")}</span>
            </span>
            <Icon name="chevron" size={14} className="shrink-0 text-muted transition-transform group-open:rotate-90" />
          </summary>
          <div className="border-t border-line bg-surface-2/60 px-3 py-1.5">
            {remaining.map((item) => (
              <AttentionCompact key={item.id} item={item} onOpen={item.sessionId ? () => onOpen(item) : null} />
            ))}
          </div>
        </details>
      )}
    </Card>
  );
}

function AttentionFocus({ item, onOpen }) {
  const styles = { green: "bg-success-soft text-success", amber: "bg-warning-soft text-warning", red: "bg-critical-soft text-critical", blue: "bg-primary-soft text-primary" };
  const surfaces = { green: "bg-success-soft/75", amber: "bg-warning-soft/75", red: "bg-critical-soft/85", blue: "bg-primary-soft/75" };
  const content = <><span className={cn("grid h-10 w-10 shrink-0 place-items-center rounded-control", styles[item.tone] ?? styles.blue)}><Icon name={item.icon} size={17} /></span><span className="min-w-0 flex-1"><small className="block text-[10px] font-semibold text-faint">{item.eyebrow}</small><strong className="mt-1 block line-clamp-2 text-sm font-semibold leading-5 text-ink">{item.title}</strong><span className="mt-1.5 block text-[10px] leading-4 text-muted">{item.detail}</span></span>{item.action && <span className="inline-flex shrink-0 items-center gap-1 self-end pb-0.5 text-[11px] font-semibold text-primary">{item.action}<Icon name="arrow" size={13} /></span>}</>;
  const className = cn("flex w-full items-start gap-3 rounded-card px-4 py-4 text-left transition-colors", surfaces[item.tone] ?? surfaces.blue);
  if (!onOpen) return <div className={className}>{content}</div>;
  return <button type="button" onClick={onOpen} className={cn(className, "cursor-pointer hover:bg-surface-3 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary")}>{content}</button>;
}

function AttentionCompact({ item, onOpen }) {
  const styles = { green: "bg-success-soft text-success", amber: "bg-warning-soft text-warning", red: "bg-critical-soft text-critical", blue: "bg-primary-soft text-primary" };
  const content = <><span className={cn("grid h-7 w-7 shrink-0 place-items-center rounded-control", styles[item.tone] ?? styles.blue)}><Icon name={item.icon} size={13} /></span><span className="min-w-0 flex-1"><strong className="block truncate text-[11px] font-semibold text-ink">{item.title}</strong><span className="mt-0.5 block truncate text-[10px] text-muted">{item.detail}</span></span>{onOpen && <Icon name="arrow" size={13} className="shrink-0 text-faint" />}</>;
  const className = "flex w-full items-center gap-2.5 rounded-control px-2 py-2.5 text-left";
  if (!onOpen) return <div className={className}>{content}</div>;
  return <button type="button" onClick={onOpen} className={cn(className, "cursor-pointer transition-colors hover:bg-surface")}>{content}</button>;
}

function InvestigationRow({ task, onOpen }) {
  const status = runLabels[task.status] ?? task.status;
  const tone = task.status === "succeeded" ? "green" : task.status === "failed" || task.status === "blocked" ? "red" : task.status === "waiting_user" ? "amber" : "blue";
  const stateStyles = { green: "bg-success-soft text-success", red: "bg-critical-soft text-critical", amber: "bg-warning-soft text-warning", blue: "bg-primary-soft text-primary" };
  const stateIcon = tone === "green" ? "check" : tone === "red" ? "alert" : tone === "amber" ? "clock" : "analysis";
  return <button type="button" onClick={onOpen} className="group grid w-full cursor-pointer gap-2 border-b border-line px-5 py-4 text-left transition-colors last:border-b-0 hover:bg-primary-soft/25 sm:grid-cols-[minmax(0,1fr)_120px_150px_90px] sm:items-center"><span className="flex min-w-0 items-center gap-3"><span className={cn("grid h-8 w-8 shrink-0 place-items-center rounded-control", stateStyles[tone])}><Icon name={stateIcon} size={14} /></span><span className="min-w-0"><strong className="block truncate text-xs font-semibold text-ink">{task.title}</strong><small className="mt-1 block text-[10px] text-faint">调查记录 · {task.runCount} 次分析</small></span></span><StatusPill tone={tone}>{status}</StatusPill><span className="text-[10px] text-muted">更新于 {formatTime(task.updatedAt)}</span><span className="inline-flex items-center justify-end gap-1 text-[11px] font-semibold text-primary">继续调查<Icon name="arrow" size={13} className="transition-transform group-hover:translate-x-0.5" /></span></button>;
}

function TopicRow({ topic }) {
  const tone = topicTones[topic.color] ?? topicTones.blue;
  return <div className="grid gap-3 border-b border-line px-5 py-4 last:border-b-0 sm:grid-cols-[minmax(150px,0.7fr)_minmax(0,1.3fr)_150px] sm:items-center"><div className="flex min-w-0 items-center gap-3"><span className={cn("h-8 w-1 shrink-0 rounded-full", tone)} /><span className="min-w-0"><strong className="block truncate text-sm text-ink">{topic.name}</strong><small className="mt-0.5 block text-[10px] text-faint">{topic.code}</small></span></div><div className="min-w-0"><p className="truncate text-[11px] text-muted">{topic.description}</p>{topic.metricNames.length > 0 && <p className="mt-1.5 truncate text-[10px] text-ink-2">{topic.metricNames.join(" / ")}</p>}</div><div className="flex items-center justify-between gap-3 sm:justify-end"><span className="text-[10px] text-muted">{topic.tableCount} 张表 · {topic.metricCount} 个指标</span><StatusPill tone={topic.mode === "metrics" ? "green" : "gray"}>{topic.mode === "metrics" ? "可问析" : "可探索"}</StatusPill></div></div>;
}
