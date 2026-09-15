import { useEffect, useMemo, useState } from "react";
import { listAnalysisSkills, reviewAnalysisSkill } from "../../api/skills";
import { Button, Card, EmptyState, Icon, PageFrame, StatusPill } from "../../components/ui";

const tabs = [
  { id: "candidate", label: "待审核" },
  { id: "published", label: "已发布" },
  { id: "retired", label: "已停用" },
];

export default function SkillGovernancePage() {
  const [status, setStatus] = useState("candidate");
  const [skills, setSkills] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const reload = async () => {
    setLoading(true);
    setError("");
    try { setSkills(await listAnalysisSkills(status)); }
    catch (reason) { setError(reason.message); }
    finally { setLoading(false); }
  };
  useEffect(() => { reload(); }, [status]);
  const evidenceCount = useMemo(() => skills.reduce((sum, item) => sum + item.evidenceRunIds.length, 0), [skills]);

  const review = async (skillId, action) => {
    await reviewAnalysisSkill(skillId, action);
    await reload();
  };

  return (
    <PageFrame className="max-w-[1360px] gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line pb-3">
        <div className="flex min-w-0 items-center gap-2.5">
          <span className="grid h-8 w-8 shrink-0 place-items-center rounded-control bg-primary-soft text-primary"><Icon name="knowledge" size={16} /></span>
          <div className="min-w-0">
            <h2 className="text-sm font-semibold tracking-tight text-ink">分析技能</h2>
            <p className="mt-0.5 text-[11px] text-muted">程序性知识治理 · 技能只影响分析方法，不扩大数据访问权限</p>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-3 divide-x divide-line overflow-hidden rounded-panel border border-line bg-paper">
        <div className="px-4 py-3"><span className="block text-[11px] text-muted">当前列表</span><strong className="mt-1 block font-mono text-base font-semibold leading-none text-ink">{skills.length}</strong></div>
        <div className="px-4 py-3"><span className="block text-[11px] text-muted">关联运行证据</span><strong className="mt-1 block font-mono text-base font-semibold leading-none text-ink">{evidenceCount}</strong></div>
        <div className="px-4 py-3"><span className="block text-[11px] text-muted">生效边界</span><strong className="mt-1 block text-sm font-semibold leading-none text-ink">审核后</strong></div>
      </div>

      <Card>
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-4 py-3">
          <div className="flex rounded-control bg-surface-3 p-0.5">
            {tabs.map((tab) => <button key={tab.id} type="button" onClick={() => setStatus(tab.id)} aria-pressed={status === tab.id} className={`rounded-control px-3 py-1.5 text-xs font-semibold transition-colors ${status === tab.id ? "bg-surface text-ink shadow-card" : "text-muted hover:text-ink"}`}>{tab.label}</button>)}
          </div>
        </div>
        {loading ? <div className="px-5 py-12 text-center text-sm text-faint">正在读取技能…</div>
          : error ? <div className="px-5 py-12 text-center text-sm text-red">{error}</div>
            : skills.length === 0 ? <EmptyState title="当前没有技能" description="高质量分析收到反馈后会形成待审核候选。" />
              : <div className="divide-y divide-line">{skills.map((skill) => <SkillRow key={skill.id} skill={skill} onReview={review} />)}</div>}
      </Card>
    </PageFrame>
  );
}

function SkillRow({ skill, onReview }) {
  const tone = skill.status === "published" ? "green" : skill.status === "candidate" ? "amber" : "gray";
  return (
    <article className="grid gap-4 px-5 py-4 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2"><h3 className="font-semibold text-ink">{skill.name}</h3><StatusPill tone={tone}>v{skill.version} · {skill.status}</StatusPill><span className="text-[11px] text-faint">{skill.sourceId ? `数据源 ${skill.sourceId}` : "企业公共"}</span></div>
        <p className="mt-1.5 text-xs leading-relaxed text-muted">{skill.description}</p>
        <div className="mt-2 flex flex-wrap gap-1.5">{skill.steps.slice(0, 3).map((step, index) => <span key={step} className="rounded-tag bg-surface-3 px-2 py-1 text-[11px] text-ink-2">{index + 1}. {step}</span>)}</div>
        <p className="mt-2 text-[11px] text-faint">{skill.evidenceRunIds.length} 条运行证据 · 置信度 {Math.round(skill.confidence * 100)}%</p>
      </div>
      <div className="flex gap-2">
        {skill.status === "candidate" && <><Button variant="secondary" onClick={() => onReview(skill.id, "reject")}>拒绝</Button><Button variant="primary" onClick={() => onReview(skill.id, "publish")}>审核发布</Button></>}
        {skill.status === "published" && <Button variant="secondary" onClick={() => onReview(skill.id, "retire")}>停用</Button>}
      </div>
    </article>
  );
}
