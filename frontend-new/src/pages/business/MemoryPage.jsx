import { useEffect, useMemo, useState } from "react";
import {
  correctMemory, createMemory, forgetMemory, getMemorySettings, listMemories,
} from "../../api/memory";
import { Button, Card, EmptyState, ErrorNotice, Icon, Modal, PageFrame, RouteSkeleton, StatusPill } from "../../components/ui";

const kindMeta = {
  preference: { label: "个人偏好", tone: "blue", description: "你明确告诉 ForgeSight 的展示和分析习惯" },
  episodic: { label: "调查经验", tone: "green", description: "成功调查的精简问题、结论与证据引用" },
  semantic: { label: "事实记忆", tone: "amber", description: "经过确认、带有效期的业务事实" },
  procedural: { label: "分析方法", tone: "gray", description: "待治理后才能提升为团队技能的方法" },
};

export default function MemoryPage() {
  const [items, setItems] = useState(null);
  const [settings, setSettings] = useState(null);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState("all");
  const [editor, setEditor] = useState(null);

  const load = async () => {
    setError("");
    try {
      const [memoryItems, feature] = await Promise.all([listMemories(), getMemorySettings()]);
      setItems(memoryItems.filter((item) => !["deleted", "superseded"].includes(item.status)));
      setSettings(feature);
    } catch (reason) { setError(reason.message); }
  };
  useEffect(() => { load(); }, []);

  const visible = useMemo(() => (items ?? []).filter((item) => filter === "all" || item.kind === filter), [filter, items]);
  const counts = useMemo(() => (items ?? []).reduce((acc, item) => ({ ...acc, [item.kind]: (acc[item.kind] ?? 0) + 1 }), {}), [items]);

  const save = async () => {
    if (!editor?.content.trim()) return;
    if (editor.id) await correctMemory(editor.id, editor.content);
    else await createMemory({ content: editor.content, title: editor.title, kind: "preference" });
    setEditor(null);
    await load();
  };

  const remove = async (item) => {
    if (!window.confirm(`确认让 ForgeSight 遗忘“${item.title}”吗？`)) return;
    await forgetMemory(item.id);
    await load();
  };

  if (error) return <PageFrame><ErrorNotice title="记忆加载失败" detail={error} onRetry={load} /></PageFrame>;
  if (!items || !settings) return <RouteSkeleton />;

  return (
    <PageFrame>
      <section className="overflow-hidden rounded-card border border-line bg-surface shadow-card">
        <div className="grid lg:grid-cols-[minmax(0,1.45fr)_minmax(300px,.75fr)]">
          <div className="px-5 py-4.5 lg:px-6">
            <div className="flex items-center gap-2 text-xs font-semibold text-primary"><Icon name="memory" size={16} />个人智能设置</div>
            <h2 className="mt-1.5 text-lg font-semibold tracking-tight text-ink">让 ForgeSight 按你的分析习惯工作</h2>
            <p className="mt-2 max-w-3xl text-[13px] leading-6 text-muted">功能开启后，系统会在完成调查后自动整理明确偏好和有效经验。你无需逐条维护，也可以随时查看、纠正或遗忘。</p>
            <div className="mt-5 flex flex-wrap items-center gap-3">
              <span className={`inline-flex h-9 items-center gap-2 rounded-control px-3 text-xs font-semibold ${settings.enabled ? "bg-green-soft text-green" : "bg-surface-3 text-muted"}`}><span className="h-1.5 w-1.5 rounded-full bg-current" />{settings.enabled ? "自动学习已开启" : "自动学习暂未开启"}</span>
              <Button variant="secondary" onClick={() => setEditor({ title: "", content: "" })}><Icon name="plus" size={14} />补充明确偏好</Button>
              {!settings.enabled && <span className="text-[11px] text-faint">可提前补充，管理员开启后自动用于相关分析</span>}
            </div>
          </div>
          <div className="grid grid-cols-2 border-t border-line bg-surface-2 lg:border-l lg:border-t-0">
            <Metric label="个人偏好" value={counts.preference ?? 0} hint="可随时修改" />
            <Metric label="调查经验" value={counts.episodic ?? 0} hint="保留证据来源" />
            <Metric label="候选内容" value={(items ?? []).filter((item) => item.status === "candidate").length} hint="不会直接影响结果" />
            <Metric label="隔离范围" value="账号 + 数据源" textual hint="先过滤，再召回" />
          </div>
        </div>
      </section>

      <Card className="overflow-hidden">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-5 py-3.5">
          <div className="flex rounded-control bg-surface-3 p-0.5">
            {[{ id: "all", label: "全部" }, { id: "preference", label: "个人偏好" }, { id: "episodic", label: "调查经验" }].map((tab) => (
              <button key={tab.id} type="button" onClick={() => setFilter(tab.id)} className={`rounded-control px-3 py-1.5 text-xs font-semibold transition-colors ${filter === tab.id ? "bg-surface text-ink shadow-card" : "text-muted hover:text-ink"}`}>{tab.label}</button>
            ))}
          </div>
          <span className="text-xs text-faint">共 {visible.length} 条 · 只在与当前问题相关时使用</span>
        </div>
        {visible.length ? <div className="divide-y divide-line">{visible.map((item) => <MemoryRow key={item.id} item={item} onEdit={() => setEditor({ id: item.id, title: item.title, content: item.content })} onRemove={() => remove(item)} />)}</div>
          : <EmptyState title="尚未形成可复用的偏好或经验" description={settings.enabled ? "继续正常发起分析即可。系统会在后台整理明确偏好和成功调查，不需要你手动维护。" : "自动学习目前未开启。你仍可提前补充明确偏好，开启后系统会在相关分析中使用。"} />}
      </Card>

      <Modal open={Boolean(editor)} onClose={() => setEditor(null)} label={editor?.id ? "纠正记忆" : "补充明确偏好"}>
        <div className="border-b border-line px-5 py-4"><h3 className="font-semibold text-ink">{editor?.id ? "纠正这条记忆" : "补充一条长期分析偏好"}</h3><p className="mt-1 text-xs text-muted">只填写希望长期沿用的习惯，例如：查看趋势时默认使用折线图。</p></div>
        <div className="px-5 py-5"><textarea autoFocus value={editor?.content ?? ""} onChange={(event) => setEditor((current) => ({ ...current, content: event.target.value }))} className="min-h-32 w-full resize-y rounded-control border border-line bg-surface px-3 py-2 text-sm leading-6 text-ink outline-none focus:border-primary/50" maxLength={4000} /></div>
        <div className="flex justify-end gap-2 border-t border-line px-5 py-3"><Button onClick={() => setEditor(null)}>取消</Button><Button variant="primary" disabled={!editor?.content.trim()} onClick={save}>保存</Button></div>
      </Modal>
    </PageFrame>
  );
}

function Metric({ label, value, hint, textual = false }) {
  return <div className="border-b border-r border-line px-5 py-4 last:border-b-0"><span className="text-[11px] text-faint">{label}</span><strong className={`mt-1 block text-ink ${textual ? "text-sm" : "font-mono text-xl"}`}>{value}</strong><span className="mt-1 block text-[11px] text-muted">{hint}</span></div>;
}

function MemoryRow({ item, onEdit, onRemove }) {
  const meta = kindMeta[item.kind] ?? kindMeta.semantic;
  return (
    <article className="grid gap-4 px-5 py-4 lg:grid-cols-[42px_minmax(0,1fr)_auto] lg:items-start">
      <span className="grid h-10 w-10 place-items-center rounded-tag bg-primary-soft text-primary"><Icon name={item.kind === "preference" ? "settings" : "history"} size={17} /></span>
      <div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><h3 className="font-semibold text-ink">{item.title}</h3><StatusPill tone={meta.tone}>{meta.label}</StatusPill><StatusPill tone="gray">{item.explicit ? "用户补充" : "自动整理"}</StatusPill></div><p className="mt-1.5 whitespace-pre-line text-xs leading-5 text-muted">{item.content}</p><p className="mt-2 text-[11px] text-faint">置信度 {Math.round(item.confidence * 100)}% · 使用 {item.accessCount} 次 · {item.evidence.length} 条证据</p></div>
      <div className="flex gap-2"><Button size="sm" onClick={onEdit}>纠正</Button><Button size="sm" variant="ghost" onClick={onRemove}>遗忘</Button></div>
    </article>
  );
}
