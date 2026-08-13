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

const topicTones = {
  blue: "bg-primary",
  teal: "bg-teal",
  amber: "bg-amber",
  violet: "bg-violet",
};

const capabilityTones = {
  ready: "green",
  catalog_only: "blue",
  offline: "red",
  empty: "amber",
};

const runLabels = {
  queued: "排队中",
  running: "分析中",
  waiting_user: "待补充",
  succeeded: "已完成",
  failed: "失败",
  blocked: "未执行",
  cancelled: "已取消",
  "未开始": "未开始",
};

function formatTime(value) {
  if (!value) return "暂无记录";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function sourceTypeLabel(type) {
  return { mysql: "MySQL", postgresql: "PostgreSQL" }[type] ?? String(type || "数据源").toUpperCase();
}

export default function OverviewPage() {
  const { navigatePage } = useOutletContext();
  const [data, setData] = useState(null);
  const [sources, setSources] = useState([]);
  const [sourceId, setSourceId] = useState(() => Number(window.sessionStorage.getItem(ACTIVE_SOURCE_KEY)) || null);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [activeView, setActiveView] = useState("overview");
  const [selectedPrompt, setSelectedPrompt] = useState("");
  const [error, setError] = useState(null);

  const load = async () => {
    setError(null);
    setData(null);
    try {
      const availableSources = await listSources();
      const preferred = availableSources.find((item) => item.id === sourceId)
        ?? availableSources.find((item) => item.isDefault)
        ?? availableSources[0];
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

  useEffect(() => { load(); }, []);

  const selectSource = async (nextSource) => {
    setPickerOpen(false);
    if (nextSource.id === sourceId) return;
    setSelectedPrompt("");
    window.sessionStorage.setItem(ACTIVE_SOURCE_KEY, String(nextSource.id));
    window.sessionStorage.removeItem(ANALYSIS_SESSION_KEY);
    setSourceId(nextSource.id);
    setError(null);
    setData(null);
    try {
      setData(await getBusinessOverview(nextSource.id, USER_ID()));
    } catch (requestError) {
      setError(requestError);
    }
  };

  const startAnalysis = (prompt = "", sessionId = "") => {
    if (!data?.capability?.canAnalyze) return;
    window.sessionStorage.setItem(ACTIVE_SOURCE_KEY, String(data.source.id));
    if (prompt) window.sessionStorage.setItem(ANALYSIS_DRAFT_KEY, prompt);
    else window.sessionStorage.removeItem(ANALYSIS_DRAFT_KEY);
    if (sessionId) window.sessionStorage.setItem(ANALYSIS_SESSION_KEY, sessionId);
    else window.sessionStorage.removeItem(ANALYSIS_SESSION_KEY);
    navigatePage("analysis");
  };

  if (error) {
    return <PageFrame><ErrorNotice title="分析总览加载失败" detail={error.message || "请检查数据源连接后重试。"} onRetry={load} /></PageFrame>;
  }
  if (!data) return <RouteSkeleton />;

  const { source, capability, snapshot, metrics, topics, suggestions, recent } = data;
  const statusTone = capabilityTones[capability.state] ?? "gray";
  const analysisPrompt = selectedPrompt || suggestions[0]?.prompt || "";
  const primaryAction = activeView === "content"
    ? { label: "用所选问题开始", prompt: analysisPrompt }
    : { label: activeView === "recent" ? "新建分析" : "发起智能分析", prompt: "" };

  return (
    <PageFrame className="gap-4.5">
      <section className="flex flex-wrap items-center justify-between gap-3">
        <div className="inline-flex rounded-control bg-surface-3 p-1" role="tablist" aria-label="分析总览视图">
          {[
            { id: "overview", label: "总览" },
            { id: "content", label: "可分析内容" },
            { id: "recent", label: "最近分析" },
          ].map((view) => (
            <button
              key={view.id}
              type="button"
              role="tab"
              aria-selected={activeView === view.id}
              onClick={() => setActiveView(view.id)}
              className={cn(
                "min-w-[92px] cursor-pointer rounded-[8px] border px-3 py-1.5 text-xs font-medium transition-all duration-150",
                activeView === view.id
                  ? "border-line bg-surface text-ink shadow-card"
                  : "border-transparent text-muted hover:text-ink",
              )}
            >
              {view.label}
            </button>
          ))}
        </div>
        <div className="flex w-full flex-wrap items-center gap-2.5 sm:w-auto sm:flex-nowrap">
          <SourcePicker source={source} sources={sources} open={pickerOpen} onToggle={() => setPickerOpen((value) => !value)} onSelect={selectSource} />
          <Button
            className="flex-1 sm:flex-none"
            variant="primary"
            disabled={!capability.canAnalyze || (activeView === "content" && !analysisPrompt)}
            onClick={() => startAnalysis(primaryAction.prompt)}
          >
            <Icon name="analysis" size={15} />{primaryAction.label}
          </Button>
        </div>
      </section>

      {activeView === "overview" && (
        <div role="tabpanel" className="grid gap-4 xl:grid-cols-[minmax(0,1.55fr)_minmax(310px,0.72fr)]">
          <Card className="overflow-hidden">
            <div className="px-6 py-5">
              <div className="flex flex-wrap items-center gap-2">
                <span className="grid h-7 w-7 place-items-center rounded-tag bg-primary-soft text-primary"><Icon name="database" size={15} /></span>
                <span className="text-[10px] font-semibold tracking-[0.06em] text-faint">当前分析数据源</span>
              </div>
              <h2 className="mt-3 truncate text-[24px] font-semibold tracking-[-0.025em] text-ink">{source.name}</h2>
              <p className="mt-1 text-xs text-muted">{sourceTypeLabel(source.type)} · {source.database || "数据库待识别"} · 只读访问</p>
              <p className="mt-4 max-w-[680px] text-[12px] leading-6 text-muted">
                当前页面展示这个数据源已经具备的业务分析能力。切换分析范围后，可用主题、可信指标、数据日期和历史任务会同步更新。
              </p>
            </div>
            <div className="grid border-t border-line sm:grid-cols-2 xl:grid-cols-4">
              <FoundationFact icon="database" label="已登记数据表" value={snapshot.registeredTables} caption="当前源已确认的物理资产" />
              <FoundationFact icon="knowledge" label="可信业务指标" value={metrics.trusted} caption={metrics.pendingBindings ? `${metrics.pendingBindings} 个绑定仍待验证` : "均已发布并完成绑定验证"} />
              <FoundationFact icon="overview" label="可分析业务主题" value={topics.length} caption="由业务语义与指标口径识别" />
              <FoundationFact icon="clock" label="最近数据日期" value={snapshot.latestData} caption={snapshot.datedTables ? `${snapshot.datedTables}/${snapshot.registeredTables} 张表已识别日期` : "等待识别各表数据日期"} compact />
            </div>
          </Card>

          <Card className="flex min-h-[250px] flex-col overflow-hidden">
            <div className="flex items-start justify-between gap-3 px-5 pb-3 pt-5">
              <div>
                <span className="text-[10px] font-semibold tracking-[0.06em] text-faint">ANALYSIS READINESS</span>
                <h3 className="mt-1 text-base font-semibold text-ink">现在建议做什么</h3>
              </div>
              <StatusPill tone={statusTone}>{capability.label}</StatusPill>
            </div>
            <div className="flex flex-1 flex-col px-5 pb-5">
              <p className="text-xs leading-6 text-muted">{capability.detail}</p>
              {suggestions[0] ? (
                <div className="mt-4 rounded-card border border-line bg-surface-2 px-4 py-3.5">
                  <span className="text-[10px] font-semibold tracking-wide text-primary">推荐下一步</span>
                  <p className="mt-1.5 text-sm font-semibold text-ink">{suggestions[0].title}</p>
                  <p className="mt-1 line-clamp-2 text-[11px] leading-relaxed text-muted">{suggestions[0].prompt}</p>
                  <button
                    type="button"
                    onClick={() => startAnalysis(suggestions[0].prompt)}
                    disabled={!capability.canAnalyze}
                    className="mt-3 inline-flex cursor-pointer items-center gap-1 text-xs font-semibold text-primary transition-opacity hover:opacity-75 disabled:cursor-not-allowed disabled:opacity-45"
                  >
                    直接开始分析 <Icon name="arrow" size={13} />
                  </button>
                </div>
              ) : (
                <dl className="mt-4 space-y-3 border-t border-line pt-4">
                  <ReadinessItem label="可直接使用的可信指标" value={metrics.trusted} />
                  <ReadinessItem label="可分析业务主题" value={topics.length} />
                </dl>
              )}
              <p className="mt-auto pt-4 text-[10px] text-faint">最近扫描 {formatTime(snapshot.lastScan)}</p>
            </div>
          </Card>
        </div>
      )}

      {activeView === "content" && (
        <section role="tabpanel" className="grid gap-4 xl:grid-cols-[minmax(0,1.35fr)_minmax(340px,0.65fr)]">
          <Card className="overflow-hidden">
            <SectionTitle eyebrow="可信分析范围" title="当前数据源可以分析什么" caption="业务范围由这个数据源已登记的表和已验证指标共同决定。" />
            {topics.length ? (
              <div className="border-t border-line">
                {topics.map((topic) => <TopicRow key={`${topic.code}-${topic.name}`} topic={topic} />)}
              </div>
            ) : (
              <div className="border-t border-line px-5 py-6"><EmptyState title="还没有可分析的业务范围" description="当前数据范围暂未开放业务自助分析。" /></div>
            )}
          </Card>

          <Card className="overflow-hidden">
            <SectionTitle eyebrow="推荐问题" title="选择一个分析问题" caption="选择后，使用页面右上角按钮进入智能问析。" />
            <div className="border-t border-line p-2.5">
              {suggestions.map((item) => {
                const selected = analysisPrompt === item.prompt;
                return (
                  <button
                    key={item.id}
                    type="button"
                    aria-pressed={selected}
                    disabled={!capability.canAnalyze}
                    onClick={() => setSelectedPrompt(item.prompt)}
                    className={cn(
                      "flex w-full cursor-pointer items-start gap-3 rounded-control border px-3 py-3 text-left transition-colors disabled:cursor-not-allowed disabled:opacity-50",
                      selected ? "border-primary/30 bg-primary-soft" : "border-transparent hover:bg-surface-3",
                    )}
                  >
                    <span className={cn("mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded-full border", selected ? "border-primary bg-primary text-white" : "border-line-strong bg-surface")}>
                      {selected && <Icon name="check" size={12} />}
                    </span>
                    <span className="min-w-0 flex-1"><strong className="block text-xs font-semibold text-ink">{item.title}</strong><small className="mt-1 block text-[10px] leading-relaxed text-faint">{item.caption}</small></span>
                  </button>
                );
              })}
            </div>
          </Card>
        </section>
      )}

      {activeView === "recent" && (
        <section role="tabpanel">
          <Card className="overflow-hidden">
            <div className="flex flex-wrap items-end justify-between gap-3 px-5 pb-4 pt-5">
              <div>
                <span className="text-[10px] font-semibold tracking-[0.06em] text-faint">RECENT ANALYSIS</span>
                <h3 className="mt-1 text-base font-semibold text-ink">当前数据源的最近分析</h3>
                <p className="mt-1 text-[11px] text-muted">继续处理你在这个数据范围内提出的业务问题。</p>
              </div>
              <span className="font-mono text-xs text-faint">{recent.total} 项</span>
            </div>
            {recent.items.length ? (
              <div className="grid gap-0 border-t border-line md:grid-cols-2">
                {recent.items.map((task) => <RecentTask key={task.id} task={task} onOpen={() => startAnalysis("", task.id)} compact />)}
              </div>
            ) : (
              <div className="border-t border-line px-5 py-10 text-center text-xs text-faint">这个数据源还没有分析记录</div>
            )}
          </Card>
        </section>
      )}
    </PageFrame>
  );
}

function SourcePicker({ source, sources, open, onToggle, onSelect }) {
  const online = source.status === "连接正常";
  return (
    <div className="relative min-w-0 flex-1 sm:flex-none">
      <button type="button" aria-expanded={open} onClick={onToggle} className="flex w-full min-w-0 cursor-pointer items-center gap-2 rounded-control border border-line bg-surface px-3 py-2 text-left transition-colors hover:border-line-strong sm:min-w-[230px]">
        <span className={cn("h-2 w-2 rounded-full", online ? "bg-green" : "bg-red")} />
        <span className="min-w-0 flex-1"><strong className="block truncate text-xs text-ink">{source.name}</strong><small className="block truncate text-[10px] text-faint">{source.status} · {sourceTypeLabel(source.type)}</small></span>
        <Icon name="chevron" size={15} className={cn("transition-transform", open && "rotate-90")} />
      </button>
      {open && (
        <div role="listbox" aria-label="切换分析数据源" className="motion-soft-rise absolute left-0 top-full z-30 mt-2 w-[min(310px,calc(100vw-32px))] rounded-panel border border-line bg-surface p-1.5 shadow-card-hover sm:left-auto sm:right-0">
          <p className="px-2 pb-1 pt-1 text-[10px] font-semibold text-faint">分析数据源</p>
          {sources.map((item) => (
            <button key={item.id} type="button" role="option" aria-selected={item.id === source.id} onClick={() => onSelect(item)} className={cn("flex w-full cursor-pointer items-center gap-2.5 rounded-control px-2.5 py-2.5 text-left transition-colors", item.id === source.id ? "bg-primary-soft" : "hover:bg-surface-3")}>
              <span className={cn("h-1.5 w-1.5 rounded-full", item.status === "连接正常" ? "bg-green" : "bg-red")} />
              <span className="min-w-0 flex-1"><strong className="block truncate text-xs text-ink">{item.name}</strong><small className="mt-0.5 block truncate text-[10px] text-faint">{item.status} · {item.typeLabel || sourceTypeLabel(item.type)}</small></span>
              {item.id === source.id && <Icon name="check" size={14} className="text-primary" />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function FoundationFact({ icon, label, value, caption, compact = false }) {
  return (
    <div className="flex min-w-0 items-start gap-3 border-b border-line px-5 py-4 last:border-b-0 xl:border-b-0 xl:border-l xl:first:border-l-0">
      <span className="grid h-8 w-8 shrink-0 place-items-center rounded-tag bg-surface-2 text-muted"><Icon name={icon} size={15} /></span>
      <span className="min-w-0"><small className="block text-[10px] text-faint">{label}</small><strong className={cn("mt-0.5 block truncate font-mono font-semibold text-ink", compact ? "text-sm" : "text-lg")}>{value}</strong><span className="mt-0.5 block truncate text-[10px] text-muted">{caption}</span></span>
    </div>
  );
}

function SectionTitle({ eyebrow, title, caption }) {
  return <div className="px-5 pb-4 pt-5"><span className="text-[10px] font-semibold text-primary">{eyebrow}</span><h3 className="mt-1 text-base font-semibold text-ink">{title}</h3><p className="mt-1 text-[11px] text-muted">{caption}</p></div>;
}

function ReadinessItem({ label, value }) {
  return <div className="flex items-baseline justify-between gap-4"><dt className="text-[11px] text-muted">{label}</dt><dd className="font-mono text-sm font-semibold text-ink">{value}</dd></div>;
}

function TopicRow({ topic }) {
  const tone = topicTones[topic.color] ?? topicTones.blue;
  return (
    <div className="grid gap-3 border-b border-line px-5 py-4 last:border-b-0 sm:grid-cols-[minmax(150px,0.7fr)_minmax(0,1.3fr)_150px] sm:items-center">
      <div className="flex min-w-0 items-center gap-3"><span className={cn("h-8 w-1 shrink-0 rounded-full", tone)} /><span className="min-w-0"><strong className="block truncate text-sm text-ink">{topic.name}</strong><small className="mt-0.5 block text-[10px] text-faint">{topic.code}</small></span></div>
      <div className="min-w-0"><p className="truncate text-[11px] text-muted">{topic.description}</p>{topic.metricNames.length > 0 && <p className="mt-1.5 truncate text-[10px] text-ink-2">{topic.metricNames.join(" / ")}</p>}</div>
      <div className="flex items-center justify-between gap-3 sm:justify-end"><span className="text-[10px] text-muted">{topic.tableCount} 张表 · {topic.metricCount} 个指标</span><StatusPill tone={topic.mode === "metrics" ? "green" : "gray"}>{topic.mode === "metrics" ? "可问析" : "可探索"}</StatusPill></div>
    </div>
  );
}

function RecentTask({ task, onOpen, compact = false }) {
  const status = runLabels[task.status] ?? task.status;
  const tone = task.status === "succeeded" ? "green" : task.status === "failed" || task.status === "blocked" ? "red" : task.status === "waiting_user" ? "amber" : "blue";
  return (
    <div className={cn("group flex min-h-[142px] flex-col p-5 transition-colors hover:bg-surface-2", compact && "border-b border-line md:odd:border-r")}>
      <div className="flex items-start justify-between gap-3">
        <span className="grid h-8 w-8 shrink-0 place-items-center rounded-tag bg-primary-soft text-primary"><Icon name="message" size={15} /></span>
        <StatusPill tone={tone}>{status}</StatusPill>
      </div>
      <h4 className="mt-3 line-clamp-2 text-sm font-semibold leading-5 text-ink">{task.title}</h4>
      <div className="mt-auto flex items-center justify-between gap-3 pt-4">
        <span className="text-[10px] text-faint">{formatTime(task.updatedAt)} · {task.runCount} 次分析</span>
        <button type="button" onClick={onOpen} className="flex shrink-0 cursor-pointer items-center gap-1 text-[11px] font-semibold text-primary transition-colors hover:text-primary-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/30">
          继续分析<Icon name="arrow" size={13} className="transition-transform group-hover:translate-x-0.5" />
        </button>
      </div>
    </div>
  );
}
