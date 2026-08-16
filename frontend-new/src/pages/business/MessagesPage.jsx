import { useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";

import { getMessage, listMessages } from "../../api/messages";
import { getReport, listReports } from "../../api/reports";
import { WeeklyReportCharts } from "../../components/WeeklyReportCharts";
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
import cn from "../../lib/cn";
import { buildReportView, reportText } from "../../lib/weeklyReportView";

const TYPE_LABEL = {
  weekly_report: "Agent 周报",
  admin_announcement: "管理员公告",
};
const PRIORITY = {
  normal: ["一般", "gray"],
  high: ["重要", "amber"],
  urgent: ["紧急", "red"],
};

function formatTime(value) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

const REPORT_TERMINAL = new Set(["published", "partial", "failed", "validation_failed"]);

export function mergeInboxWithGeneratingReports(messages = [], reports = [], type = "") {
  if (type && type !== "weekly_report") return messages;
  const generating = reports
    .filter((report) => !REPORT_TERMINAL.has(report.status))
    .map((report) => ({
      id: `generating-report-${report.id}`,
      reportId: report.id,
      type: "weekly_report",
      priority: "normal",
      sourceLabel: "Agent 周报",
      title: report.title || "本期周报",
      summaryLines: ["正在生成中，暂不可打开；完成后将自动投递到绑定邮箱。"],
      publishedAt: report.createdAt || report.updatedAt,
      isRead: true,
      isGenerating: true,
    }));
  return [...generating, ...messages].sort((a, b) => new Date(b.publishedAt || 0) - new Date(a.publishedAt || 0));
}

export default function MessagesPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const { messageId } = useParams();
  const [items, setItems] = useState(null);
  const [selected, setSelected] = useState(null);
  const [nextCursor, setNextCursor] = useState(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [type, setType] = useState("");
  const [read, setRead] = useState("");
  const [priority, setPriority] = useState("");
  const [publishedFrom, setPublishedFrom] = useState("");
  const [publishedTo, setPublishedTo] = useState("");
  const [keyword, setKeyword] = useState("");
  const [error, setError] = useState(null);
  const [detailError, setDetailError] = useState(null);

  const load = async ({ append = false } = {}) => {
    setError(null);
    if (append) setLoadingMore(true);
    try {
      const [result, reports] = await Promise.all([listMessages({
        type,
        read: read === "" ? undefined : read === "read",
        priority,
        keyword: keyword.trim(),
        publishedFrom: publishedFrom ? `${publishedFrom}T00:00:00` : undefined,
        publishedTo: publishedTo ? `${publishedTo}T23:59:59` : undefined,
        cursor: append ? nextCursor : undefined,
      }), append ? Promise.resolve([]) : listReports()]);
      const nextItems = append ? result.items : mergeInboxWithGeneratingReports(result.items, reports, type);
      setItems((current) =>
        append ? [...(current ?? []), ...nextItems] : nextItems,
      );
      setNextCursor(result.nextCursor ?? null);
    } catch (nextError) {
      setError(nextError);
    } finally {
      if (append) setLoadingMore(false);
    }
  };

  useEffect(() => {
    load();
  }, [type, read, priority, publishedFrom, publishedTo]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!items?.some((item) => item.isGenerating)) return undefined;
    const timer = window.setInterval(() => load(), 1800);
    return () => window.clearInterval(timer);
  }, [items]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!messageId) {
      setSelected(null);
      return;
    }
    setDetailError(null);
    getMessage(messageId)
      .then((message) => {
        setSelected(message);
        setItems((current) =>
          current?.map((item) =>
            item.id === message.id ? { ...item, isRead: true } : item,
          ),
        );
        window.dispatchEvent(new Event("forgesight:messages-changed"));
      })
      .catch(setDetailError);
  }, [messageId]);

  const unread = useMemo(
    () => items?.filter((item) => !item.isRead).length ?? 0,
    [items],
  );
  if (error)
    return (
      <PageFrame>
        <ErrorNotice
          title="消息加载失败"
          detail={error.message}
          onRetry={load}
        />
      </PageFrame>
    );
  if (!items) return <RouteSkeleton />;

  const messageBase = location.pathname.startsWith("/admin/")
    ? "/admin/messages"
    : "/workspace/messages";
  return (
    <PageFrame className="message-workspace max-w-[1440px] gap-4">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <span className="section-kicker">INBOX · {unread} 未读</span>
          <h2 className="mt-1 text-[25px] font-semibold tracking-tight">
            重要信息，在一个地方读完
          </h2>
          <p className="mt-2 text-base text-muted">
            Agent 周报与管理员公告按优先级和时间统一排列。
          </p>
        </div>
        <label className="relative w-full sm:w-72">
          <Icon
            name="search"
            size={16}
            className="absolute left-3 top-1/2 -translate-y-1/2 text-muted"
          />
          <input
            className="input-control h-10 pl-9 text-sm"
            value={keyword}
            onChange={(event) => setKeyword(event.target.value)}
            onKeyDown={(event) => event.key === "Enter" && load()}
            placeholder="搜索标题或摘要，按 Enter"
          />
        </label>
      </header>
      <div className="grid min-h-[620px] overflow-hidden rounded-section border border-line bg-paper shadow-card lg:grid-cols-[minmax(360px,0.88fr)_minmax(420px,1.12fr)]">
        <section
          className={cn(
            "min-w-0 border-b border-line lg:block lg:border-b-0 lg:border-r",
            messageId && "hidden",
          )}
        >
          <div className="flex flex-wrap gap-2 border-b border-line p-3">
            <Filter
              value={type}
              onChange={setType}
              options={[
                ["", "全部"],
                ["weekly_report", "Agent 周报"],
                ["admin_announcement", "管理员公告"],
              ]}
              label="消息类型"
            />
            <Filter
              value={read}
              onChange={setRead}
              options={[
                ["", "全部状态"],
                ["unread", "未读"],
                ["read", "已读"],
              ]}
              label="阅读状态"
            />
            <Filter
              value={priority}
              onChange={setPriority}
              options={[
                ["", "全部级别"],
                ["urgent", "紧急"],
                ["high", "重要"],
                ["normal", "一般"],
              ]}
              label="重要程度"
            />
            <label>
              <span className="sr-only">开始日期</span>
              <input
                type="date"
                className="input-control h-9 w-auto text-sm"
                value={publishedFrom}
                onChange={(event) => setPublishedFrom(event.target.value)}
              />
            </label>
            <label>
              <span className="sr-only">结束日期</span>
              <input
                type="date"
                className="input-control h-9 w-auto text-sm"
                value={publishedTo}
                onChange={(event) => setPublishedTo(event.target.value)}
              />
            </label>
          </div>
          {items.length ? (
            <div className="divide-y divide-line">
              {items.map((item) => (
                <MessageRow
                  key={item.id}
                  item={item}
                  active={messageId === item.id}
                  onOpen={() => navigate(`${messageBase}/${item.id}`)}
                />
              ))}
              {nextCursor && (
                <div className="p-3 text-center">
                  <Button
                    disabled={loadingMore}
                    onClick={() => load({ append: true })}
                  >
                    {loadingMore ? "正在加载…" : "加载更多消息"}
                  </Button>
                </div>
              )}
            </div>
          ) : (
            <EmptyState
              title="当前分类没有消息"
              description="调整筛选条件，或稍后回来查看新周报和公告。"
            />
          )}
        </section>
        <section
          className={cn(
            "min-w-0 bg-surface-muted/55 lg:block",
            !messageId && "hidden",
          )}
        >
          {messageId && (
            <button
              type="button"
              onClick={() => navigate(messageBase)}
              className="m-3 inline-flex min-h-11 items-center gap-2 rounded-control px-2 text-sm font-semibold text-primary lg:hidden"
            >
              <Icon name="chevron" size={15} className="rotate-180" />
              返回消息列表
            </button>
          )}
          {detailError ? (
            <div className="p-6">
              <ErrorNotice
                title="消息无法打开"
                detail={detailError.message}
                onRetry={() => navigate(messageBase)}
              />
            </div>
          ) : selected ? (
            <MessageDetail
              message={selected}
              onOpenReport={(id) => navigate(`/workspace/reports/${id}`)}
            />
          ) : (
            <EmptyState
              title="选择一条消息"
              description="右侧将显示完整公告，或周报摘要与打开入口。"
            />
          )}
        </section>
      </div>
    </PageFrame>
  );
}

function Filter({ value, onChange, options, label }) {
  return (
    <label>
      <span className="sr-only">{label}</span>
      <select
        className="input-control h-9 w-auto min-w-28 text-sm"
        value={value}
        onChange={(event) => onChange(event.target.value)}
      >
        {options.map(([key, text]) => (
          <option key={key} value={key}>
            {text}
          </option>
        ))}
      </select>
    </label>
  );
}

function MessageRow({ item, active, onOpen }) {
  const [priorityLabel, tone] = PRIORITY[item.priority] ?? PRIORITY.normal;
  if (item.isGenerating) return (
    <div className="message-row-generating relative grid w-full grid-cols-[12px_minmax(0,1fr)] gap-3 px-4 py-4 text-left" aria-label={`${item.title}，正在生成，暂不可打开`}>
      <span className="mt-2 h-2 w-2 animate-pulse rounded-full bg-primary" />
      <span className="min-w-0"><span className="flex flex-wrap items-center gap-2"><strong className="text-[13px] text-primary">Agent 周报</strong><StatusPill tone="blue">生成中</StatusPill><time className="ml-auto text-[12px] text-muted">{formatTime(item.publishedAt)}</time></span><span className="mt-2 block truncate text-base font-semibold text-ink">{item.title}</span><span className="mt-1 flex items-center gap-1.5 text-sm leading-6 text-muted"><span className="report-generation-card__dots" aria-hidden="true"><i /><i /><i /></span>正在生成中，暂不可打开</span></span>
    </div>
  );
  return (
    <button
      type="button"
      onClick={onOpen}
      className={cn(
        "relative grid w-full cursor-pointer grid-cols-[12px_minmax(0,1fr)] gap-3 px-4 py-4 text-left transition-colors hover:bg-surface-muted",
        active && "bg-primary-soft",
      )}
    >
      {!item.isRead ? (
        <span
          className="mt-2 h-2 w-2 rounded-full bg-primary"
          aria-label="未读"
        />
      ) : (
        <span />
      )}
      <span className="min-w-0">
        <span className="flex flex-wrap items-center gap-2">
          <strong className="text-[13px] text-primary">
            {item.sourceLabel || TYPE_LABEL[item.type]}
          </strong>
          <StatusPill tone={tone}>{priorityLabel}</StatusPill>
          <time className="ml-auto text-[12px] text-muted">
            {formatTime(item.publishedAt)}
          </time>
        </span>
        <span className="mt-2 block truncate text-base font-semibold text-ink">
          {reportText(item.title) || "未命名消息"}
        </span>
        <span className="mt-1 line-clamp-2 text-sm leading-6 text-muted">
          {reportText(item.summaryLines?.join(" ")) || "没有摘要"}
        </span>
      </span>
    </button>
  );
}

function MessageDetail({ message, onOpenReport }) {
  if (message.type === "weekly_report" && message.primaryAction?.resourceId) {
    return <WeeklyReportMessagePreview message={message} onOpenReport={onOpenReport} />;
  }
  const [priorityLabel, tone] = PRIORITY[message.priority] ?? PRIORITY.normal;
  return (
    <article className="mx-auto max-w-3xl p-5 sm:p-8">
      <header className="border-b border-line pb-6">
        <div className="flex flex-wrap items-center gap-2">
          <span className="section-kicker">
            {message.sourceLabel || TYPE_LABEL[message.type]}
          </span>
          <StatusPill tone={tone}>{priorityLabel}</StatusPill>
          {message.isPinned && <StatusPill tone="blue">置顶</StatusPill>}
        </div>
        <h2 className="mt-4 font-report text-[25px] font-semibold leading-tight text-ink">
          {reportText(message.title) || "未命名消息"}
        </h2>
        <p className="mt-2 text-sm text-muted">
          发布于 {formatTime(message.publishedAt)}
        </p>
      </header>
      <div className="whitespace-pre-wrap py-7 text-base leading-8 text-ink-2">
        {reportText(message.content || message.summaryLines?.join("\n")) || "暂无正文"}
      </div>
      {message.status === "withdrawn" && (
        <p className="rounded-control border border-warning/25 bg-warning-soft p-3 text-sm text-warning">
          该公告已撤回
          {message.withdrawalReason ? `：${message.withdrawalReason}` : ""}
        </p>
      )}
      {message.primaryAction?.type === "open_report" && (
        <div className="mt-6 border-t border-line pt-5">
          <Button
            variant="primary"
            onClick={() => onOpenReport(message.primaryAction.resourceId)}
          >
            查看完整周报 <Icon name="arrow" size={15} />
          </Button>
        </div>
      )}
    </article>
  );
}

function WeeklyReportMessagePreview({ message, onOpenReport }) {
  const [report, setReport] = useState(null);
  useEffect(() => {
    getReport(message.primaryAction.resourceId).then(setReport).catch(() => setReport(false));
  }, [message.primaryAction.resourceId]);
  const view = buildReportView(report || {});
  const summary = reportText(report?.summary || message.content || message.summaryLines?.join(" "));
  const compactSummary = summary.split(/\n{2,}|。/).map((line) => line.trim()).find(Boolean) || "本期周报已发布。";
  return (
    <article className="mx-auto max-w-3xl p-5 sm:p-8">
      <header className="border-b border-line pb-6">
        <div className="flex items-center gap-2"><span className="section-kicker">AGENT WEEKLY BRIEF</span><StatusPill tone="blue">已发布</StatusPill></div>
        <h2 className="mt-4 font-report text-[25px] font-semibold leading-tight text-ink">{reportText(message.title)}</h2>
        <p className="mt-2 text-sm text-muted">发布于 {formatTime(message.publishedAt)} · 已投递绑定邮箱</p>
      </header>
      <section className="mt-6 rounded-section border border-primary/15 bg-primary-soft/45 p-5">
        <span className="section-kicker">本期结论</span>
        <p className="mt-2 text-base leading-7 text-ink">{view.headline !== "本周期尚未形成总体结论。" ? view.headline : compactSummary}</p>
      </section>
      {view.metrics.length ? <div className="mt-5 grid grid-cols-2 gap-px overflow-hidden rounded-section border border-line bg-line">{view.metrics.slice(0, 4).map((metric) => <div key={metric.key} className="bg-paper p-4"><span className="text-xs text-muted">{reportText(metric.name || metric.key)}</span><strong className="mt-1 block text-xl text-ink">{metric.valueLabel}</strong><span className="mt-1 block text-xs text-muted">{metric.changeLabel} · {metric.previousLabel}</span></div>)}</div> : <div className="mt-5 rounded-section border border-amber/25 bg-amber-soft p-4"><strong className="text-sm text-ink">本期数据尚未覆盖</strong><p className="mt-1 text-sm leading-6 text-muted">当前统计周期没有可用业务记录，因此未生成虚假图表或指标。请在完整报告中查看数据范围说明。</p></div>}
      {view.charts.length > 0 && <div className="mt-5"><WeeklyReportCharts charts={view.charts.slice(0, 1)} sourceName={report?.sourceName || "当前数据源"} /></div>}
      <div className="mt-6 border-t border-line pt-5"><Button variant="primary" onClick={() => onOpenReport(message.primaryAction.resourceId)}>查看完整周报 <Icon name="arrow" size={15} /></Button></div>
    </article>
  );
}
