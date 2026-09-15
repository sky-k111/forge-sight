import { useEffect, useState } from "react";
import { useOutletContext } from "react-router-dom";
import Icon from "../../components/ui/Icon";
import cn from "../../lib/cn";
import { ErrorNotice, PageFrame, RouteSkeleton, StatusPill } from "../../components/ui";
import { getAdminOverview } from "../../api/overview";

const healthTones = {
  blue: "text-primary",
  teal: "text-teal",
  violet: "text-violet",
  green: "text-green",
};
const healthBars = {
  blue: "bg-primary",
  teal: "bg-teal",
  violet: "bg-violet",
  green: "bg-green",
};
const groupMeta = {
  待审核: { tone: "amber", dot: "bg-amber" },
  待映射: { tone: "blue", dot: "bg-primary" },
  已拦截: { tone: "red", dot: "bg-red" },
};
const activityTones = { 已拦截: "red", 允许: "green", 已记录: "gray" };
const sourceTypeLabels = { mysql: "MySQL", postgresql: "PostgreSQL", mariadb: "MariaDB", sqlserver: "SQL Server", oracle: "Oracle" };

function percentOf(value) {
  const match = String(value).match(/(\d+(?:\.\d+)?)%/);
  return match ? Math.max(0, Math.min(100, Number(match[1]))) : null;
}

export default function AdminOverviewPage() {
  const { navigatePage } = useOutletContext();
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  const load = () => {
    setError(null);
    setData(null);
    getAdminOverview().then(setData).catch(setError);
  };
  useEffect(load, []);

  if (error) {
    return (
      <PageFrame>
        <ErrorNotice title="治理总览加载失败" detail="请检查数据连接后重试。" onRetry={load} />
      </PageFrame>
    );
  }
  if (!data) return <RouteSkeleton />;

  const clean = data.summary.categoryCount === 0;
  const groups = data.queue.reduce((acc, item) => {
    (acc[item.status] ??= []).push(item);
    return acc;
  }, {});
  const groupOrder = Object.keys(groups);
  const sources = data.sources ?? [];
  const activity = data.activity ?? [];

  return (
    <PageFrame className="max-w-[1360px] gap-4">
      <section className="flex flex-wrap items-center justify-between gap-3 border-b border-line pb-3">
        <div className="flex min-w-0 items-center gap-2.5">
          <span className={cn("h-2 w-2 shrink-0 rounded-full", clean ? "bg-green" : "bg-amber")} aria-hidden="true" />
          <div className="min-w-0">
            <h2 className="text-sm font-semibold tracking-tight text-ink">{data.summary.baseline}</h2>
            <p className="mt-0.5 truncate text-[11px] text-muted">{data.summary.detail}</p>
          </div>
        </div>
        <span className="shrink-0 font-mono text-[11px] text-faint">最近扫描 {data.summary.lastCheck || "—"}</span>
      </section>

      <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1.6fr)_minmax(320px,1fr)]">
        <div className="grid min-w-0 content-start gap-4">
          <section className="overflow-hidden rounded-panel border border-line bg-surface">
            <header className="flex items-center justify-between gap-3 border-b border-line px-4 py-3">
              <div className="flex items-baseline gap-2">
                <h3 className="text-sm font-semibold text-ink">待处理事项</h3>
                <span className="font-mono text-[11px] text-faint">{data.queue.length} 项</span>
              </div>
              <span className="text-[11px] text-faint">按优先级排序</span>
            </header>
            {data.queue.length ? (
              <div>
                {groupOrder.map((status) => {
                  const meta = groupMeta[status] ?? { tone: "gray", dot: "bg-line-strong" };
                  return (
                    <div key={status}>
                      <div className="flex items-center gap-1.5 bg-surface-2/60 px-4 py-1.5 text-[10px] font-semibold text-muted">
                        <span className={cn("h-1.5 w-1.5 rounded-full", meta.dot)} aria-hidden="true" />
                        {status}
                        <span className="font-mono text-faint">{groups[status].length}</span>
                      </div>
                      {groups[status].map((item) => (
                        <button
                          type="button"
                          key={item.index}
                          onClick={() => navigatePage(item.page)}
                          className="group flex w-full items-center gap-3 border-t border-line/70 px-4 py-2.5 text-left transition-colors hover:bg-surface-2"
                        >
                          <span className="min-w-0 flex-1">
                            <strong className="block truncate text-[13px] font-medium text-ink">{item.title}</strong>
                            <small className="mt-0.5 block truncate text-[11px] text-faint">{item.note}</small>
                          </span>
                          <StatusPill tone={meta.tone}>{item.status}</StatusPill>
                          <span className="shrink-0 text-faint transition-colors group-hover:text-ink"><Icon name="arrow" size={14} /></span>
                        </button>
                      ))}
                    </div>
                  );
                })}
              </div>
            ) : (
              <div className="grid place-content-center gap-1 px-6 py-10 text-center">
                <span className="mx-auto grid h-9 w-9 place-items-center rounded-full bg-green/10 text-green"><Icon name="check" size={18} /></span>
                <p className="mt-1 text-[13px] font-semibold text-ink">全部事项已处理</p>
                <p className="text-[11px] text-muted">没有待审核指标、待映射字段或待复核拦截记录。</p>
              </div>
            )}
          </section>

          <section className="overflow-hidden rounded-panel border border-line bg-surface">
            <h3 className="flex items-center justify-between border-b border-line px-4 py-3 text-sm font-semibold text-ink">
              最近治理活动
              <span className="text-[11px] font-normal text-faint">只读审计 · 最近 {activity.length} 条</span>
            </h3>
            {activity.length ? (
              <div className="divide-y divide-line/70">
                {activity.map((item, index) => (
                  <div key={`${item.time}-${index}`} className="flex items-center gap-3 px-4 py-2.5 text-[12px]">
                    <span className="w-20 shrink-0 font-mono text-[11px] text-faint">{item.time}</span>
                    <span className="min-w-0 flex-1 truncate text-ink">{item.action || "—"}</span>
                    <span className="hidden shrink-0 font-mono text-[10px] text-faint sm:block" title={item.user}>{item.user ? `${item.user.slice(0, 6)}…${item.user.slice(-4)}` : ""}</span>
                    <StatusPill tone={activityTones[item.result] ?? "gray"}>{item.result}</StatusPill>
                  </div>
                ))}
              </div>
            ) : <p className="px-4 py-6 text-center text-[11px] text-muted">暂无审计记录。</p>}
          </section>
        </div>

        <aside className="grid min-w-0 content-start gap-4">
          <section className="overflow-hidden rounded-panel border border-line bg-surface">
            <h3 className="border-b border-line px-4 py-2.5 text-[11px] font-semibold text-muted">治理健康</h3>
            <div className="divide-y divide-line/70">
              {data.health.map((item) => {
                const pct = percentOf(item.value);
                return (
                  <button
                    type="button"
                    key={item.label}
                    onClick={() => navigatePage(item.page)}
                    className="group flex w-full items-center gap-3 px-4 py-2.5 text-left transition-colors hover:bg-surface-2"
                  >
                    <span className={cn("shrink-0", healthTones[item.tone] ?? healthTones.blue)}><Icon name={item.icon} size={15} /></span>
                    <span className="min-w-0 flex-1">
                      <span className="flex items-baseline justify-between gap-2">
                        <strong className="truncate text-[13px] font-medium text-ink">{item.label}</strong>
                        <span className={cn("shrink-0 font-mono text-[13px] font-semibold", healthTones[item.tone] ?? "text-ink")}>{item.value}</span>
                      </span>
                      {pct != null && (
                        <span className="mt-1.5 block h-1 overflow-hidden rounded-full bg-surface-3">
                          <span className={cn("block h-full rounded-full", healthBars[item.tone] ?? "bg-primary")} style={{ width: `${pct}%` }} />
                        </span>
                      )}
                      <small className="mt-1 block truncate text-[10px] text-faint">{item.note}</small>
                    </span>
                  </button>
                );
              })}
            </div>
          </section>

          <section className="overflow-hidden rounded-panel border border-line bg-surface">
            <h3 className="border-b border-line px-4 py-2.5 text-[11px] font-semibold text-muted">主题语义覆盖</h3>
            <div className="grid gap-2.5 px-4 py-3">
              {data.topics.length ? data.topics.map((item) => (
                <div key={item.name}>
                  <div className="flex items-baseline justify-between text-[11px]">
                    <span className="text-ink-2">{item.name}</span>
                    <span className="font-mono text-faint">{item.coverage}%</span>
                  </div>
                  <span className="mt-1 block h-1.5 overflow-hidden rounded-full bg-surface-3">
                    <span className={cn("block h-full rounded-full", item.coverage >= 90 ? "bg-teal" : item.coverage >= 70 ? "bg-primary" : "bg-amber")} style={{ width: `${Math.max(0, Math.min(100, item.coverage))}%` }} />
                  </span>
                </div>
              )) : <p className="text-[11px] text-muted">尚未配置业务主题。</p>}
            </div>
          </section>

          <section className="overflow-hidden rounded-panel border border-line bg-surface">
            <h3 className="border-b border-line px-4 py-2.5 text-[11px] font-semibold text-muted">数据源新鲜度</h3>
            {sources.length ? (
              <div className="divide-y divide-line/70">
                {sources.map((src) => (
                  <button
                    type="button"
                    key={src.id}
                    onClick={() => navigatePage("resources")}
                    className="group flex w-full items-center gap-2.5 px-4 py-2.5 text-left transition-colors hover:bg-surface-2"
                  >
                    <span className="shrink-0 text-muted"><Icon name="database" size={15} /></span>
                    <span className="min-w-0 flex-1">
                      <strong className="block truncate text-[13px] font-medium text-ink">{src.name}</strong>
                      <small className="mt-0.5 block truncate text-[10px] text-faint">
                        {sourceTypeLabels[src.type] ?? src.type} · {src.tables} 张表 · 扫描 {src.lastScan || "—"}
                      </small>
                    </span>
                    <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", src.lastScan ? "bg-teal" : "bg-amber")} aria-hidden="true" />
                  </button>
                ))}
              </div>
            ) : <p className="px-4 py-6 text-center text-[11px] text-muted">尚未接入数据源。</p>}
          </section>
        </aside>
      </div>
    </PageFrame>
  );
}
