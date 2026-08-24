import { useEffect, useMemo, useState } from "react";
import { listAnalysisSkills, reviewAnalysisSkill } from "../../api/skills";
import { Button, Card, EmptyState, PageFrame, SectionHeader, StatusPill } from "../../components/ui";

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
    <PageFrame>
      <Card>
        <SectionHeader eyebrow="程序性知识治理" title="分析技能" />
        <div className="grid gap-4 border-t border-line px-5 py-4 sm:grid-cols-3">
          <Metric label="当前列表" value={skills.length} />
          <Metric label="关联运行证据" value={evidenceCount} />
          <Metric label="生效边界" value="审核后" textual />
        </div>
      </Card>
      <Card>
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-5 py-3.5">
          <div className="flex rounded-control bg-surface-3 p-0.5">
            {tabs.map((tab) => <button key={tab.id} type="button" onClick={() => setStatus(tab.id)} className={`rounded-control px-3 py-1.5 text-xs font-semibold transition-colors ${status === tab.id ? "bg-surface text-ink shadow-card" : "text-muted hover:text-ink"}`}>{tab.label}</button>)}
          </div>
          <span className="text-xs text-faint">技能只影响分析方法，不扩大数据访问权限</span>
        </div>
        {loading ? <div className="px-5 py-12 text-center text-sm text-faint">正在读取技能…</div>
          : error ? <div className="px-5 py-12 text-center text-sm text-red">{error}</div>
            : skills.length === 0 ? <EmptyState title="当前没有技能" description="高质量分析收到反馈后会形成待审核候选。" />
              : <div className="divide-y divide-line">{skills.map((skill) => <SkillRow key={skill.id} skill={skill} onReview={review} />)}</div>}
      </Card>
    </PageFrame>
  );
}

function Metric({ label, value, textual }) {
  return <div><span className="text-[11px] text-faint">{label}</span><strong className={`mt-1 block ${textual ? "text-sm" : "font-mono text-xl"} text-ink`}>{value}</strong></div>;
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
