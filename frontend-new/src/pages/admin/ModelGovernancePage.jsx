import { useEffect, useState } from "react";
import { listGovernedModels, publishModel, retireModel, reviewModel } from "../../api/modeling";
import Icon from "../../components/ui/Icon";
import cn from "../../lib/cn";
import { Button, Card, ErrorNotice, PageFrame, RouteSkeleton, SectionHeader, StatusPill } from "../../components/ui";

// 后端治理状态机：review（pending/reviewing/rejected 可判），publish（仅 reviewing），retire（published/rejected）
const FILTERS = [
  { key: "", label: "全部模型" },
  { key: "pending", label: "待复核" },
  { key: "published", label: "已发布" },
  { key: "rejected", label: "已驳回" },
  { key: "retired", label: "已下线" },
];

export default function ModelGovernancePage() {
  const [models, setModels] = useState(null);
  const [error, setError] = useState(null);
  const [filter, setFilter] = useState("");
  const [selectedId, setSelectedId] = useState(null);
  const [acting, setActing] = useState(false);
  const [actionError, setActionError] = useState(null);
  const [reviewTarget, setReviewTarget] = useState(null);
  const [reviewDecision, setReviewDecision] = useState("pass");
  const [reviewComment, setReviewComment] = useState("");

  const load = () => {
    setError(null);
    setModels(null);
    listGovernedModels().then((items) => {
      setModels(items);
      setSelectedId((current) => current ?? items[0]?.id ?? null);
    }).catch(setError);
  };

  useEffect(load, []);

  const runAction = async (action) => {
    if (acting) return;
    setActing(true);
    setActionError(null);
    try {
      await action();
      await load();
    } catch (err) {
      setActionError(err.message || "操作失败，请重试。");
    } finally {
      setActing(false);
    }
  };

  const confirmReview = async () => {
    if (!reviewTarget) return;
    setActing(true);
    setActionError(null);
    try {
      await reviewModel(reviewTarget.id, { decision: reviewDecision, comment: reviewComment });
      setReviewTarget(null);
      await load();
    } catch (err) {
      setActionError(err.message || "提交复核失败，请重试。");
    } finally {
      setActing(false);
    }
  };

  if (error) {
    return <PageFrame><ErrorNotice title="模型目录加载失败" detail="请检查建模服务后重试。" onRetry={load} /></PageFrame>;
  }
  if (!models) return <RouteSkeleton />;

  const visibleModels = filter ? models.filter((model) => model.status === filter) : models;
  const selected = models.find((model) => model.id === selectedId) ?? models[0];
  const published = models.filter((model) => model.status === "published").length;
  const pending = models.filter((model) => model.status === "pending").length;
  const retired = models.filter((model) => model.status === "retired").length;

  const canReview = ["pending", "reviewing", "rejected"].includes(selected?.status ?? "");
  const canPublish = selected?.status === "reviewing";
  const canRetire = ["published", "rejected"].includes(selected?.status ?? "");

  return (
    <PageFrame>
      <section className="grid gap-3.5 sm:grid-cols-2 xl:grid-cols-4">
        <GovernanceStat icon="model" label="登记模型" value={models.length} caption="覆盖分类、回归、聚类与异常检测" />
        <GovernanceStat icon="check" label="已发布" value={published} caption="可被业务人员用于推理" tone="green" />
        <GovernanceStat icon="clock" label="待复核" value={pending} caption="需确认数据、指标与适用边界" tone="amber" />
        <GovernanceStat icon="shield" label="已下线" value={retired} caption="停止推理但保留审计记录" tone="gray" />
      </section>

      {actionError && (
        <Card className="flex items-start gap-2.5 border-red/25 bg-red-soft px-4 py-3 text-[11px] leading-relaxed text-red">
          <span className="mt-0.5"><Icon name="warning" size={14} /></span><span>{actionError}</span>
        </Card>
      )}

      <div className="grid items-start gap-3.5 xl:grid-cols-[minmax(0,1.55fr)_360px]">
        <Card className="overflow-hidden">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-5 py-4">
            <div>
              <span className="text-[11px] font-semibold tracking-wide text-faint">模型注册表</span>
              <h2 className="mt-0.5 text-base font-semibold tracking-tight text-ink">业务模型与版本</h2>
            </div>
            <div className="flex flex-wrap items-center gap-1 rounded-control border border-line bg-surface-2 p-0.5">
              {FILTERS.map((item) => (
                <button
                  type="button"
                  key={item.key || "all"}
                  onClick={() => setFilter(item.key)}
                  className={cn(
                    "h-7 cursor-pointer rounded-tag px-2.5 text-[11px] font-medium transition-colors",
                    filter === item.key ? "bg-surface text-ink shadow-card" : "text-muted hover:text-ink",
                  )}
                >
                  {item.label}
                </button>
              ))}
            </div>
          </div>
          <div className="overflow-x-auto">
            <div className="min-w-[840px]">
              <div className="grid grid-cols-[minmax(190px,1.4fr)_120px_130px_90px_100px] gap-3 border-b border-line bg-surface-2 px-5 py-2.5 text-[10px] font-semibold text-faint">
                <span>模型</span><span>算法</span><span>评估摘要</span><span>调用次数</span><span>状态</span>
              </div>
              {visibleModels.map((model) => (
                <button
                  type="button"
                  key={model.id}
                  onClick={() => setSelectedId(model.id)}
                  className={cn(
                    "grid w-full cursor-pointer grid-cols-[minmax(190px,1.4fr)_120px_130px_90px_100px] items-center gap-3 border-b border-line px-5 py-3 text-left transition-colors last:border-b-0 hover:bg-surface-2",
                    selected?.id === model.id && "bg-primary-soft/50",
                  )}
                >
                  <span className="min-w-0"><strong className="block truncate text-xs font-semibold text-ink">{model.name}</strong><small className="mt-0.5 block truncate font-mono text-[10px] text-faint">{model.id} · v{model.version}</small></span>
                  <span className="truncate text-xs text-ink-2">{model.algorithmName}</span>
                  <span className="truncate font-mono text-[11px] text-ink-2">{model.metric}</span>
                  <span className="font-mono text-[11px] text-ink-2">{model.calls} 次</span>
                  <StatusPill tone={model.tone}>{model.statusLabel}</StatusPill>
                </button>
              ))}
              {!visibleModels.length && <div className="grid min-h-36 place-content-center text-xs text-muted">当前筛选下没有模型</div>}
            </div>
          </div>
        </Card>

        <div className="grid gap-3.5">
          {!selected && <Card className="grid min-h-40 place-content-center p-6 text-center"><p className="text-xs leading-relaxed text-muted">暂无可管理的模型。<br />先在业务端“智能建模”中训练并完成评估，模型会登记到这里。</p></Card>}
          {selected && <Card>
            <SectionHeader eyebrow="当前模型" title={selected.name} />
            <div className="grid gap-3 px-5 pb-5">
              <div className="flex items-center justify-between"><span className="text-xs text-muted">版本</span><code className="font-mono text-xs text-ink">v{selected.version}</code></div>
              <GovernanceDetail label="算法" value={selected.algorithmName} />
              <GovernanceDetail label="责任人" value={selected.owner} />
              <GovernanceDetail label="来源任务" value={selected.taskId} mono />
              <GovernanceDetail label="训练数据" value={selected.dataset} />
              <GovernanceDetail label="评估摘要" value={selected.metric} mono />
              <GovernanceDetail label="调用次数" value={`${selected.calls} 次`} />
              <GovernanceDetail label="最近更新" value={selected.updatedAt} />
              <div className="border-t border-line pt-3">
                <p className="text-[11px] leading-relaxed text-muted">管理员负责发布、下线、权限与版本治理；业务目标、特征确认与训练操作仍在业务端“智能建模”完成。</p>
              </div>
              <div className="flex justify-end gap-2">
                {canPublish && <Button variant="primary" disabled={acting} onClick={() => runAction(() => publishModel(selected.id))}>发布模型</Button>}
                {canReview && <Button variant="primary" disabled={acting} onClick={() => { setReviewTarget(selected); setReviewDecision("pass"); setReviewComment(""); }}>开始复核</Button>}
                {canRetire && <Button variant="secondary" disabled={acting} onClick={() => runAction(() => retireModel(selected.id))}>下线模型</Button>}
                {!canReview && !canPublish && !canRetire && <Button variant="secondary" disabled>查看版本</Button>}
              </div>
            </div>
          </Card>}

          <Card>
            <SectionHeader eyebrow="治理策略" title="发布与运行边界" />
            <div className="grid gap-2 px-5 pb-5">
              <PolicyLine title="发布前人工复核" caption="核对数据范围、评估指标和适用边界" />
              <PolicyLine title="过期模型提醒" caption="连续 30 天无评估记录时标记待检查" />
              <PolicyLine title="隔离推理环境" caption="禁止模型访问网络和未授权数据源" />
              <Button variant="secondary" className="mt-1 justify-self-end" disabled>治理策略由系统托管</Button>
            </div>
          </Card>
        </div>
      </div>

      {reviewTarget && (
        <div className="fixed inset-0 z-50 grid place-items-center bg-ink/30 p-4" role="dialog" aria-modal="true" aria-label="模型复核">
          <Card className="w-full max-w-md">
            <div className="border-b border-line px-5 py-4">
              <span className="text-[10px] font-semibold tracking-wide text-faint">人工复核</span>
              <h3 className="mt-0.5 text-base font-semibold tracking-tight text-ink">{reviewTarget.name} · v{reviewTarget.version}</h3>
            </div>
            <div className="grid gap-3.5 px-5 py-4">
              <div className="grid grid-cols-2 gap-2">
                <button
                  type="button"
                  onClick={() => setReviewDecision("pass")}
                  className={cn("cursor-pointer rounded-control border px-3 py-2.5 text-left transition-colors", reviewDecision === "pass" ? "border-green/40 bg-green/10" : "border-line bg-surface-2")}
                >
                  <strong className="block text-xs font-semibold text-ink">通过</strong>
                  <small className="mt-0.5 block text-[10px] text-muted">审核通过后进入已发布，可被调用</small>
                </button>
                <button
                  type="button"
                  onClick={() => setReviewDecision("reject")}
                  className={cn("cursor-pointer rounded-control border px-3 py-2.5 text-left transition-colors", reviewDecision === "reject" ? "border-red/40 bg-red-soft" : "border-line bg-surface-2")}
                >
                  <strong className="block text-xs font-semibold text-ink">驳回</strong>
                  <small className="mt-0.5 block text-[10px] text-muted">驳回后模型进入已驳回，可重新复核</small>
                </button>
              </div>
              <label className="grid gap-1.5">
                <span className="text-[11px] font-semibold text-muted">复核意见（可选）</span>
                <textarea
                  value={reviewComment}
                  onChange={(event) => setReviewComment(event.target.value)}
                  rows={3}
                  placeholder="填写数据范围、指标或适用边界的核验意见…"
                  className="resize-none rounded-control border border-line bg-surface-2 px-3 py-2 text-xs text-ink outline-none placeholder:text-faint focus:border-line-strong"
                />
              </label>
              {actionError && <p className="text-[11px] leading-relaxed text-red">{actionError}</p>}
              <div className="flex justify-end gap-2">
                <Button variant="ghost" onClick={() => setReviewTarget(null)} disabled={acting}>取消</Button>
                <Button variant="primary" onClick={confirmReview} disabled={acting}>{acting ? "提交中…" : "提交复核"}</Button>
              </div>
            </div>
          </Card>
        </div>
      )}
    </PageFrame>
  );
}

function GovernanceStat({ icon, label, value, caption, tone = "blue" }) {
  const tones = { blue: "bg-primary-soft text-primary", green: "bg-green/10 text-green", amber: "bg-amber-soft text-amber", teal: "bg-teal-soft text-teal", gray: "bg-surface-3 text-muted" };
  return <Card className="flex min-h-24 items-start gap-3 p-4"><span className={cn("grid h-9 w-9 shrink-0 place-items-center rounded-control", tones[tone])}><Icon name={icon} size={17} /></span><span className="min-w-0"><span className="block text-[11px] text-muted">{label}</span><strong className="mt-0.5 block font-mono text-xl font-semibold text-ink">{value}</strong><small className="mt-1 block text-[10px] leading-relaxed text-faint">{caption}</small></span></Card>;
}

function GovernanceDetail({ label, value, mono = false }) {
  return <div className="flex items-start justify-between gap-4 border-b border-line pb-2.5 text-xs last:border-0"><span className="shrink-0 text-muted">{label}</span><span className={cn("text-right text-ink-2", mono && "font-mono text-[11px]")}>{value}</span></div>;
}

function PolicyLine({ title, caption }) {
  return <div className="flex items-start gap-2.5 rounded-control border border-line bg-surface-2 px-3 py-2.5"><span className="mt-0.5 text-green"><Icon name="check" size={14} /></span><span><strong className="block text-xs font-semibold text-ink">{title}</strong><small className="mt-0.5 block text-[10px] leading-relaxed text-faint">{caption}</small></span></div>;
}
