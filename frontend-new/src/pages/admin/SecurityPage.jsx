import { useCallback, useEffect, useState } from "react";
import Icon from "../../components/ui/Icon";
import { Button, Card, EmptyState, ErrorNotice, Modal, PageFrame, RouteSkeleton, SectionHeader, StatusPill } from "../../components/ui";
import { listAuditLogs, reviewAuditLog, reviewAuditLogsBatch } from "../../api/security";

const layers = [
  ["权限", "角色与数据范围"],
  ["语义", "表字段白名单"],
  ["代码", "SQL AST 校验"],
  ["执行", "只读沙箱与限流"],
  ["结果", "脱敏与数值核对"],
];

const resultOptions = [
  { value: "", label: "全部" },
  { value: "允许", label: "允许" },
  { value: "已拦截", label: "已拦截" },
  { value: "已记录", label: "已记录" },
];

export default function SecurityPage() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);
  const [busyId, setBusyId] = useState(null);
  const [batchBusy, setBatchBusy] = useState(false);
  const [confirmBatch, setConfirmBatch] = useState(false);
  const [detailRow, setDetailRow] = useState(null);
  const [filters, setFilters] = useState({ result: "", reviewed: "", user: "", keyword: "" });

  const load = useCallback(async () => {
    setError(null);
    try {
      const payload = await listAuditLogs({
        result: filters.result || undefined,
        reviewed: filters.reviewed || undefined,
        user: filters.user || undefined,
        keyword: filters.keyword.trim(),
      });
      setData(payload);
      // 详情弹窗打开时同步该行最新状态（如刚完成复核）
      setDetailRow((current) =>
        current ? (payload.items.find((item) => item.id === current.id) ?? current) : current,
      );
    } catch (err) {
      setError(err);
    }
  }, [filters]);

  useEffect(() => {
    setConfirmBatch(false);
  }, [filters]);

  useEffect(() => {
    const timer = window.setTimeout(load, filters.keyword ? 300 : 0);
    return () => window.clearTimeout(timer);
  }, [load, filters.keyword]);

  const items = data?.items ?? [];
  const stats = data?.stats ?? { total: 0, allowed: 0, blocked: 0, recorded: 0, unreviewed: 0 };
  const users = data?.users ?? [];
  const hasFilter = Boolean(filters.result || filters.reviewed || filters.user || filters.keyword);
  const pendingRows = items.filter((row) => row.result === "已拦截" && !row.reviewedAt);

  const review = async (row) => {
    setBusyId(row.id);
    setNotice(null);
    try {
      await reviewAuditLog(row.id);
      window.dispatchEvent(new CustomEvent("forgesight:governance-changed"));
      await load();
      setNotice({ tone: "success", text: `已复核 1 条拦截记录` });
    } catch (err) {
      setNotice({ tone: "error", text: `复核失败：${err?.message || "请稍后重试"}` });
    } finally {
      setBusyId(null);
    }
  };

  const reviewBatch = async () => {
    setBatchBusy(true);
    setNotice(null);
    try {
      const result = await reviewAuditLogsBatch(pendingRows.map((row) => row.id));
      window.dispatchEvent(new CustomEvent("forgesight:governance-changed"));
      await load();
      setNotice({
        tone: "success",
        text: result.reviewed > 0 ? `已批量复核 ${result.reviewed} 条拦截记录` : "所选记录均已复核",
      });
    } catch (err) {
      setNotice({ tone: "error", text: `批量复核失败：${err?.message || "请稍后重试"}` });
    } finally {
      setBatchBusy(false);
      setConfirmBatch(false);
    }
  };

  const exportCsv = () => {
    if (!items.length) return;
    const header = ["时间", "用户", "角色", "执行动作", "数据资源", "结果", "复核状态", "复核人", "详情"];
    const quote = (value) => `"${String(value ?? "").replaceAll('"', '""')}"`;
    const csv = [
      header,
      ...items.map((row) => [
        row.at ?? row.time,
        row.user,
        row.role,
        row.action,
        row.resource,
        row.result,
        row.result === "已拦截" ? (row.reviewedAt ? "已复核" : "待复核") : "—",
        row.reviewedBy,
        row.detail,
      ]),
    ]
      .map((line) => line.map(quote).join(","))
      .join("\n");
    const blob = new Blob([`${csv}`], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "ForgeSight-审计日志.csv";
    link.click();
    URL.revokeObjectURL(url);
  };

  if (error) {
    return (
      <PageFrame>
        <ErrorNotice title="审计记录加载失败" detail="请检查数据连接后重试。" onRetry={load} />
      </PageFrame>
    );
  }
  if (!data) return <RouteSkeleton />;

  return (
    <PageFrame className="max-w-[1360px] gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line pb-3">
        <div className="flex min-w-0 items-center gap-2.5">
          <span className="grid h-8 w-8 shrink-0 place-items-center rounded-control bg-teal-soft text-teal"><Icon name="shield" size={16} /></span>
          <div className="min-w-0">
            <h2 className="text-sm font-semibold tracking-tight text-ink">权限与审计</h2>
            <p className="mt-0.5 text-[11px] text-muted">
              共 {stats.total} 条审计记录 · {stats.unreviewed > 0 ? `${stats.unreviewed} 条拦截待复核` : "拦截记录均已复核"}
            </p>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-2 divide-y divide-line overflow-hidden rounded-panel border border-line bg-paper sm:grid-cols-4 sm:divide-y-0 sm:divide-x">
        <div className="px-4 py-3"><span className="block text-[11px] text-muted">审计记录</span><strong className="mt-1 block font-mono text-base font-semibold leading-none text-ink">{stats.total}</strong></div>
        <div className="px-4 py-3"><span className="block text-[11px] text-muted">允许执行</span><strong className="mt-1 block font-mono text-base font-semibold leading-none text-ink">{stats.allowed}</strong></div>
        <div className="px-4 py-3"><span className="block text-[11px] text-muted">危险操作拦截</span><strong className="mt-1 block font-mono text-base font-semibold leading-none text-red">{stats.blocked}</strong></div>
        <div className="px-4 py-3"><span className="block text-[11px] text-muted">拦截待复核</span><strong className="mt-1 block font-mono text-base font-semibold leading-none text-ink">{stats.unreviewed}</strong></div>
      </div>

      <Card>
        <SectionHeader eyebrow="已启用 5 项" title="安全控制策略" />
        <div className="grid grid-cols-5 gap-2 px-5 pb-5">
          {layers.map(([title, text], index) => (
            <div key={title} className="relative rounded-control border border-line bg-surface-2 px-2.5 py-3">
              <span className="grid h-5 w-5 place-items-center rounded-tag border border-line bg-surface font-mono text-[10px] font-semibold text-muted">{index + 1}</span>
              <strong className="mt-2 block text-xs font-semibold text-ink">{title}控制</strong>
              <small className="mt-0.5 block text-[10px] leading-snug text-faint">{text}</small>
              {index < layers.length - 1 && <span className="absolute -right-2 top-1/2 z-10 -translate-y-1/2 text-faint"><Icon name="chevron" size={14} /></span>}
            </div>
          ))}
        </div>
      </Card>

      <Card>
        <SectionHeader
          eyebrow={`显示 ${items.length} 条`}
          title="执行审计记录"
          action={items.length ? "导出审计日志" : null}
          onAction={items.length ? exportCsv : undefined}
        />
        <div className="flex flex-wrap items-center gap-2 px-5 pb-3">
          <div className="flex items-center gap-0.5 rounded-control border border-line bg-surface-2 p-0.5" role="group" aria-label="按结果筛选">
            {resultOptions.map((option) => (
              <button
                key={option.label}
                type="button"
                aria-pressed={filters.result === option.value}
                onClick={() => setFilters((current) => ({ ...current, result: option.value }))}
                className={
                  filters.result === option.value
                    ? "h-7 cursor-pointer rounded-tag bg-surface px-2.5 text-[11px] font-semibold text-ink shadow-card"
                    : "h-7 cursor-pointer rounded-tag px-2.5 text-[11px] text-muted transition-colors hover:text-ink"
                }
              >
                {option.label}
              </button>
            ))}
          </div>
          <button
            type="button"
            aria-pressed={filters.reviewed === "待复核"}
            onClick={() => setFilters((current) => ({ ...current, reviewed: current.reviewed === "待复核" ? "" : "待复核" }))}
            className={
              filters.reviewed === "待复核"
                ? "flex h-8 cursor-pointer items-center gap-1.5 rounded-control border border-amber bg-amber-soft px-2.5 text-[11px] font-semibold text-amber"
                : "flex h-8 cursor-pointer items-center gap-1.5 rounded-control border border-line bg-surface px-2.5 text-[11px] text-muted transition-colors hover:border-line-strong hover:text-ink"
            }
          >
            <Icon name="clock" size={13} />
            仅看待复核
          </button>
          {pendingRows.length > 0 && (
            <button
              type="button"
              disabled={batchBusy}
              onClick={() => (confirmBatch ? reviewBatch() : setConfirmBatch(true))}
              className={
                confirmBatch
                  ? "flex h-8 cursor-pointer items-center gap-1.5 rounded-control bg-amber px-2.5 text-[11px] font-semibold text-white transition-opacity hover:opacity-90 disabled:cursor-wait disabled:opacity-60"
                  : "flex h-8 cursor-pointer items-center gap-1.5 rounded-control border border-line bg-surface px-2.5 text-[11px] text-muted transition-colors hover:border-line-strong hover:text-ink disabled:cursor-wait"
              }
            >
              <Icon name="check" size={13} />
              {batchBusy ? "复核中…" : confirmBatch ? `确认复核这 ${pendingRows.length} 条` : `批量复核（${pendingRows.length}）`}
            </button>
          )}
          <select
            aria-label="按操作人筛选"
            value={filters.user}
            onChange={(event) => setFilters((current) => ({ ...current, user: event.target.value }))}
            className="h-8 rounded-control border border-line bg-surface px-2 text-[11px] text-ink"
          >
            <option value="">全部用户</option>
            {users.map((name) => (
              <option key={name} value={name}>{name}</option>
            ))}
          </select>
          <label className="relative min-w-[180px] flex-1">
            <span className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-faint"><Icon name="search" size={13} /></span>
            <input
              type="search"
              aria-label="搜索审计记录"
              value={filters.keyword}
              onChange={(event) => setFilters((current) => ({ ...current, keyword: event.target.value }))}
              placeholder="搜索动作、资源或用户"
              className="h-8 w-full rounded-control border border-line bg-surface pl-8 pr-2.5 text-[11px] text-ink placeholder:text-faint"
            />
          </label>
          {hasFilter && (
            <button
              type="button"
              onClick={() => setFilters({ result: "", reviewed: "", user: "", keyword: "" })}
              className="h-8 cursor-pointer rounded-control px-2 text-[11px] text-muted transition-colors hover:bg-surface-2 hover:text-ink"
            >
              清除筛选
            </button>
          )}
        </div>
        {notice && (
          <p
            role="status"
            className={
              notice.tone === "success"
                ? "mx-5 mb-3 rounded-control border border-green bg-green/10 px-3 py-2 text-[11px] text-green"
                : "mx-5 mb-3 rounded-control border border-red bg-red-soft px-3 py-2 text-[11px] text-red"
            }
          >
            {notice.text}
          </p>
        )}
        {items.length ? (
          <div className="px-5 pb-5">
            <div className="grid grid-cols-[84px_0.8fr_minmax(0,1.4fr)_1fr_0.7fr_140px_52px] gap-3 py-2.5 text-[11px] font-semibold text-muted">
              <span>时间</span><span>用户</span><span>执行动作</span><span>数据资源</span><span>结果</span><span>复核</span><span>详情</span>
            </div>
            {items.map((row, index) => (
              <div key={row.id ?? `${row.time}-${index}`} className="grid grid-cols-[84px_0.8fr_minmax(0,1.4fr)_1fr_0.7fr_140px_52px] items-center gap-3 border-t border-line py-3 text-xs text-ink-2">
                <span><code className="font-mono text-[11px] text-muted">{row.time}</code></span>
                <span className="min-w-0">
                  <b className="block truncate font-semibold text-ink">{row.user}</b>
                  <small className="text-[10px] text-faint">{row.role}</small>
                </span>
                <span className="min-w-0 truncate" title={row.action}>{row.action}</span>
                <span className="min-w-0"><code className="block truncate font-mono text-[11px]">{row.resource}</code></span>
                <span><StatusPill tone={row.tone}>{row.result}</StatusPill></span>
                <span className="min-w-0">
                  {row.result === "已拦截" && (row.reviewedAt ? (
                    <small className="block truncate text-[10px] text-faint" title={`复核人 ${row.reviewedBy}`}>已复核 · {row.reviewedBy} {row.reviewedAt}</small>
                  ) : (
                    <button
                      type="button"
                      disabled={busyId === row.id}
                      onClick={() => review(row)}
                      className="h-7 cursor-pointer rounded-tag border border-amber bg-amber-soft px-2 text-[11px] font-semibold text-amber transition-opacity hover:opacity-85 disabled:cursor-wait disabled:opacity-50"
                    >
                      {busyId === row.id ? "复核中…" : "标记已复核"}
                    </button>
                  ))}
                </span>
                <span>
                  <button
                    type="button"
                    onClick={() => setDetailRow(row)}
                    className="cursor-pointer text-[11px] text-muted underline-offset-2 transition-colors hover:text-ink hover:underline"
                  >
                    详情
                  </button>
                </span>
              </div>
            ))}
          </div>
        ) : (
          <div className="px-5 pb-5">
            <EmptyState
              title={hasFilter ? "没有符合筛选条件的审计记录" : "暂无审计记录"}
              description={hasFilter ? "调整结果、用户或关键词后重试。" : "Agent 执行查询后，审计记录会显示在这里。"}
            />
          </div>
        )}
      </Card>

      <Modal open={Boolean(detailRow)} onClose={() => setDetailRow(null)} label="审计记录详情" maxWidth={640}>
        {detailRow && (
          <div className="p-5">
            <div className="flex items-start justify-between gap-3 border-b border-line pb-3">
              <div className="min-w-0">
                <span className="font-mono text-[10px] font-semibold tracking-wide text-faint">审计记录 #{detailRow.id}</span>
                <h3 className="mt-1 break-words text-base font-semibold tracking-tight text-ink">{detailRow.action}</h3>
              </div>
              <StatusPill tone={detailRow.tone}>{detailRow.result}</StatusPill>
            </div>
            <dl className="mt-3 grid grid-cols-[88px_minmax(0,1fr)] gap-y-2 text-xs">
              <dt className="text-muted">发生时间</dt>
              <dd><code className="font-mono text-[11px] text-ink-2">{detailRow.at || detailRow.time}</code></dd>
              <dt className="text-muted">操作人</dt>
              <dd className="text-ink-2">{detailRow.user} · {detailRow.role}</dd>
              <dt className="text-muted">数据资源</dt>
              <dd><code className="font-mono text-[11px] text-ink-2">{detailRow.resource || "—"}</code></dd>
              <dt className="text-muted">复核状态</dt>
              <dd className="text-ink-2">
                {detailRow.result === "已拦截"
                  ? detailRow.reviewedAt
                    ? `已复核 · ${detailRow.reviewedBy} ${detailRow.reviewedAt}`
                    : "待复核"
                  : "无需复核"}
              </dd>
            </dl>
            <div className="mt-3">
              <span className="text-[11px] text-muted">详情 / 拦截原因</span>
              <pre className="mt-1 max-h-56 overflow-auto whitespace-pre-wrap break-all rounded-control border border-line bg-surface-2 p-3 font-mono text-[11px] leading-relaxed text-ink-2">
                {detailRow.detail || "无附加详情"}
              </pre>
            </div>
            {detailRow.result === "已拦截" && !detailRow.reviewedAt && (
              <div className="mt-4 flex justify-end border-t border-line pt-3">
                <Button variant="primary" disabled={busyId === detailRow.id} onClick={() => review(detailRow)}>
                  {busyId === detailRow.id ? "复核中…" : "标记已复核"}
                </Button>
              </div>
            )}
          </div>
        )}
      </Modal>
    </PageFrame>
  );
}
