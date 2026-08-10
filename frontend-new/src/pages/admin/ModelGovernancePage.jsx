import { useEffect, useState } from "react";
import { listGovernedModels } from "../../api/modeling";
import Icon from "../../components/ui/Icon";
import cn from "../../lib/cn";
import { Button, Card, ErrorNotice, PageFrame, RouteSkeleton, SectionHeader, StatusPill } from "../../components/ui";

const filters = ["全部模型", "待复核", "已发布", "已下线"];

export default function ModelGovernancePage() {
  const [models, setModels] = useState(null);
  const [error, setError] = useState(null);
  const [filter, setFilter] = useState("全部模型");
  const [selectedId, setSelectedId] = useState(null);
  const [policySaved, setPolicySaved] = useState(false);

  const load = () => {
    setError(null);
    setModels(null);
    listGovernedModels().then((items) => {
      setModels(items);
      setSelectedId((current) => current ?? items[0]?.id ?? null);
    }).catch(setError);
  };

  useEffect(load, []);

  if (error) {
    return <PageFrame><ErrorNotice title="模型目录加载失败" detail="请检查建模服务后重试。" onRetry={load} /></PageFrame>;
  }
  if (!models) return <RouteSkeleton />;

  const visibleModels = filter === "全部模型" ? models : models.filter((model) => model.status === filter);
  const selected = models.find((model) => model.id === selectedId) ?? models[0];
  const published = models.filter((model) => model.status === "已发布").length;
  const pending = models.filter((model) => model.status === "待复核").length;

  return (
    <PageFrame>
      <section className="grid gap-3.5 sm:grid-cols-2 xl:grid-cols-4">
        <GovernanceStat icon="model" label="登记模型" value={models.length} caption="覆盖分类、回归、聚类与异常检测" />
        <GovernanceStat icon="check" label="已发布" value={published} caption="可被业务人员用于推理" tone="green" />
        <GovernanceStat icon="clock" label="待复核" value={pending} caption="需确认数据、指标与适用边界" tone="amber" />
        <GovernanceStat icon="shield" label="运行策略" value="6/6" caption="隔离、配额与审计策略已启用" tone="teal" />
      </section>

      <div className="grid items-start gap-3.5 xl:grid-cols-[minmax(0,1.55fr)_360px]">
        <Card className="overflow-hidden">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-5 py-4">
            <div>
              <span className="text-[11px] font-semibold tracking-wide text-faint">模型注册表</span>
              <h2 className="mt-0.5 text-base font-semibold tracking-tight text-ink">业务模型与版本</h2>
            </div>
            <div className="flex flex-wrap items-center gap-1 rounded-control border border-line bg-surface-2 p-0.5">
              {filters.map((item) => (
                <button
                  type="button"
                  key={item}
                  onClick={() => setFilter(item)}
                  className={cn(
                    "h-7 cursor-pointer rounded-tag px-2.5 text-[11px] font-medium transition-colors",
                    filter === item ? "bg-surface text-ink shadow-card" : "text-muted hover:text-ink",
                  )}
                >
                  {item}
                </button>
              ))}
            </div>
          </div>
          <div className="overflow-x-auto">
            <div className="min-w-[840px]">
              <div className="grid grid-cols-[minmax(190px,1.4fr)_110px_120px_130px_90px] gap-3 border-b border-line bg-surface-2 px-5 py-2.5 text-[10px] font-semibold text-faint">
                <span>模型</span><span>创建方式</span><span>算法</span><span>评估摘要</span><span>状态</span>
              </div>
              {visibleModels.map((model) => (
                <button
                  type="button"
                  key={model.id}
                  onClick={() => setSelectedId(model.id)}
                  className={cn(
                    "grid w-full cursor-pointer grid-cols-[minmax(190px,1.4fr)_110px_120px_130px_90px] items-center gap-3 border-b border-line px-5 py-3 text-left transition-colors last:border-b-0 hover:bg-surface-2",
                    selected?.id === model.id && "bg-primary-soft/50",
                  )}
                >
                  <span className="min-w-0"><strong className="block truncate text-xs font-semibold text-ink">{model.name}</strong><small className="mt-0.5 block truncate font-mono text-[10px] text-faint">{model.id} · {model.version}</small></span>
                  <span className="text-xs text-ink-2">{model.source}</span>
                  <span className="truncate text-xs text-ink-2">{model.algorithm}</span>
                  <span className="font-mono text-[11px] text-ink-2">{model.metric}</span>
                  <StatusPill tone={model.tone}>{model.status}</StatusPill>
                </button>
              ))}
              {!visibleModels.length && <div className="grid min-h-36 place-content-center text-xs text-muted">当前筛选下没有模型</div>}
            </div>
          </div>
        </Card>

        <div className="grid gap-3.5">
          <Card>
            <SectionHeader eyebrow="当前模型" title={selected.name} />
            <div className="grid gap-3 px-5 pb-5">
              <div className="flex items-center justify-between"><span className="text-xs text-muted">版本</span><code className="font-mono text-xs text-ink">{selected.version}</code></div>
              <GovernanceDetail label="责任人" value={selected.owner} />
              <GovernanceDetail label="来源任务" value={selected.taskId} mono />
              <GovernanceDetail label="训练数据" value={selected.dataset} />
              <GovernanceDetail label="调用次数" value={`${selected.calls} 次`} />
              <GovernanceDetail label="最近更新" value={selected.updatedAt} />
              <div className="border-t border-line pt-3">
                <p className="text-[11px] leading-relaxed text-muted">管理员只负责发布、下线、权限和版本治理；业务目标、特征确认与训练操作仍在业务端“智能建模”完成。</p>
              </div>
              <div className="flex justify-end gap-2">
                <Button variant="secondary">查看审计</Button>
                <Button variant="primary">{selected.status === "待复核" ? "开始复核" : "查看版本"}</Button>
              </div>
            </div>
          </Card>

          <Card>
            <SectionHeader eyebrow="治理策略" title="发布与运行边界" />
            <div className="grid gap-2 px-5 pb-5">
              <PolicyLine title="发布前人工复核" caption="核对数据范围、评估指标和适用边界" />
              <PolicyLine title="过期模型提醒" caption="连续 30 天无评估记录时标记待检查" />
              <PolicyLine title="隔离推理环境" caption="禁止模型访问网络和未授权数据源" />
              <Button variant="primary" className="mt-1 justify-self-end" onClick={() => setPolicySaved(true)}>{policySaved ? "策略已保存" : "保存治理策略"}</Button>
            </div>
          </Card>
        </div>
      </div>
    </PageFrame>
  );
}

function GovernanceStat({ icon, label, value, caption, tone = "blue" }) {
  const tones = { blue: "bg-primary-soft text-primary", green: "bg-green/10 text-green", amber: "bg-amber-soft text-amber", teal: "bg-teal-soft text-teal" };
  return <Card className="flex min-h-24 items-start gap-3 p-4"><span className={cn("grid h-9 w-9 shrink-0 place-items-center rounded-control", tones[tone])}><Icon name={icon} size={17} /></span><span className="min-w-0"><span className="block text-[11px] text-muted">{label}</span><strong className="mt-0.5 block font-mono text-xl font-semibold text-ink">{value}</strong><small className="mt-1 block text-[10px] leading-relaxed text-faint">{caption}</small></span></Card>;
}

function GovernanceDetail({ label, value, mono = false }) {
  return <div className="flex items-start justify-between gap-4 border-b border-line pb-2.5 text-xs last:border-0"><span className="shrink-0 text-muted">{label}</span><span className={cn("text-right text-ink-2", mono && "font-mono text-[11px]")}>{value}</span></div>;
}

function PolicyLine({ title, caption }) {
  return <div className="flex items-start gap-2.5 rounded-control border border-line bg-surface-2 px-3 py-2.5"><span className="mt-0.5 text-green"><Icon name="check" size={14} /></span><span><strong className="block text-xs font-semibold text-ink">{title}</strong><small className="mt-0.5 block text-[10px] leading-relaxed text-faint">{caption}</small></span></div>;
}
