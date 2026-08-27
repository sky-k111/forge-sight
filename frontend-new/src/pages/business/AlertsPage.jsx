import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { listMonitoringAlerts } from "../../api/monitoring";
import { Card, EmptyState, ErrorNotice, PageFrame, RouteSkeleton, StatusPill } from "../../components/ui";
import Icon from "../../components/ui/Icon";

const STATES = {
  firing: ["告警中", "red"],
  recovering: ["恢复观察", "amber"],
  resolved: ["已恢复", "green"],
};
const SEVERITIES = {
  critical: ["严重", "red"],
  high: ["高", "amber"],
  medium: ["中", "blue"],
  low: ["低", "gray"],
};

function when(value) {
  return value ? new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(new Date(value)) : "—";
}

export default function AlertsPage() {
  const navigate = useNavigate();
  const [items, setItems] = useState(null);
  const [status, setStatus] = useState("");
  const [severity, setSeverity] = useState("");
  const [error, setError] = useState(null);
  const load = () => {
    setError(null);
    listMonitoringAlerts({ status, severity }).then(setItems).catch(setError);
  };
  useEffect(load, [status, severity]); // eslint-disable-line react-hooks/exhaustive-deps
  const counts = useMemo(() => ({
    firing: items?.filter((item) => item.status === "firing").length ?? 0,
    recovering: items?.filter((item) => item.status === "recovering").length ?? 0,
    resolved: items?.filter((item) => item.status === "resolved").length ?? 0,
  }), [items]);
  if (error) return <PageFrame><ErrorNotice title="异常记录加载失败" detail={error.message} onRetry={load} /></PageFrame>;
  if (!items) return <RouteSkeleton />;
  return (
    <PageFrame className="gap-4">
      <header>
        <span className="section-kicker">ANOMALY RADAR</span>
        <h2 className="mt-1 text-[25px] font-semibold">智能异常监测</h2>
        <p className="mt-2 text-base text-muted">持续监测数据断流、生产、质量、停机和库存指标；这里只读展示告警与证据。</p>
      </header>
      <div className="grid gap-3 sm:grid-cols-3">
        <Metric label="告警中" value={counts.firing} tone="text-critical" />
        <Metric label="恢复观察" value={counts.recovering} tone="text-warning" />
        <Metric label="已自动恢复" value={counts.resolved} tone="text-success" />
      </div>
      <Card className="overflow-hidden">
        <div className="flex flex-wrap gap-2 border-b border-line p-3">
          <select className="input-control h-9 w-auto" value={status} onChange={(event) => setStatus(event.target.value)} aria-label="告警状态">
            <option value="">全部状态</option><option value="firing">告警中</option><option value="recovering">恢复观察</option><option value="resolved">已恢复</option>
          </select>
          <select className="input-control h-9 w-auto" value={severity} onChange={(event) => setSeverity(event.target.value)} aria-label="严重程度">
            <option value="">全部级别</option><option value="critical">严重</option><option value="high">高</option><option value="medium">中</option><option value="low">低</option>
          </select>
        </div>
        {items.length ? (
          <div className="divide-y divide-line">
            {items.map((item) => {
              const [stateLabel, stateTone] = STATES[item.status] ?? [item.status, "gray"];
              const [severityLabel, severityTone] = SEVERITIES[item.severity] ?? [item.severity, "gray"];
              return (
                <button key={item.id} type="button" onClick={() => navigate(`/workspace/alerts/${item.id}`)} className="grid w-full cursor-pointer gap-3 p-4 text-left transition-colors hover:bg-surface-2 sm:grid-cols-[minmax(0,1fr)_auto_auto] sm:items-center">
                  <span className="min-w-0">
                    <span className="flex items-center gap-2"><Icon name="alert" size={17} className="text-critical" /><strong className="truncate text-sm text-ink">{item.title}</strong></span>
                    <span className="mt-1 block text-xs text-muted">{item.entityName || item.entityId} · 最近出现 {when(item.lastSeenAt)} · 样本 {item.sampleSize ?? "—"}</span>
                  </span>
                  <span className="font-mono text-sm">{item.currentValue ?? "—"}</span>
                  <span className="flex gap-2"><StatusPill tone={severityTone}>{severityLabel}</StatusPill><StatusPill tone={stateTone}>{stateLabel}</StatusPill></span>
                </button>
              );
            })}
          </div>
        ) : <EmptyState title="当前没有符合条件的异常" description="监控仍在后台运行，新异常会自动出现在这里并发送通知。" className="m-4" />}
      </Card>
    </PageFrame>
  );
}

function Metric({ label, value, tone }) {
  return <Card className="p-4"><strong className={`font-mono text-[28px] ${tone}`}>{value}</strong><span className="mt-1 block text-sm text-muted">{label}</span></Card>;
}
