import { useEffect, useMemo, useState } from "react";
import {
  consolidateMemories, getMemoryGovernanceSummary, listGovernanceMemories,
  promoteMemory, updateMemorySettings,
} from "../../api/memory";
import { Button, Card, EmptyState, ErrorNotice, Icon, PageFrame, RouteSkeleton, StatusPill } from "../../components/ui";

const tabs = [
  { id: "all", label: "全部" }, { id: "candidate", label: "待确认" },
  { id: "active", label: "已生效" }, { id: "superseded", label: "已覆盖" },
];

export default function MemoryGovernancePage() {
  const [summary, setSummary] = useState(null);
  const [items, setItems] = useState(null);
  const [status, setStatus] = useState("candidate");
  const [error, setError] = useState("");
  const load = async () => {
    setError("");
    try {
      const [nextSummary, nextItems] = await Promise.all([
        getMemoryGovernanceSummary(), listGovernanceMemories({ status: status === "all" ? undefined : status }),
      ]);
      setSummary(nextSummary); setItems(nextItems);
    } catch (reason) { setError(reason.message); }
  };
  useEffect(() => { load(); }, [status]);

  const sensitiveCandidates = useMemo(() => (items ?? []).filter((item) => item.confidence < 0.7).length, [items]);
  if (error) return <PageFrame><ErrorNotice title="记忆治理加载失败" detail={error} onRetry={load} /></PageFrame>;
  if (!summary || !items) return <RouteSkeleton />;

  const toggle = async () => { await updateMemorySettings(!summary.settings.enabled, summary.settings.config); await load(); };
  const consolidate = async () => { await consolidateMemories(); await load(); };
  const promote = async (id) => { await promoteMemory(id); await load(); };

  return (
    <PageFrame>
      <Card className="overflow-hidden">
        <div className="flex flex-wrap items-start justify-between gap-4 px-5 py-5">
          <div><span className="text-[11px] font-semibold tracking-[.08em] text-primary">MEMORY CONTROL PLANE</span><h2 className="mt-1 text-xl font-semibold text-ink">记忆治理与实验开关</h2><p className="mt-1 max-w-2xl text-xs leading-5 text-muted">记忆召回按运行快照进入 A/B 对照。关闭后，新运行不召回也不沉淀记忆，现有内容仍保留供审计与恢复。</p></div>
          <div className="flex gap-2"><Button onClick={consolidate}><Icon name="refresh" size={14} />执行合并与过期</Button><Button variant={summary.settings.enabled ? "secondary" : "primary"} onClick={toggle}>{summary.settings.enabled ? "关闭记忆增强" : "开启记忆增强"}</Button></div>
        </div>
        <div className="grid border-t border-line sm:grid-cols-2 lg:grid-cols-5">
          <Metric label="全部记忆" value={summary.total} />
          <Metric label="已生效" value={summary.byStatus.active ?? 0} />
          <Metric label="待确认" value={summary.byStatus.candidate ?? 0} />
          <Metric label="召回次数" value={summary.recallCount} />
          <Metric label="当前实验" value={summary.settings.variant.toUpperCase()} textual />
        </div>
      </Card>

      <Card className="overflow-hidden">
        <div className="grid lg:grid-cols-[300px_1fr_1fr]">
          <div className="border-b border-line px-5 py-4 lg:border-b-0 lg:border-r"><span className="text-[11px] font-semibold tracking-[.08em] text-primary">A/B OBSERVATION</span><h3 className="mt-1 font-semibold text-ink">开启前后是否真的更好</h3><p className="mt-1 text-xs leading-5 text-muted">每次运行保存开关快照，不用主观印象判断记忆有没有帮助。</p></div>
          <ExperimentCell label="未启用记忆" data={summary.experiment.off} />
          <ExperimentCell label="启用记忆" data={summary.experiment.on} active />
        </div>
      </Card>

      <section className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_320px]">
        <Card className="overflow-hidden">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-5 py-3.5"><div className="flex rounded-control bg-surface-3 p-0.5">{tabs.map((tab) => <button key={tab.id} onClick={() => setStatus(tab.id)} type="button" className={`rounded-control px-3 py-1.5 text-xs font-semibold ${status === tab.id ? "bg-surface text-ink shadow-card" : "text-muted"}`}>{tab.label}</button>)}</div><span className="text-xs text-faint">低置信候选 {sensitiveCandidates} 条</span></div>
          {items.length ? <div className="divide-y divide-line">{items.map((item) => <article key={item.id} className="grid gap-3 px-5 py-4 lg:grid-cols-[minmax(0,1fr)_auto]"><div><div className="flex flex-wrap items-center gap-2"><strong className="text-sm text-ink">{item.title}</strong><StatusPill tone={item.status === "active" ? "green" : item.status === "candidate" ? "amber" : "gray"}>{item.status}</StatusPill><span className="text-[11px] text-faint">{item.kind} · 数据源 {item.sourceId ?? "企业公共"}</span></div><p className="mt-1.5 line-clamp-2 text-xs leading-5 text-muted">{item.content}</p><p className="mt-2 text-[11px] text-faint">账号 {item.userId ?? "公共"} · 置信度 {Math.round(item.confidence * 100)}% · {item.evidence.length} 条证据</p></div>{["episodic", "procedural"].includes(item.kind) && <Button size="sm" onClick={() => promote(item.id)}>提升为候选技能</Button>}</article>)}</div> : <EmptyState title="当前没有记忆" description="运行成功、用户明确偏好或管理员录入后会出现在这里。" />}
        </Card>
        <Card className="h-fit p-5"><div className="flex items-center gap-2"><Icon name="shield" className="text-green" /><h3 className="font-semibold text-ink">不可逾越的边界</h3></div><div className="mt-4 space-y-3">{summary.guardrails.map((item, index) => <div key={item} className="flex gap-2.5 text-xs leading-5 text-muted"><span className="grid h-5 w-5 shrink-0 place-items-center rounded-full bg-green-soft font-mono text-[10px] text-green">{index + 1}</span>{item}</div>)}</div><div className="mt-5 rounded-control bg-surface-3 p-3 text-[11px] leading-5 text-muted">记忆与业务知识库分开管理。经验只能作为上下文证据，不能偷偷变成正式指标或数据库权限。</div></Card>
      </section>
    </PageFrame>
  );
}

function Metric({ label, value, textual = false }) {
  return <div className="border-b border-line px-5 py-4 sm:border-b-0 sm:border-r"><span className="text-[11px] text-faint">{label}</span><strong className={`mt-1 block text-ink ${textual ? "text-sm" : "font-mono text-xl"}`}>{value}</strong></div>;
}

function ExperimentCell({ label, data, active = false }) {
  return <div className={`border-b border-line px-5 py-4 lg:border-b-0 lg:border-r ${active ? "bg-primary-soft/40" : ""}`}><div className="flex items-center justify-between"><span className="text-xs font-semibold text-ink">{label}</span><StatusPill tone={active ? "blue" : "gray"}>{data.runs} 次运行</StatusPill></div><div className="mt-4 grid grid-cols-3 gap-3"><Mini label="成功率" value={`${Math.round(data.successRate * 100)}%`} /><Mini label="平均 Token" value={data.avgTokens.toLocaleString()} /><Mini label="平均耗时" value={data.avgDurationMs ? `${(data.avgDurationMs / 1000).toFixed(1)}s` : "—"} /></div></div>;
}

function Mini({ label, value }) {
  return <div><span className="text-[10px] text-faint">{label}</span><strong className="mt-1 block font-mono text-sm text-ink">{value}</strong></div>;
}
