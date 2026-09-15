import { useEffect, useMemo, useState } from "react";

import {
  createMonitoringRule,
  listMonitoringRules,
  listMonitoringRuns,
  pauseMonitoringRule,
  publishMonitoringRule,
  resumeMonitoringRule,
  runMonitoringRule,
  retryMonitoringRun,
  validateMonitoringRule,
} from "../../api/monitoring";
import { Button, Card, EmptyState, ErrorNotice, PageFrame, RouteSkeleton, StatusPill } from "../../components/ui";

const PRESETS = {
  freshness: {
    name: "生产数据断流监测", code: "production-data-stale", templateCode: "source-data-stale", metricName: "数据更新延迟", entityType: "source", grain: "hour", scheduleSeconds: 3600,
    queryPlan: { kind: "freshness", table: "mes_process_output", timeField: "stat_date" },
    condition: { operator: "gt", openThreshold: 7200, closeThreshold: 3600, pendingPeriods: 1, recoveryPeriods: 1 },
  },
  yield: {
    name: "工序良率低于红线", code: "process-yield-low", templateCode: "process-yield-drop", metricName: "工序良率", entityType: "process", grain: "day", scheduleSeconds: 86400,
    queryPlan: { kind: "ratio", table: "mes_process_output", timeField: "stat_date", entityField: "process_id", numeratorField: "good_qty", denominatorFields: ["input_qty"] },
    condition: { operator: "lt", openThreshold: 0.9, closeThreshold: 0.93, pendingPeriods: 2, recoveryPeriods: 2 },
  },
  downtime: {
    name: "非计划停机超时", code: "unplanned-downtime-high", templateCode: "unplanned-downtime", metricName: "非计划停机时长", entityType: "equipment", grain: "day", scheduleSeconds: 86400,
    queryPlan: { kind: "sum", table: "eqp_downtime_record", timeField: "start_time", entityField: "equipment_id", valueField: "downtime_minutes", filters: [{ field: "is_planned", operator: "eq", value: false }] },
    condition: { operator: "gt", openThreshold: 120, closeThreshold: 30, pendingPeriods: 1, recoveryPeriods: 1 },
  },
};
const RULE_STATE = { draft: ["草稿", "gray"], published: ["运行中", "green"], paused: ["已暂停", "amber"], retired: ["已被新版本替代", "gray"] };

export default function MonitoringRulesPage() {
  const [rules, setRules] = useState(null);
  const [runs, setRuns] = useState([]);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState("");
  const [preset, setPreset] = useState("freshness");
  const [sourceId, setSourceId] = useState("1");
  const [recipientIds, setRecipientIds] = useState("");
  const load = async () => {
    setError(null);
    try { const [nextRules, nextRuns] = await Promise.all([listMonitoringRules(), listMonitoringRuns()]); setRules(nextRules); setRuns(nextRuns); } catch (nextError) { setError(nextError); }
  };
  useEffect(() => { load(); }, []);
  const runCounts = useMemo(() => runs.reduce((acc, item) => ({ ...acc, [item.status]: (acc[item.status] ?? 0) + 1 }), {}), [runs]);
  const act = async (id, action, success) => {
    setBusy(`${id}:${action}`); setNotice("");
    try { await action(id); await load(); setNotice(success); } catch (nextError) { setNotice(nextError.message); } finally { setBusy(""); }
  };
  const create = async () => {
    setBusy("create"); setNotice("");
    try {
      const base = PRESETS[preset];
      await createMonitoringRule({ ...base, sourceId: Number(sourceId), qualityGate: { minimumSamples: preset === "freshness" ? 0 : 1 }, severityPolicy: { default: preset === "freshness" ? "high" : "medium" }, notificationPolicy: { userIds: recipientIds.split(/[,，\s]+/).filter(Boolean), channels: ["inbox", "feishu", "dingtalk"] } });
      await load(); setNotice("规则草稿已创建。验证通过后再发布，发布前不会执行。");
    } catch (nextError) { setNotice(nextError.message); } finally { setBusy(""); }
  };
  if (error) return <PageFrame><ErrorNotice title="监控规则加载失败" detail={error.message} onRetry={load} /></PageFrame>;
  if (!rules) return <RouteSkeleton />;
  return (
    <PageFrame className="gap-4">
      <header className="border-b border-line pb-3"><p className="text-[13px] text-muted">规则由结构化字段编译为只读 SQL；业务端只看结果，不能关闭或修改告警。</p></header>
      <div className="grid grid-cols-2 divide-y divide-line overflow-hidden rounded-section border border-line bg-paper sm:grid-cols-4 sm:divide-y-0 sm:divide-x"><Metric label="规则数" value={rules.length} /><Metric label="排队任务" value={runCounts.queued ?? 0} /><Metric label="成功任务" value={runCounts.succeeded ?? 0} /><Metric label="死信任务" value={runCounts.dead_letter ?? 0} danger={(runCounts.dead_letter ?? 0) > 0} /></div>
      {notice && <p className="rounded-control border border-line bg-surface p-3 text-sm" role="status">{notice}</p>}
      <Card className="p-4">
        <h3 className="font-semibold">从安全模板创建规则</h3>
        <div className="mt-3 grid gap-2 md:grid-cols-[minmax(180px,1fr)_120px_minmax(220px,1fr)_auto]">
          <select className="input-control h-9" value={preset} onChange={(event) => setPreset(event.target.value)} aria-label="监控模板"><option value="freshness">数据断流</option><option value="yield">工序良率</option><option value="downtime">非计划停机</option></select>
          <input className="input-control h-9" value={sourceId} onChange={(event) => setSourceId(event.target.value)} inputMode="numeric" placeholder="数据源 ID" aria-label="数据源 ID" />
          <input className="input-control h-9" value={recipientIds} onChange={(event) => setRecipientIds(event.target.value)} placeholder="通知用户 ID，逗号分隔" aria-label="通知用户 ID" />
          <Button variant="primary" disabled={busy === "create"} onClick={create}>{busy === "create" ? "创建中…" : "创建草稿"}</Button>
        </div>
        <p className="mt-2 text-xs text-muted">模板字段必须已在元数据系统登记，否则验证和发布会被拒绝。接收人留空时，通知所有拥有该数据源监控权限的启用用户。</p>
      </Card>
      <Card className="overflow-hidden">
        {rules.length ? <div className="overflow-x-auto"><table className="data-table min-w-[900px]"><thead><tr><th>规则</th><th>指标 / 粒度</th><th>数据源</th><th>状态</th><th>版本</th><th className="text-right">操作</th></tr></thead><tbody>{rules.map((rule) => { const [label, tone] = RULE_STATE[rule.status] ?? [rule.status, "gray"]; return <tr key={rule.id}><td><strong className="block">{rule.name}</strong><span className="text-xs text-muted">{rule.code}</span></td><td>{rule.metricName} / {rule.grain}</td><td>#{rule.sourceId}</td><td><StatusPill tone={tone}>{label}</StatusPill></td><td>v{rule.version}</td><td><div className="flex justify-end gap-2">{rule.status === "draft" && <><Button size="sm" disabled={busy.startsWith(rule.id)} onClick={() => act(rule.id, validateMonitoringRule, "规则验证通过，未执行任何业务查询。")}>验证</Button><Button size="sm" variant="primary" disabled={busy.startsWith(rule.id)} onClick={() => act(rule.id, publishMonitoringRule, "规则已发布，Worker 将按周期执行。")}>发布</Button></>}{rule.status === "published" && <><Button size="sm" disabled={busy.startsWith(rule.id)} onClick={() => act(rule.id, runMonitoringRule, "监控任务已进入持久化队列。")}>立即运行</Button><Button size="sm" disabled={busy.startsWith(rule.id)} onClick={() => act(rule.id, pauseMonitoringRule, "规则已暂停，不再创建新任务。")}>暂停</Button></>}{rule.status === "paused" && <Button size="sm" variant="primary" disabled={busy.startsWith(rule.id)} onClick={() => act(rule.id, resumeMonitoringRule, "规则已恢复运行，将立即重新参与调度。")}>恢复运行</Button>}</div></td></tr>; })}</tbody></table></div> : <EmptyState title="还没有监控规则" description="从上方模板创建第一条规则。" className="m-4" />}
      </Card>
      <Card className="overflow-hidden">
        <div className="border-b border-line p-4"><h3 className="font-semibold">最近运行</h3><p className="mt-1 text-xs text-muted">任务使用数据库租约，服务重启后可重新认领；死信可安全重试。</p></div>
        {runs.length ? <div className="overflow-x-auto"><table className="data-table min-w-[760px]"><thead><tr><th>周期</th><th>规则 ID</th><th>状态</th><th>尝试</th><th>错误</th><th className="text-right">操作</th></tr></thead><tbody>{runs.slice(0, 20).map((run) => <tr key={run.id}><td>{run.logicalPeriod}</td><td className="font-mono text-xs">{run.ruleId}</td><td>{run.status}</td><td>{run.attemptCount}</td><td className="max-w-64 truncate text-xs text-muted">{run.error || "—"}</td><td className="text-right">{["dead_letter", "retry_wait"].includes(run.status) && <Button size="sm" disabled={busy.startsWith(run.id)} onClick={() => act(run.id, retryMonitoringRun, "失败任务已重新排队。")}>重试</Button>}</td></tr>)}</tbody></table></div> : <EmptyState title="暂无运行记录" className="m-4" />}
      </Card>
    </PageFrame>
  );
}

function Metric({ label, value, danger }) { return <div className="px-4 py-3 text-center"><strong className={`font-mono text-lg font-semibold ${danger ? "text-critical" : "text-ink"}`}>{value}</strong><span className="mt-1 block text-xs text-muted">{label}</span></div>; }
