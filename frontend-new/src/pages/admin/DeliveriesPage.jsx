import { useEffect, useMemo, useState } from "react";

import { listDeliveries, retryDelivery } from "../../api/messages";
import {
  Button,
  Card,
  EmptyState,
  ErrorNotice,
  PageFrame,
  RouteSkeleton,
  StatusPill,
} from "../../components/ui";
import Icon from "../../components/ui/Icon";

const LABELS = {
  queued: ["排队中", "blue"],
  sending: ["发送中", "blue"],
  sent: ["已送达", "green"],
  retry_wait: ["等待重试", "amber"],
  waiting_attachment: ["等待 PDF", "amber"],
  failed: ["失败", "red"],
  blocked_configuration: ["配置受阻", "red"],
  unknown: ["结果未知", "amber"],
  suppressed: ["已跳过", "gray"],
};
const RETRYABLE = new Set([
  "failed",
  "retry_wait",
  "waiting_attachment",
  "unknown",
]);

function time(value) {
  return value
    ? new Intl.DateTimeFormat("zh-CN", {
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      }).format(new Date(value))
    : "—";
}

export default function DeliveriesPage() {
  const [items, setItems] = useState(null);
  const [status, setStatus] = useState("");
  const [recipient, setRecipient] = useState("");
  const [contentType, setContentType] = useState("");
  const [relationId, setRelationId] = useState("");
  const [createdFrom, setCreatedFrom] = useState("");
  const [createdTo, setCreatedTo] = useState("");
  const [limit, setLimit] = useState(100);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState("");
  const load = async (requestedLimit = limit) => {
    setError(null);
    try {
      setItems(
        await listDeliveries({
          status,
          recipientUserId: recipient.trim(),
          reportId: contentType === "report" ? relationId.trim() : undefined,
          announcementId:
            contentType === "announcement" ? relationId.trim() : undefined,
          contentType: contentType || undefined,
          createdFrom: createdFrom ? `${createdFrom}T00:00:00` : undefined,
          createdTo: createdTo ? `${createdTo}T23:59:59` : undefined,
          limit: requestedLimit,
        }),
      );
      setLimit(requestedLimit);
    } catch (nextError) {
      setError(nextError);
    }
  };
  useEffect(() => {
    load();
  }, [status]); // eslint-disable-line react-hooks/exhaustive-deps
  const counts = useMemo(
    () => ({
      all: items?.length ?? 0,
      failed:
        items?.filter((item) =>
          ["failed", "blocked_configuration", "unknown"].includes(item.status),
        ).length ?? 0,
      waiting:
        items?.filter((item) =>
          ["queued", "retry_wait", "waiting_attachment"].includes(item.status),
        ).length ?? 0,
    }),
    [items],
  );
  const retry = async (item) => {
    const confirmUnknown =
      item.status === "unknown"
        ? window.confirm("投递结果未知，重发可能造成重复邮件。确认继续吗？")
        : false;
    if (item.status === "unknown" && !confirmUnknown) return;
    setBusy(item.id);
    setNotice("");
    try {
      await retryDelivery(item.id, confirmUnknown);
      await load();
      setNotice("已复用原投递记录重新排队，不会创建重复业务任务。");
    } catch (nextError) {
      setNotice(nextError.message);
    } finally {
      setBusy("");
    }
  };
  if (error)
    return (
      <PageFrame>
        <ErrorNotice
          title="投递记录加载失败"
          detail={error.message}
          onRetry={load}
        />
      </PageFrame>
    );
  if (!items) return <RouteSkeleton />;
  return (
    <PageFrame className="gap-4">
      <header>
        <span className="section-kicker">DELIVERY OPERATIONS</span>
        <h2 className="mt-1 text-[25px] font-semibold">邮件投递</h2>
        <p className="mt-2 text-base text-muted">
          只展示掩码地址和安全错误分类，帮助管理员定位失败任务。
        </p>
      </header>
      <div className="grid grid-cols-3 divide-x divide-line rounded-section border border-line bg-paper py-4">
        <Metric label="当前记录" value={counts.all} />
        <Metric label="待处理" value={counts.waiting} />
        <Metric label="异常" value={counts.failed} danger={counts.failed > 0} />
      </div>
      {notice && (
        <p
          className="rounded-control border border-line bg-surface p-3 text-sm"
          role="status"
        >
          {notice}
        </p>
      )}
      <Card className="overflow-hidden">
        <div className="flex flex-wrap items-center gap-2 border-b border-line p-3">
          <select
            className="input-control h-9 w-auto"
            value={status}
            onChange={(event) => setStatus(event.target.value)}
          >
            <option value="">全部状态</option>
            {Object.entries(LABELS).map(([key, [label]]) => (
              <option key={key} value={key}>
                {label}
              </option>
            ))}
          </select>
          <select
            className="input-control h-9 w-auto"
            value={contentType}
            onChange={(event) => {
              setContentType(event.target.value);
              setRelationId("");
            }}
          >
            <option value="">全部内容</option>
            <option value="report">周报</option>
            <option value="announcement">管理员公告</option>
          </select>
          {contentType && (
            <input
              className="input-control h-9 min-w-52"
              value={relationId}
              onChange={(event) => setRelationId(event.target.value)}
              placeholder={contentType === "report" ? "周报 ID" : "公告 ID"}
              aria-label={contentType === "report" ? "周报 ID" : "公告 ID"}
            />
          )}
          <label className="relative min-w-56 flex-1 sm:max-w-xs">
            <Icon
              name="search"
              size={14}
              className="absolute left-3 top-1/2 -translate-y-1/2 text-muted"
            />
            <input
              className="input-control h-9 pl-8"
              value={recipient}
              onChange={(event) => setRecipient(event.target.value)}
              onKeyDown={(event) => event.key === "Enter" && load()}
              placeholder="接收人用户 ID"
            />
          </label>
          <label>
            <span className="sr-only">创建开始日期</span>
            <input
              type="date"
              className="input-control h-9 w-auto"
              value={createdFrom}
              onChange={(event) => setCreatedFrom(event.target.value)}
            />
          </label>
          <label>
            <span className="sr-only">创建结束日期</span>
            <input
              type="date"
              className="input-control h-9 w-auto"
              value={createdTo}
              onChange={(event) => setCreatedTo(event.target.value)}
            />
          </label>
          <Button onClick={() => load(100)}>查询记录</Button>
        </div>
        {items.length ? (
          <div className="overflow-x-auto">
            <table className="data-table min-w-[980px]">
              <thead>
                <tr>
                  <th>状态</th>
                  <th>接收邮箱</th>
                  <th>关联内容</th>
                  <th className="text-right">尝试次数</th>
                  <th>失败分类</th>
                  <th>下次重试</th>
                  <th>创建时间</th>
                  <th className="text-right">操作</th>
                </tr>
              </thead>
              <tbody>
                {items.map((item) => {
                  const [label, tone] = LABELS[item.status] ?? [
                    item.status,
                    "gray",
                  ];
                  return (
                    <tr key={item.id}>
                      <td>
                        <StatusPill tone={tone}>{label}</StatusPill>
                      </td>
                      <td className="font-mono">
                        {item.recipientAddress || "未验证邮箱"}
                      </td>
                      <td>
                        <span className="block text-ink">
                          {item.reportId ? "周报" : "公告"}
                        </span>
                        <span className="text-[12px] text-muted">
                          {item.reportId || item.messageId || "—"}
                        </span>
                      </td>
                      <td className="text-right font-mono">
                        {item.attemptCount}
                      </td>
                      <td>{item.lastErrorCategory || "—"}</td>
                      <td>{time(item.nextRetryAt)}</td>
                      <td>{time(item.createdAt)}</td>
                      <td className="text-right">
                        {RETRYABLE.has(item.status) && (
                          <Button
                            size="sm"
                            disabled={busy === item.id}
                            onClick={() => retry(item)}
                          >
                            {busy === item.id ? "正在排队…" : "重新投递"}
                          </Button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {items.length >= limit && limit < 500 && (
              <div className="border-t border-line p-3 text-center">
                <Button onClick={() => load(Math.min(limit + 100, 500))}>
                  加载更多投递记录
                </Button>
              </div>
            )}
          </div>
        ) : (
          <EmptyState
            title="当前筛选没有投递记录"
            description="调整状态或接收人条件后重新查询。"
          />
        )}
      </Card>
    </PageFrame>
  );
}
function Metric({ label, value, danger }) {
  return (
    <div className="px-4 text-center">
      <strong
        className={`font-mono text-[25px] ${danger ? "text-critical" : "text-ink"}`}
      >
        {value}
      </strong>
      <span className="mt-1 block text-[13px] text-muted">{label}</span>
    </div>
  );
}
