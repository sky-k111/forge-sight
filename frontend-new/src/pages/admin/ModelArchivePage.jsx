import { useEffect, useMemo, useState } from "react";
import Icon from "../../components/ui/Icon";
import cn from "../../lib/cn";
import {
  Button,
  ErrorNotice,
  PageFrame,
  RouteSkeleton,
  StatusPill,
} from "../../components/ui";
import {
  listGovernedModels,
  publishModel,
  publishModelDirect,
  purgeModelArtifacts,
  retireModel,
  rollbackModel,
  submitModelReview,
} from "../../api/modeling";

const statusFilters = [
  { value: "", label: "全部" },
  { value: "published", label: "已发布" },
  { value: "personal", label: "个人自用" },
  { value: "pending", label: "待复核" },
  { value: "retired", label: "已下线" },
];

export default function ModelArchivePage() {
  const [models, setModels] = useState(null);
  const [error, setError] = useState(null);
  const [filter, setFilter] = useState("");
  const [busyId, setBusyId] = useState("");
  const [notice, setNotice] = useState(null);
  const [confirmPurge, setConfirmPurge] = useState(false);

  const load = () => {
    setError(null);
    listGovernedModels().then((data) => setModels(data.items ?? [])).catch(setError);
  };
  useEffect(load, []);

  const act = async (key, fn, okText) => {
    setBusyId(key);
    setNotice(null);
    try {
      await fn();
      setNotice({ tone: "success", text: okText });
      load();
    } catch (err) {
      setNotice({ tone: "error", text: err?.message || "操作失败，请稍后重试" });
    } finally {
      setBusyId("");
    }
  };

  const stats = useMemo(() => {
    const items = models ?? [];
    const count = (status) => items.filter((m) => m.status === status).length;
    return {
      total: items.length,
      published: count("published"),
      personal: count("personal"),
      pending: count("pending") + count("reviewing"),
      retired: count("retired"),
      calls: items.reduce((sum, m) => sum + (m.calls ?? 0), 0),
    };
  }, [models]);

  const visible = useMemo(
    () => (models ?? []).filter((m) => !filter || m.status === filter || (filter === "pending" && m.status === "reviewing")),
    [models, filter],
  );

  if (error) {
    return (
      <PageFrame>
        <ErrorNotice title="模型档案加载失败" detail={error.message} onRetry={load} />
      </PageFrame>
    );
  }
  if (!models) return <RouteSkeleton />;

  return (
    <PageFrame className="max-w-[1360px] gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line pb-3">
        <div className="flex min-w-0 items-center gap-2.5">
          <span className="grid h-8 w-8 shrink-0 place-items-center rounded-control bg-violet/10 text-violet"><Icon name="history" size={16} /></span>
          <div className="min-w-0">
            <h2 className="text-sm font-semibold tracking-tight text-ink">模型档案</h2>
            <p className="mt-0.5 text-[11px] text-muted">注册表全量视图：版本、调用、直发标记与工件保留状态</p>
          </div>
        </div>
        <Button
          size="sm"
          variant="ghost"
          disabled={Boolean(busyId)}
          onClick={() => (confirmPurge
            ? act("purge", async () => {
                const result = await purgeModelArtifacts();
                setConfirmPurge(false);
                return result;
              }, "过期工件已清理，元数据与指纹保留")
            : setConfirmPurge(true))}
        >
          <Icon name="trash" size={13} />
          {confirmPurge ? "确认清理过期工件" : "清理过期工件"}
        </Button>
      </div>

      <div className="grid grid-cols-2 divide-y divide-line overflow-hidden rounded-panel border border-line bg-paper sm:grid-cols-5 sm:divide-y-0 sm:divide-x">
        <div className="px-4 py-3"><span className="block text-[11px] text-muted">模型总数</span><strong className="mt-1 block font-mono text-base font-semibold leading-none text-ink">{stats.total}</strong></div>
        <div className="px-4 py-3"><span className="block text-[11px] text-muted">已发布</span><strong className="mt-1 block font-mono text-base font-semibold leading-none text-teal">{stats.published}</strong></div>
        <div className="px-4 py-3"><span className="block text-[11px] text-muted">个人自用</span><strong className="mt-1 block font-mono text-base font-semibold leading-none text-amber">{stats.personal}</strong></div>
        <div className="px-4 py-3"><span className="block text-[11px] text-muted">待复核</span><strong className="mt-1 block font-mono text-base font-semibold leading-none text-ink">{stats.pending}</strong></div>
        <div className="px-4 py-3"><span className="block text-[11px] text-muted">累计推理调用</span><strong className="mt-1 block font-mono text-base font-semibold leading-none text-ink">{stats.calls}</strong></div>
      </div>

      {notice && (
        <p
          role="status"
          className={cn(
            "rounded-control border px-3 py-2 text-[11px]",
            notice.tone === "success" ? "border-green bg-green/10 text-green" : "border-red bg-red-soft text-red",
          )}
        >
          {notice.text}
        </p>
      )}

      <section className="overflow-hidden rounded-panel border border-line bg-surface">
        <div className="flex flex-wrap items-center gap-2 border-b border-line px-3 py-2">
          <div className="flex items-center gap-0.5 rounded-control bg-surface-2 p-0.5">
            {statusFilters.map((option) => (
              <button
                key={option.label}
                type="button"
                aria-pressed={filter === option.value}
                onClick={() => setFilter(option.value)}
                className={cn(
                  "h-7 cursor-pointer rounded-tag px-2.5 text-xs transition-colors",
                  filter === option.value ? "bg-surface font-semibold text-ink shadow-card" : "text-muted hover:text-ink",
                )}
              >
                {option.label}
              </button>
            ))}
          </div>
          <span className="ml-auto font-mono text-[11px] text-faint">{visible.length} / {models.length}</span>
        </div>
        {visible.length ? (
          <div>
            <div className="grid grid-cols-[minmax(0,1.6fr)_0.8fr_0.9fr_0.7fr_1fr_auto] gap-3 px-4 py-2.5 text-[11px] font-semibold text-muted">
              <span>模型</span><span>算法</span><span>所有者</span><span>效果</span><span>状态与时间</span><span>操作</span>
            </div>
            {visible.map((model) => (
              <div key={model.id} className="grid grid-cols-[minmax(0,1.6fr)_0.8fr_0.9fr_0.7fr_1fr_auto] items-center gap-3 border-t border-line px-4 py-2.5 text-xs text-ink-2">
                <span className="min-w-0">
                  <strong className="block truncate font-semibold text-ink">{model.name} <span className="font-mono text-[10px] text-faint">v{model.version}</span></strong>
                  <small className="mt-0.5 block truncate text-[11px] text-faint">{model.dataset}</small>
                </span>
                <span className="truncate">{model.algorithmName}</span>
                <span className="truncate">{model.owner}</span>
                <span className="truncate font-mono text-[11px]">{model.metric || "—"}</span>
                <span className="min-w-0">
                  <span className="flex flex-wrap items-center gap-1.5">
                    <StatusPill tone={model.tone}>{model.statusLabel}</StatusPill>
                    {model.directPublish && <StatusPill tone="violet">管理员直发</StatusPill>}
                    {model.status === "personal" && model.personalActive && <StatusPill tone="amber">仅本人</StatusPill>}
                  </span>
                  <small className="mt-0.5 block truncate text-[11px] text-faint">
                    {model.status === "personal"
                      ? `有效期至 ${model.personalExpiresAt ?? "—"}`
                      : model.publishedAt
                        ? `发布于 ${model.publishedAt}`
                        : model.retiredAt
                          ? `下线于 ${model.retiredAt}`
                          : `更新于 ${model.updatedAt ?? "—"}`}
                    {" · "}{model.calls ?? 0} 次调用
                  </small>
                </span>
                <span className="flex shrink-0 flex-wrap justify-end gap-1.5">
                  {model.status === "personal" && (
                    <>
                      <Button size="sm" variant="ghost" disabled={Boolean(busyId)} onClick={() => act(`${model.id}-submit`, () => submitModelReview(model.id), "已提交复核队列")}>提交复核</Button>
                      <Button size="sm" disabled={Boolean(busyId)} onClick={() => act(`${model.id}-direct`, () => publishModelDirect(model.id), "已免审直发为生产版本")}>直发</Button>
                    </>
                  )}
                  {model.status === "reviewing" && (
                    <Button size="sm" disabled={Boolean(busyId)} onClick={() => act(`${model.id}-publish`, () => publishModel(model.id), "模型已发布")}>发布</Button>
                  )}
                  {model.status === "published" && (
                    <Button size="sm" variant="ghost" disabled={Boolean(busyId)} onClick={() => act(`${model.id}-retire`, () => retireModel(model.id), "模型已下线")}>下线</Button>
                  )}
                  {model.status === "retired" && (
                    <Button size="sm" variant="ghost" disabled={Boolean(busyId)} onClick={() => act(`${model.id}-rollback`, () => rollbackModel(model.id), "已回滚为生产版本")}>回滚</Button>
                  )}
                </span>
              </div>
            ))}
          </div>
        ) : (
          <div className="grid place-content-center gap-1 px-6 py-10 text-center">
            <p className="text-sm font-semibold text-ink">还没有模型档案</p>
            <p className="text-[11px] text-muted">训练完成后，模型会登记在这里并进入治理生命周期。</p>
          </div>
        )}
      </section>
    </PageFrame>
  );
}
