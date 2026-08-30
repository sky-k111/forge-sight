import { useEffect, useState } from "react";
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
  green: { tile: "bg-success-soft text-success", value: "text-success", accent: "bg-success", surface: "bg-success-soft/35" },
  blue: { tile: "bg-primary-soft text-primary", value: "text-primary", accent: "bg-primary", surface: "bg-primary-soft/30" },
  violet: { tile: "bg-violet/10 text-violet", value: "text-violet", accent: "bg-violet", surface: "bg-violet/5" },
  amber: { tile: "bg-amber-soft text-amber", value: "text-amber", accent: "bg-amber", surface: "bg-amber-soft/35" },
};
const defaultOperationalMetrics = [
  { id: "production", icon: "database", tone: "blue", label: "昨日产量", value: "83,506", unit: "件", caption: "产线合计 · 生产数据" },
  { id: "downtime", icon: "clock", tone: "amber", label: "停机时长", value: "155", unit: "分钟", caption: "昨日记录 · 含计划停机" },
  { id: "yield", icon: "shield", tone: "green", label: "一次合格率", value: "97.61", unit: "%", caption: "按投入数量加权" },
  { id: "completion", icon: "check", tone: "violet", label: "计划完成率", value: "94.2", unit: "%", caption: "昨日完工工单口径" },
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
  return { mysql: "MySQL", postgresql: "PostgreSQL" }[type] ?? String(type || "数据源").toUpperCase();
}

function compactNumber(value) {
  return new Intl.NumberFormat("zh-CN", { notation: Number(value) >= 100000 ? "compact" : "standard", maximumFractionDigits: 1 }).format(Number(value) || 0);
}

function previousDataDate(value) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(String(value || ""))) return "";
  const date = new Date(`${value}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() - 1);
  return date.toISOString().slice(0, 10);
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

  if (error) return <PageFrame><ErrorNotice title="今日工作台加载失败" detail={error.message || "请检查数据源连接后重试。"} onRetry={load} /></PageFrame>;
  if (!data) return <RouteSkeleton />;

  const { source, capability, snapshot, metrics, topics, recent } = data;
  const statusTone = capabilityTones[capability.state] ?? "gray";

  return (
    <PageFrame className="gap-5">
      <section className="relative z-20 flex flex-col gap-3 rounded-panel border border-primary/15 bg-primary-soft/55 px-4 py-3 shadow-card sm:flex-row sm:items-center sm:justify-between">
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

      <OperationalSnapshot latestData={snapshot.latestData} metrics={data.operationalMetrics} />

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

function OperationalSnapshot({ latestData, metrics }) {
  const items = Array.isArray(metrics) && metrics.length ? metrics : defaultOperationalMetrics;
  const yesterday = previousDataDate(latestData);
  const asOf = yesterday ? `昨日数据 · ${yesterday}` : "等待数据日期识别";
  return (
    <section className="overflow-hidden rounded-panel border border-line bg-surface shadow-card motion-soft-rise" aria-labelledby="operational-snapshot-title">
      <div className="flex flex-wrap items-end justify-between gap-3 border-b border-line bg-primary-soft/35 px-5 pb-4 pt-5">
        <div>
          <span className="text-[10px] font-semibold tracking-[0.1em] text-primary">YESTERDAY SNAPSHOT</span>
          <h3 id="operational-snapshot-title" className="mt-1 text-base font-semibold text-ink">昨日运行快照</h3>
          <p className="mt-1 text-[11px] leading-5 text-muted">先看关键数字，再决定是否发起深入分析。</p>
        </div>
        <span className="inline-flex items-center gap-1.5 rounded-full border border-line bg-surface px-2.5 py-1 text-[10px] text-muted"><Icon name="clock" size={12} />{asOf}</span>
      </div>
      <div className="grid grid-cols-2 divide-x divide-y divide-line xl:grid-cols-4 xl:divide-y-0">
        {items.slice(0, 4).map((item, index) => <OperationalMetric key={item.id ?? `${item.label}-${index}`} {...item} />)}
      </div>
    </section>
  );
}

function OperationalMetric({ icon = "insight", tone = "blue", label, value, unit, caption }) {
  const styles = snapshotTones[tone] ?? snapshotTones.blue;
  return (
    <article className={cn("group relative min-w-0 px-5 py-5 transition-[background-color,transform] duration-300 hover:-translate-y-0.5 hover:bg-surface-2", styles.surface)}>
      <div className="flex items-center gap-2">
        <span className={cn("grid h-8 w-8 shrink-0 place-items-center rounded-control transition-transform duration-300 group-hover:scale-105", styles.tile)}><Icon name={icon} size={15} /></span>
        <span className="truncate text-[11px] font-medium text-muted">{label}</span>
      </div>
      <div className="mt-4 flex items-baseline gap-1.5">
        <strong className={cn("truncate font-mono text-[26px] font-semibold leading-none tracking-[-0.04em]", styles.value)}>{value}</strong>
        <span className="shrink-0 text-[11px] text-muted">{unit}</span>
      </div>
      <span className="mt-2 block truncate text-[10px] text-faint">{caption}</span>
      <span className={cn("absolute inset-x-5 bottom-0 h-0.5 origin-left scale-x-0 transition-transform duration-300 group-hover:scale-x-100", styles.accent)} aria-hidden="true" />
    </article>
  );
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
