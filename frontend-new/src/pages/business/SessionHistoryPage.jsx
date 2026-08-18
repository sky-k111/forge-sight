import { useEffect, useMemo, useState } from "react";
import { useOutletContext } from "react-router-dom";
import Icon from "../../components/ui/Icon";
import { Card, EmptyState, ErrorNotice, PageFrame, RouteSkeleton, StatusPill } from "../../components/ui";
import { listSessions } from "../../api/analysis";

const ANALYSIS_SESSION_KEY = "forgesight:analysis-session-id";

// 与 OverviewPage/分析页共用的状态文案与色调
const runLabels = {
  queued: "排队中",
  running: "分析中",
  waiting_user: "待补充",
  succeeded: "已完成",
  failed: "失败",
  blocked: "未执行",
  cancelled: "已取消",
};

const runTones = {
  succeeded: "green",
  failed: "red",
  blocked: "red",
  waiting_user: "amber",
  queued: "blue",
  running: "blue",
};

// 状态筛选分组：null=从未运行，展示为「未开始」
const statusGroups = [
  { id: "all", label: "全部" },
  { id: "running", label: "进行中" },
  { id: "succeeded", label: "已完成" },
  { id: "failed", label: "失败/拦截" },
  { id: "never", label: "未开始" },
];

const statusGroupOf = (latestRunStatus) => {
  if (!latestRunStatus) return "never";
  if (latestRunStatus === "succeeded") return "succeeded";
  if (["failed", "blocked", "cancelled"].includes(latestRunStatus)) return "failed";
  return "running";
};

function formatTime(value) {
  if (!value) return "暂无记录";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export default function SessionHistoryPage() {
  const { navigatePage } = useOutletContext();
  const [sessions, setSessions] = useState(null);
  const [error, setError] = useState(null);
  const [sourceFilter, setSourceFilter] = useState("all");
  const [statusFilter, setStatusFilter] = useState("all");

  const load = async () => {
    setError(null);
    setSessions(null);
    try {
      setSessions(await listSessions());
    } catch (requestError) {
      setError(requestError);
    }
  };

  useEffect(() => { load(); }, []);

  const sources = useMemo(() => {
    const seen = new Map();
    for (const item of sessions ?? []) {
      if (item.sourceId != null && !seen.has(item.sourceId)) seen.set(item.sourceId, item.sourceName ?? `数据源 ${item.sourceId}`);
    }
    return [...seen.entries()].map(([id, name]) => ({ id, name }));
  }, [sessions]);

  const filtered = useMemo(() => {
    const rows = sessions ?? [];
    return rows.filter((item) => {
      if (sourceFilter !== "all" && item.sourceId !== sourceFilter) return false;
      if (statusFilter !== "all" && statusGroupOf(item.latestRunStatus) !== statusFilter) return false;
      return true;
    });
  }, [sessions, sourceFilter, statusFilter]);

  const openSession = (session) => {
    window.sessionStorage.setItem(ANALYSIS_SESSION_KEY, session.id);
    navigatePage("analysis");
  };

  if (error) {
    return <PageFrame><ErrorNotice title="历史会话加载失败" detail={error.message || "请稍后重试。"} onRetry={load} /></PageFrame>;
  }
  if (!sessions) return <RouteSkeleton />;

  const total = sessions.length;

  return (
    <PageFrame className="gap-4.5">
      <section className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2.5">
          <label className="flex items-center gap-1.5 text-[11px] font-medium text-muted">
            数据源
            <select
              value={sourceFilter}
              onChange={(event) => setSourceFilter(event.target.value === "all" ? "all" : Number(event.target.value))}
              className="h-8 cursor-pointer rounded-tag border border-line bg-surface px-2 text-xs text-ink outline-none focus:border-primary/40"
            >
              <option value="all">全部数据源</option>
              {sources.map((source) => <option key={source.id} value={source.id}>{source.name}</option>)}
            </select>
          </label>
          <label className="flex items-center gap-1.5 text-[11px] font-medium text-muted">
            状态
            <select
              value={statusFilter}
              onChange={(event) => setStatusFilter(event.target.value)}
              className="h-8 cursor-pointer rounded-tag border border-line bg-surface px-2 text-xs text-ink outline-none focus:border-primary/40"
            >
              {statusGroups.map((group) => <option key={group.id} value={group.id}>{group.label}</option>)}
            </select>
          </label>
        </div>
        <span className="text-xs text-faint">{filtered.length} / {total} 个会话</span>
      </section>

      <Card className="overflow-hidden">
        <div className="border-t border-line">
          {filtered.length ? (
            <ul className="divide-y divide-line">
              {filtered.map((session) => {
                const status = session.latestRunStatus ? (runLabels[session.latestRunStatus] ?? session.latestRunStatus) : "未开始";
                const tone = session.latestRunStatus ? (runTones[session.latestRunStatus] ?? "gray") : "gray";
                return (
                  <li key={session.id}>
                    <button
                      type="button"
                      onClick={() => openSession(session)}
                      className="flex w-full cursor-pointer items-center gap-3 px-5 py-4 text-left transition-colors hover:bg-surface-2"
                    >
                      <span className="grid h-9 w-9 shrink-0 place-items-center rounded-tag bg-primary-soft text-primary"><Icon name="message" size={16} /></span>
                      <span className="min-w-0 flex-1">
                        <strong className="block truncate text-sm font-semibold text-ink">{session.displayTitle ?? session.title ?? "未命名分析"}</strong>
                        <span className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px] text-muted">
                          <span className="inline-flex items-center gap-1"><Icon name="database" size={12} />{session.sourceName ?? `数据源 ${session.sourceId}`}</span>
                          <span className="inline-flex items-center gap-1"><Icon name="clock" size={12} />{formatTime(session.updatedAt)}</span>
                          <span className="inline-flex items-center gap-1"><Icon name="analysis" size={12} />{session.runCount ?? 0} 次分析</span>
                        </span>
                      </span>
                      <StatusPill tone={tone}>{status}</StatusPill>
                      <Icon name="arrow" size={14} className="shrink-0 text-faint" />
                    </button>
                  </li>
                );
              })}
            </ul>
          ) : (
            <div className="px-5 py-10">
              <EmptyState
                title={sessions.length ? "没有符合条件的会话" : "还没有历史会话"}
                description={sessions.length ? "换个筛选条件试试，或在智能问析中发起第一次分析。" : "在智能问析中发起分析后，这里会沉淀你的全部问答记录。"}
              />
            </div>
          )}
        </div>
      </Card>
    </PageFrame>
  );
}
