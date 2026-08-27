import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { getMonitoringAlert, getMonitoringDeliveries, getMonitoringEvidence } from "../../api/monitoring";
import { Button, Card, EmptyState, ErrorNotice, PageFrame, RouteSkeleton, StatusPill } from "../../components/ui";
import Icon from "../../components/ui/Icon";

const STATE = { firing: ["告警中", "red"], recovering: ["恢复观察", "amber"], resolved: ["已恢复", "green"] };
const SEVERITY = { critical: ["严重", "red"], high: ["高", "amber"], medium: ["中", "blue"], low: ["低", "gray"] };

function time(value) {
  return value ? new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value)) : "—";
}

export default function AlertDetailPage() {
  const { alertId } = useParams();
  const navigate = useNavigate();
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const load = () => Promise.all([getMonitoringAlert(alertId), getMonitoringEvidence(alertId), getMonitoringDeliveries(alertId)])
    .then(([alert, evidence, deliveries]) => setData({ alert, evidence, deliveries })).catch(setError);
  useEffect(() => { load(); }, [alertId]); // eslint-disable-line react-hooks/exhaustive-deps
  if (error) return <PageFrame><ErrorNotice title="异常证据加载失败" detail={error.message} onRetry={load} /></PageFrame>;
  if (!data) return <RouteSkeleton />;
  const { alert, evidence, deliveries } = data;
  const [stateLabel, stateTone] = STATE[alert.status] ?? [alert.status, "gray"];
  const [severityLabel, severityTone] = SEVERITY[alert.severity] ?? [alert.severity, "gray"];
  return (
    <PageFrame className="gap-4">
      <div><Button variant="ghost" onClick={() => navigate("/workspace/alerts")}><Icon name="chevron" size={14} className="rotate-180" />返回异常列表</Button></div>
      <Card className="p-5 sm:p-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div><span className="section-kicker">ALERT EVIDENCE</span><h2 className="mt-2 text-[25px] font-semibold">{alert.title}</h2><p className="mt-2 text-sm text-muted">{alert.entityName || alert.entityId} · 首次出现 {time(alert.firstSeenAt)}</p></div>
          <div className="flex gap-2"><StatusPill tone={severityTone}>{severityLabel}</StatusPill><StatusPill tone={stateTone}>{stateLabel}</StatusPill></div>
        </div>
        <div className="mt-6 grid gap-3 sm:grid-cols-4">
          <Value label="当前值" value={alert.currentValue} />
          <Value label="基线值" value={alert.baselineValue} />
          <Value label="样本量" value={alert.sampleSize} />
          <Value label="连续出现" value={`${alert.recurrenceCount} 次`} />
        </div>
      </Card>
      <div className="grid gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(280px,1fr)]">
        <Card className="p-5"><h3 className="font-semibold">指标趋势与证据</h3><EvidenceChart items={evidence} /><div className="mt-4 overflow-x-auto"><table className="data-table min-w-[580px]"><thead><tr><th>统计周期</th><th>当前值</th><th>基线</th><th>样本量</th><th>数据截至</th></tr></thead><tbody>{evidence.map((item) => <tr key={item.id}><td>{item.logicalPeriod}</td><td className="font-mono">{item.currentValue ?? "—"}</td><td className="font-mono">{item.baselineValue ?? "—"}</td><td>{item.sampleSize}</td><td>{time(item.dataCutoffAt)}</td></tr>)}</tbody></table></div></Card>
        <div className="grid content-start gap-4">
          <Card className="p-5"><h3 className="font-semibold">规则口径</h3><dl className="mt-4 grid gap-3 text-sm"><Row label="监控指标" value={alert.rule?.metricName} /><Row label="统计粒度" value={alert.rule?.grain} /><Row label="触发条件" value={conditionText(alert.rule?.condition)} /><Row label="数据源 ID" value={alert.sourceId} /></dl></Card>
          <Card className="p-5"><h3 className="font-semibold">通知投递</h3>{deliveries.length ? <ul className="mt-3 grid gap-2">{deliveries.map((item) => <li key={item.id} className="flex justify-between rounded-control bg-surface-2 px-3 py-2 text-sm"><span>{item.channel}</span><span>{item.status}</span></li>)}</ul> : <p className="mt-3 text-sm text-muted">暂无外部渠道投递记录，站内消息仍会独立送达。</p>}</Card>
        </div>
      </div>
    </PageFrame>
  );
}

function conditionText(condition = {}) { return `${condition.operator ?? "—"} ${condition.openThreshold ?? "—"}，连续 ${condition.pendingPeriods ?? 1} 个周期触发`; }
function Value({ label, value }) { return <div className="rounded-control bg-surface-2 p-3"><span className="text-xs text-muted">{label}</span><strong className="mt-1 block font-mono text-xl">{value ?? "—"}</strong></div>; }
function Row({ label, value }) { return <div className="flex justify-between gap-4 border-b border-line pb-2"><dt className="text-muted">{label}</dt><dd className="text-right font-medium">{value ?? "—"}</dd></div>; }
function EvidenceChart({ items }) {
  const values = items.map((item) => item.currentValue).filter((value) => Number.isFinite(value));
  const max = Math.max(...values, 1);
  if (!values.length) return <EmptyState title="暂无可绘制的数值证据" className="mt-4" />;
  return <div className="mt-5 flex h-48 items-end gap-2 rounded-control border border-line bg-paper p-4" role="img" aria-label="异常指标历史趋势">{items.map((item) => <div key={item.id} className="group flex min-w-0 flex-1 flex-col items-center justify-end gap-2"><span className="text-[10px] opacity-0 transition-opacity group-hover:opacity-100">{item.currentValue}</span><div className="w-full max-w-12 rounded-t bg-primary" style={{ height: `${Math.max(4, ((item.currentValue ?? 0) / max) * 120)}px` }} title={`${item.logicalPeriod}: ${item.currentValue}`} /><span className="max-w-full truncate text-[10px] text-muted">{item.logicalPeriod}</span></div>)}</div>;
}
