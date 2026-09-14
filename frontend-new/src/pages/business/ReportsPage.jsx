import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import Icon from "../../components/ui/Icon";
import cn from "../../lib/cn";
import { reportListSummary } from "../../lib/narrative";
import {
  filterReportRows,
  reportListMeta,
  reportScopeOptions,
} from "../../lib/reportListFilters";
import {
  Button,
  ErrorNotice,
  Modal,
  PageFrame,
  RouteSkeleton,
  StatusPill,
} from "../../components/ui";
import { listSources } from "../../api/datasources";
import {
  createReport,
  downloadReportFile,
  listReportSubscriptions,
  listReportTemplates,
  listReports,
} from "../../api/reports";

const ACTIVE_SOURCE_KEY = "forgesight:active-source-id";
const TERMINAL = new Set([
  "awaiting_review",
  "review_pending",
  "approved",
  "published",
  "partial",
  "failed",
  "validation_failed",
]);

const statusTone = {
  queued: "gray",
  running_sections: "blue",
  waiting_user: "amber",
  awaiting_review: "blue",
  review_pending: "blue",
  approved: "blue",
  published: "green",
  partial: "amber",
  failed: "red",
  validation_failed: "red",
};

function dateValue(date) {
  return date.toISOString().slice(0, 10);
}

function defaultRange() {
  const end = new Date();
  const start = new Date(end);
  start.setDate(start.getDate() - 7);
  return { start: dateValue(start), end: dateValue(end) };
}

export default function ReportsPage() {
  const navigate = useNavigate();
  const [reports, setReports] = useState(null);
  const [sources, setSources] = useState([]);
  const [templates, setTemplates] = useState([]);
  const [subscriptions, setSubscriptions] = useState([]);
  const [sourceId, setSourceId] = useState(null);
  const [filter, setFilter] = useState("all");
  const [search, setSearch] = useState("");
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [advanced, setAdvanced] = useState({
    periodFrom: "",
    periodTo: "",
    reportType: "",
    scope: "",
    severity: "",
    read: "",
  });
  const [downloadBusy, setDownloadBusy] = useState("");
  const [downloadNotice, setDownloadNotice] = useState("");
  const [error, setError] = useState(null);
  const [createOpen, setCreateOpen] = useState(false);

  const load = async () => {
    setError(null);
    try {
      const availableSources = await listSources();
      const stored = Number(window.sessionStorage.getItem(ACTIVE_SOURCE_KEY));
      const selected =
        availableSources.find((item) => item.id === stored) ??
        availableSources.find((item) => item.isDefault) ??
        availableSources[0];
      const selectedId = selected?.id ?? null;
      setSources(availableSources);
      setSourceId(selectedId);
      if (selectedId)
        window.sessionStorage.setItem(ACTIVE_SOURCE_KEY, String(selectedId));
      const [reportItems, templateItems, subscriptionItems] = await Promise.all(
        [
          listReports({ sourceId: selectedId }),
          selectedId ? listReportTemplates(selectedId) : [],
          listReportSubscriptions(),
        ],
      );
      setReports(reportItems);
      setTemplates(templateItems);
      setSubscriptions(subscriptionItems);
    } catch (nextError) {
      setError(nextError);
    }
  };

  useEffect(() => {
    load();
  }, []);

  useEffect(() => {
    if (!reports?.some((item) => !TERMINAL.has(item.status))) return undefined;
    const timer = window.setInterval(async () => {
      try {
        setReports(await listReports({ sourceId }));
      } catch {
        // 页面已有数据时保留当前结果，下一轮继续刷新。
      }
    }, 1800);
    return () => window.clearInterval(timer);
  }, [reports, sourceId]);

  const visible = useMemo(() => {
    const keyword = search.trim().toLowerCase();
    const statusRows = (reports ?? []).filter((report) => {
      const matchesFilter =
        filter === "all" ||
        (filter === "attention" &&
          (report.reportOverview?.anomalies?.length ||
            report.status === "failed" ||
            report.status === "partial")) ||
        (filter === "published" && report.status === "published") ||
        (filter === "running" && !TERMINAL.has(report.status));
      return matchesFilter;
    });
    return filterReportRows(statusRows, { ...advanced, keyword });
  }, [advanced, filter, reports, search]);

  const download = async (report, format) => {
    const file = report.files?.find((item) => item.format === format);
    setDownloadNotice("");
    if (!file || file.status !== "ready") {
      setDownloadNotice(
        `${report.title}的 ${format === "pdf" ? "PDF" : "Excel"} 文件尚未就绪`,
      );
      return;
    }
    setDownloadBusy(`${report.id}-${format}`);
    try {
      await downloadReportFile(file, report.title);
    } catch (nextError) {
      setDownloadNotice(nextError.message);
    } finally {
      setDownloadBusy("");
    }
  };

  const switchSource = async (event) => {
    const nextId = Number(event.target.value);
    setSourceId(nextId);
    window.sessionStorage.setItem(ACTIVE_SOURCE_KEY, String(nextId));
    const [nextReports, nextTemplates] = await Promise.all([
      listReports({ sourceId: nextId }),
      listReportTemplates(nextId),
    ]);
    setReports(nextReports);
    setTemplates(nextTemplates);
  };

  if (error) {
    return (
      <PageFrame>
        <ErrorNotice
          title="报告中心加载失败"
          detail={error.message}
          onRetry={load}
        />
      </PageFrame>
    );
  }
  if (!reports) return <RouteSkeleton />;

  const publishedCount = reports.filter(
    (item) => item.status === "published",
  ).length;
  const attentionCount = reports.filter(
    (item) =>
      item.reportOverview?.anomalies?.length ||
      ["failed", "partial"].includes(item.status),
  ).length;
  const runningCount = reports.filter(
    (item) => !TERMINAL.has(item.status),
  ).length;
  const scopeOptions = reportScopeOptions(reports);

  return (
    <div className="flex h-full min-h-0 flex-col bg-canvas">
      <header className="flex h-12 shrink-0 flex-wrap items-center gap-2 border-b border-line bg-paper px-3">
        <div className="flex min-w-0 items-baseline gap-2">
          <h2 className="text-sm font-semibold tracking-tight text-ink">周报中心</h2>
          <span className="hidden truncate text-[11px] text-faint md:block">正文、证据与导出文件对应同一冻结版本</span>
        </div>
        <div className="ml-auto flex min-w-0 flex-wrap items-center gap-1.5">
          <label className="relative flex items-center">
            <Icon name="database" size={13} className="pointer-events-none absolute left-2 text-faint" />
            <select
              value={sourceId ?? ""}
              onChange={switchSource}
              aria-label="数据源"
              className="input-control h-7 max-w-[190px] appearance-none pl-7 pr-6 text-xs"
            >
              {sources.map((source) => (
                <option key={source.id} value={source.id}>{source.name}</option>
              ))}
            </select>
          </label>
          <Button size="sm" onClick={() => setCreateOpen(true)} disabled={!sourceId}>创建报告</Button>
          <Button size="sm" variant="primary" onClick={() => navigate("/workspace/reports/composer")} disabled={!sourceId}>
            <Icon name="edit" size={14} />
            设计周报
          </Button>
        </div>
      </header>

      <div className="flex shrink-0 flex-wrap items-center gap-x-4 gap-y-1 border-b border-line bg-surface-2/60 px-3 py-1.5 text-[11px]">
        <span className="font-semibold text-ink">{attentionCount ? `${attentionCount} 份报告需处理` : "本周期运行状态正常"}</span>
        <span className="hidden h-3 w-px bg-line sm:block" />
        <CountChip label="生成中" value={runningCount} />
        <CountChip label="需关注" value={attentionCount} tone={attentionCount ? "amber" : "muted"} />
        <CountChip label="已发布" value={publishedCount} tone="teal" />
        <span className="ml-auto flex items-center gap-1.5 text-faint">
          <i className="h-1.5 w-1.5 shrink-0 rounded-full bg-teal" aria-hidden="true" />
          自动送达 {subscriptions.filter((item) => item.enabled).length} 个订阅 · 仅发送已发布版本
        </span>
      </div>

      <div className="flex min-h-0 flex-1 flex-col xl:flex-row">
        <section className="flex min-h-0 min-w-0 flex-1 flex-col">
          <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-line bg-paper px-3 py-2">
            <div className="flex items-center gap-0.5 rounded-control bg-surface-2 p-0.5">
              <FilterTab active={filter === "all"} onClick={() => setFilter("all")}>全部 {reports.length}</FilterTab>
              <FilterTab active={filter === "running"} onClick={() => setFilter("running")}>生成中 {runningCount}</FilterTab>
              <FilterTab active={filter === "attention"} onClick={() => setFilter("attention")}>需关注 {attentionCount}</FilterTab>
              <FilterTab active={filter === "published"} onClick={() => setFilter("published")}>已发布 {publishedCount}</FilterTab>
            </div>
            <label className="relative ml-auto flex items-center">
              <Icon name="search" size={13} className="pointer-events-none absolute left-2.5 text-faint" />
              <input
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="搜索报告"
                aria-label="搜索报告"
                className="input-control h-7 w-44 pl-8 pr-2.5 text-xs"
              />
            </label>
            <Button size="sm" variant="ghost" onClick={() => setAdvancedOpen((value) => !value)}>
              <Icon name="filter" size={13} />
              {advancedOpen ? "收起筛选" : "更多筛选"}
            </Button>
          </div>
          {advancedOpen && (
            <div className="grid gap-2 border-b border-line bg-surface-muted p-3 sm:grid-cols-2 xl:grid-cols-6">
              <FilterSelect
                label="报告类型"
                value={advanced.reportType}
                onChange={(value) =>
                  setAdvanced({ ...advanced, reportType: value })
                }
                options={[
                  ["", "全部类型"],
                  ["weekly", "周期周报"],
                  ["topic", "专题报告"],
                ]}
              />
              <FilterSelect
                label="组织范围"
                value={advanced.scope}
                onChange={(value) => setAdvanced({ ...advanced, scope: value })}
                options={[
                  ["", "全部范围"],
                  ...scopeOptions.map((item) => [item, item]),
                ]}
              />
              <FilterSelect
                label="异常等级"
                value={advanced.severity}
                onChange={(value) =>
                  setAdvanced({ ...advanced, severity: value })
                }
                options={[
                  ["", "全部异常"],
                  ["critical", "严重"],
                  ["high", "高"],
                  ["medium", "中"],
                  ["low", "低"],
                  ["none", "无异常"],
                ]}
              />
              <FilterSelect
                label="阅读状态"
                value={advanced.read}
                onChange={(value) => setAdvanced({ ...advanced, read: value })}
                options={[
                  ["", "全部阅读状态"],
                  ["unread", "未读"],
                  ["read", "已读"],
                ]}
              />
              <label className="grid gap-1 text-[12px] font-semibold text-muted">
                <span>周期开始</span>
                <input
                  type="date"
                  className="input-control h-9"
                  value={advanced.periodFrom}
                  onChange={(event) =>
                    setAdvanced({ ...advanced, periodFrom: event.target.value })
                  }
                />
              </label>
              <label className="grid gap-1 text-[12px] font-semibold text-muted">
                <span>周期结束</span>
                <input
                  type="date"
                  className="input-control h-9"
                  value={advanced.periodTo}
                  onChange={(event) =>
                    setAdvanced({ ...advanced, periodTo: event.target.value })
                  }
                />
              </label>
              <button
                type="button"
                onClick={() =>
                  setAdvanced({
                    periodFrom: "",
                    periodTo: "",
                    reportType: "",
                    scope: "",
                    severity: "",
                    read: "",
                  })
                }
                className="justify-self-start text-[12px] font-semibold text-primary xl:col-span-6"
              >
                清除更多筛选
              </button>
            </div>
          )}
          {downloadNotice && (
            <p
              className="shrink-0 border-b border-warning/20 bg-warning-soft px-3 py-1.5 text-xs text-warning"
              role="status"
            >
              {downloadNotice}
            </p>
          )}
          <div className="min-h-0 flex-1 overflow-auto">
            {visible.length ? (
              <div className="divide-y divide-line">
                {visible.map((report) => {
                  const meta = reportListMeta(report);
                  const tone = statusTone[report.status] ?? "gray";
                  return (
                    <article
                      key={report.id}
                      className="group relative grid gap-2 py-2.5 pl-4 pr-3 transition-colors hover:bg-surface-2 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-center sm:gap-4"
                    >
                      <span className={cn("absolute left-0 top-2 bottom-2 w-[3px] rounded-full", tone === "green" ? "bg-teal" : tone === "amber" ? "bg-amber" : tone === "red" ? "bg-red" : tone === "blue" ? "bg-primary" : "bg-line-strong")} aria-hidden="true" />
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <StatusPill tone={tone}>{report.statusLabel}</StatusPill>
                          {meta.anomalyCount > 0 && (
                            <StatusPill tone={meta.highestSeverity === "critical" || meta.highestSeverity === "high" ? "red" : "amber"}>
                              {meta.anomalyCount} 项异常
                            </StatusPill>
                          )}
                          <h3 className="min-w-0 truncate text-sm font-semibold text-ink">{report.title}</h3>
                        </div>
                        <p className="mt-1 line-clamp-1 text-xs leading-5 text-muted">
                          {meta.headline
                            ? reportListSummary(meta.headline)
                            : TERMINAL.has(report.status)
                              ? "尚未形成管理摘要。"
                              : "正在执行章节分析，页面会自动更新真实进度。"}
                        </p>
                        <p className="mt-1 truncate font-mono text-[10px] text-faint">
                          {report.templateName} · V{report.version?.version ?? 1} · 完整度 {meta.coverage == null ? "—" : `${meta.coverage}%`} · {meta.scopeLabel}
                        </p>
                      </div>
                      <div className="flex shrink-0 items-center gap-2 sm:flex-col sm:items-end sm:gap-1.5">
                        <span className="font-mono text-[10px] text-faint sm:order-first">
                          {report.periodStart?.slice(0, 10)} — {report.periodEnd?.slice(5, 10)}
                        </span>
                        <div className="flex flex-wrap gap-1.5 sm:order-last">
                          {report.files?.some((item) => item.format === "pdf" && item.status === "ready") && (
                            <Button size="sm" variant="ghost" disabled={downloadBusy === `${report.id}-pdf`} onClick={() => download(report, "pdf")}>
                              <Icon name="download" size={13} />
                              PDF
                            </Button>
                          )}
                          {report.files?.some((item) => item.format === "xlsx" && item.status === "ready") && (
                            <Button size="sm" variant="ghost" disabled={downloadBusy === `${report.id}-xlsx`} onClick={() => download(report, "xlsx")}>
                              <Icon name="download" size={13} />
                              Excel
                            </Button>
                          )}
                          <Button size="sm" onClick={() => navigate(`/workspace/reports/${report.id}`)}>
                            详情 <Icon name="arrow" size={13} />
                          </Button>
                        </div>
                      </div>
                    </article>
                  );
                })}
              </div>
            ) : (
              <div className="grid min-h-44 place-content-center px-6 py-10 text-center"><p className="text-sm font-semibold text-ink">这里还没有报告</p><p className="mt-1 text-xs text-muted">设计一份周报，或调整当前筛选条件。</p><button type="button" onClick={() => navigate("/workspace/reports/composer")} className="mt-3 text-xs font-semibold text-primary hover:opacity-75">打开周报设计器 →</button></div>
            )}
          </div>
        </section>

        <aside className="flex w-full shrink-0 flex-col border-t border-line bg-paper xl:w-[320px] xl:border-t-0 xl:border-l">
          <div className="flex h-9 shrink-0 items-center justify-between border-b border-line px-3">
            <h3 className="text-xs font-semibold text-ink">模板与覆盖</h3>
            <span className="font-mono text-[10px] text-faint">{templates.length} 个可生成</span>
          </div>
          <div className="min-h-0 flex-1 overflow-auto">
            {templates.map((template) => {
              const score = template.coverage?.score ?? 0;
              const ready = template.coverage?.ready;
              return (
                <button
                  key={template.id}
                  type="button"
                  onClick={() => setCreateOpen(template.id)}
                  className="group flex w-full items-center gap-2.5 border-b border-line/60 px-3 py-2.5 text-left transition-colors last:border-0 hover:bg-surface-2"
                >
                  <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", ready ? "bg-teal" : "bg-amber")} />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-xs font-semibold text-ink">{template.name}</span>
                    <span className="mt-0.5 block truncate text-[10px] text-muted">{template.audience}</span>
                    <span className="mt-1.5 block h-1 overflow-hidden rounded-full bg-surface-3">
                      <i className={cn("block h-full rounded-full", ready ? "bg-teal" : "bg-amber")} style={{ width: `${score}%` }} />
                    </span>
                  </span>
                  <span className={cn("shrink-0 font-mono text-[11px] font-semibold", ready ? "text-teal" : "text-amber")}>{score}%</span>
                </button>
              );
            })}
          </div>
        </aside>
      </div>

      <ReportCreateWizard
        open={Boolean(createOpen)}
        initialTemplateId={typeof createOpen === "string" ? createOpen : null}
        sourceId={sourceId}
        sources={sources}
        templates={templates}
        onClose={() => setCreateOpen(false)}
        onCreated={(report) => navigate(`/workspace/reports/${report.id}`)}
      />
    </div>
  );
}

function ReportCreateWizard({
  open,
  initialTemplateId,
  sourceId,
  sources,
  templates,
  onClose,
  onCreated,
}) {
  const range = useMemo(defaultRange, [open]);
  const [step, setStep] = useState(1);
  const [templateId, setTemplateId] = useState(initialTemplateId ?? "");
  const [availableTemplates, setAvailableTemplates] = useState(templates);
  const [selectedSourceId, setSelectedSourceId] = useState(sourceId ?? "");
  const [periodStart, setPeriodStart] = useState(range.start);
  const [periodEnd, setPeriodEnd] = useState(range.end);
  const [prompt, setPrompt] = useState("");
  const [title, setTitle] = useState("");
  const [emailEnabled, setEmailEnabled] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [loadingTemplates, setLoadingTemplates] = useState(false);
  const templateRequest = useRef(0);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!open) return;
    templateRequest.current += 1;
    setStep(1);
    setAvailableTemplates(templates);
    setTemplateId(
      initialTemplateId ??
        templates.find((item) => item.coverage?.ready)?.id ??
        templates[0]?.id ??
        "",
    );
    setSelectedSourceId(sourceId ?? "");
    setPeriodStart(range.start);
    setPeriodEnd(range.end);
    setPrompt("");
    setTitle("");
    setEmailEnabled(true);
    setError(null);
  }, [initialTemplateId, open, range.end, range.start, sourceId, templates]);

  const template = availableTemplates.find((item) => item.id === templateId);
  const changeSource = async (nextSourceId) => {
    const requestId = ++templateRequest.current;
    setSelectedSourceId(nextSourceId);
    setAvailableTemplates([]);
    setTemplateId("");
    setLoadingTemplates(true);
    setError(null);
    try {
      const nextTemplates = await listReportTemplates(Number(nextSourceId));
      if (requestId !== templateRequest.current) return;
      setAvailableTemplates(nextTemplates);
      setTemplateId(
        nextTemplates.find((item) => item.coverage?.ready)?.id ??
          nextTemplates[0]?.id ??
          "",
      );
    } catch (nextError) {
      if (requestId !== templateRequest.current) return;
      setAvailableTemplates([]);
      setTemplateId("");
      setError(nextError);
    } finally {
      if (requestId === templateRequest.current) setLoadingTemplates(false);
    }
  };
  const canContinue =
    step === 1
      ? Boolean(
          !loadingTemplates &&
          templateId &&
          selectedSourceId &&
          template?.coverage?.ready,
        )
      : step === 2
        ? Boolean(
            periodStart &&
            periodEnd &&
            (templateId !== "tpl-topic-report" || prompt.trim()),
          )
        : true;

  const submit = async () => {
    setSubmitting(true);
    setError(null);
    try {
      const created = await createReport({
        templateId,
        sourceId: Number(selectedSourceId),
        periodStart: `${periodStart}T00:00:00`,
        periodEnd: `${periodEnd}T23:59:59`,
        prompt: prompt.trim() || undefined,
        title: title.trim() || undefined,
        emailEnabled,
      });
      onCreated(created);
    } catch (nextError) {
      setError(nextError);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      open={open}
      onClose={submitting ? () => {} : onClose}
      label="创建报告"
      maxWidth={760}
    >
      <div className="flex items-center justify-between border-b border-line px-4 py-3">
        <div className="flex items-baseline gap-2">
          <h3 className="text-sm font-semibold text-ink">创建一份可复核报告</h3>
          <span className="hidden text-[10px] font-semibold tracking-[0.08em] text-faint sm:inline">CREATE REPORT</span>
        </div>
        <button
          type="button"
          aria-label="关闭报告创建窗口"
          onClick={onClose}
          className="grid h-8 w-8 cursor-pointer place-items-center rounded-control text-muted hover:bg-surface-3"
        >
          <Icon name="close" size={16} />
        </button>
      </div>
      <div className="grid border-b border-line sm:grid-cols-4">
        {["类型与数据源", "范围与问题", "确认大纲", "开始生成"].map(
          (label, index) => (
            <div
              key={label}
              className={cn(
                "flex items-center gap-2 px-4 py-2 text-xs",
                step === index + 1
                  ? "bg-primary-soft font-semibold text-primary"
                  : "text-muted",
              )}
            >
              <span className="font-mono text-[10px]">{index + 1}</span>
              {label}
            </div>
          ),
        )}
      </div>
      <div className="min-h-72 p-4">
        {step === 1 && (
          <div className="grid gap-3">
            <label className="grid gap-1 text-[11px] font-semibold text-muted">
              <span>数据源</span>
              <select
                value={selectedSourceId}
                onChange={(event) => changeSource(event.target.value)}
                className="input-control h-9 max-w-sm text-xs"
                disabled={loadingTemplates}
              >
                {sources.map((source) => (
                  <option key={source.id} value={source.id}>
                    {source.name}
                  </option>
                ))}
              </select>
            </label>
            <div>
              <div className="flex items-center justify-between">
                <span className="text-[11px] font-semibold text-muted">报告类型</span>
                {loadingTemplates && (
                  <span className="text-[11px] text-faint" role="status">正在检查覆盖率…</span>
                )}
              </div>
              <div className="mt-1.5 grid gap-1.5 sm:grid-cols-2">
                {availableTemplates.map((item) => {
                  const selected = templateId === item.id;
                  const ready = item.coverage?.ready;
                  return (
                    <button
                      key={item.id}
                      type="button"
                      onClick={() => setTemplateId(item.id)}
                      aria-pressed={selected}
                      className={cn(
                        "flex cursor-pointer items-start gap-2 rounded-control border px-3 py-2.5 text-left transition-colors",
                        selected ? "border-primary bg-primary-soft" : "border-line bg-surface hover:border-line-strong",
                      )}
                    >
                      <i className={cn("mt-1 h-1.5 w-1.5 shrink-0 rounded-full", ready ? "bg-teal" : "bg-amber")} aria-hidden="true" />
                      <span className="min-w-0 flex-1">
                        <span className="flex items-baseline justify-between gap-2">
                          <strong className="truncate text-xs font-semibold text-ink">{item.name}</strong>
                          <span className={cn("shrink-0 font-mono text-[11px] font-semibold", ready ? "text-teal" : "text-amber")}>{item.coverage?.score}%</span>
                        </span>
                        <span className="mt-0.5 block text-[11px] leading-4 text-muted">{item.description}</span>
                      </span>
                    </button>
                  );
                })}
              </div>
            </div>
          </div>
        )}
        {step === 2 && (
          <div className="mx-auto grid max-w-xl gap-4">
            <div className="grid grid-cols-2 gap-3">
              <Field label="周期开始">
                <input
                  type="date"
                  value={periodStart}
                  onChange={(event) => setPeriodStart(event.target.value)}
                  className="input-control"
                />
              </Field>
              <Field label="周期结束">
                <input
                  type="date"
                  value={periodEnd}
                  onChange={(event) => setPeriodEnd(event.target.value)}
                  className="input-control"
                />
              </Field>
            </div>
            {templateId === "tpl-topic-report" && (
              <Field label="专题问题">
                <textarea
                  value={prompt}
                  onChange={(event) => setPrompt(event.target.value)}
                  rows={4}
                  placeholder="例如：分析 7 月工序良率下降的主要影响对象"
                  className="input-control h-auto resize-none py-2.5"
                />
              </Field>
            )}
            <Field label="报告标题（可选）">
              <input
                value={title}
                onChange={(event) => setTitle(event.target.value)}
                placeholder="留空将按周期和模板自动命名"
                className="input-control"
              />
            </Field>
            <label className="flex cursor-pointer items-start gap-3 rounded-card border border-line bg-surface-2 p-3">
              <input
                type="checkbox"
                aria-label="发布后发送到绑定邮箱"
                checked={emailEnabled}
                onChange={(event) => setEmailEnabled(event.target.checked)}
                className="mt-0.5 h-4 w-4 accent-primary"
              />
              <span className="grid gap-0.5 text-xs">
                <span className="font-semibold text-ink">
                  发布后发送到绑定邮箱
                </span>
                <span className="font-normal leading-relaxed text-muted">
                  默认开启，可取消。邮件包含摘要、报告链接和 PDF 文件。
                </span>
              </span>
            </label>
            <div className="rounded-card border border-line bg-surface-2 p-3 text-xs leading-relaxed text-muted">
              默认按已选周期生成。周期不完整或比较基线缺失时，报告会降低可信度并显示限制说明，不会强行给出比较结论。
            </div>
          </div>
        )}
        {step === 3 && (
          <div className="mx-auto max-w-xl">
            <div className="flex items-center justify-between">
              <div>
                <span className="text-xs font-semibold text-ink">
                  {template?.name}
                </span>
                <p className="mt-1 text-[11px] text-muted">
                  {template?.audience} · {periodStart} 至 {periodEnd}
                </p>
              </div>
              <StatusPill tone={template?.coverage?.ready ? "green" : "amber"}>
                覆盖 {template?.coverage?.score}%
              </StatusPill>
            </div>
            <div className="mt-4 divide-y divide-line rounded-card border border-line">
              {template?.sections?.map((section, index) => (
                <div
                  key={section.code}
                  className="flex items-start gap-3 px-3.5 py-3"
                >
                  <span className="grid h-6 w-6 shrink-0 place-items-center rounded-tag bg-surface-3 font-mono text-[10px] text-ink">
                    {index + 1}
                  </span>
                  <div>
                    <strong className="text-xs font-semibold text-ink">
                      {section.title}
                    </strong>
                    <p className="mt-0.5 text-[11px] leading-relaxed text-muted">
                      {section.question?.replace("{prompt}", prompt) || prompt}
                    </p>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
        {step === 4 && (
          <div className="mx-auto max-w-xl">
            <div className="rounded-panel border border-teal/25 bg-teal-soft p-4">
              <div className="flex items-center gap-2 text-teal">
                <Icon name="shield" size={17} />
                <strong className="text-sm">生成前检查已完成</strong>
              </div>
              <p className="mt-2 text-xs leading-relaxed text-ink-2">
                将创建 {template?.sections?.length ?? 0}{" "}
                个真实分析任务。SQL、结果集、口径和置信度会绑定到对应章节；周报通过发布校验后由
                Agent 自动发布，并进入站内消息与邮件队列。
              </p>
            </div>
            <dl className="mt-4 grid grid-cols-2 gap-x-6 gap-y-3 text-xs">
              <Summary
                label="数据源"
                value={
                  sources.find((item) => item.id === Number(selectedSourceId))
                    ?.name
                }
              />
              <Summary label="报告类型" value={template?.name} />
              <Summary
                label="统计周期"
                value={`${periodStart} 至 ${periodEnd}`}
              />
              <Summary
                label="指标覆盖"
                value={`${template?.coverage?.score ?? 0}%`}
              />
              <Summary
                label="邮箱推送"
                value={emailEnabled ? "已开启（摘要 + 链接 + PDF）" : "已关闭"}
              />
            </dl>
          </div>
        )}
        {error && (
          <div className="mt-4 rounded-control border border-red/25 bg-red-soft px-3 py-2 text-xs text-red">
            {error.message}
          </div>
        )}
      </div>
      <div className="flex items-center justify-between border-t border-line bg-surface-2 px-5 py-3">
        <Button
          variant="ghost"
          onClick={step === 1 ? onClose : () => setStep((value) => value - 1)}
          disabled={submitting}
        >
          {step === 1 ? "取消" : "上一步"}
        </Button>
        {step < 4 ? (
          <Button
            variant="primary"
            onClick={() => setStep((value) => value + 1)}
            disabled={!canContinue}
          >
            继续 <Icon name="arrow" size={13} />
          </Button>
        ) : (
          <Button variant="primary" onClick={submit} disabled={submitting}>
            {submitting ? "正在创建…" : "开始生成"}
          </Button>
        )}
      </div>
    </Modal>
  );
}

function CountChip({ label, value, tone = "muted" }) {
  const dot = tone === "teal" ? "bg-teal" : tone === "amber" ? "bg-amber" : "bg-line-strong";
  return (
    <span className="inline-flex items-center gap-1.5 text-muted">
      <i className={cn("h-1.5 w-1.5 shrink-0 rounded-full", dot)} aria-hidden="true" />
      <strong className="font-mono text-[12px] font-semibold text-ink">{value}</strong>
      {label}
    </span>
  );
}

function FilterTab({ active, onClick, children }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "h-7 cursor-pointer rounded-tag px-2.5 text-xs transition-colors",
        active
          ? "bg-surface font-semibold text-ink shadow-card"
          : "text-muted hover:text-ink",
      )}
    >
      {children}
    </button>
  );
}

function FilterSelect({ label, value, onChange, options }) {
  return (
    <label className="grid gap-1 text-[12px] font-semibold text-muted">
      <span>{label}</span>
      <select
        className="input-control h-9"
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

function Field({ label, children }) {
  return (
    <label className="grid gap-1.5 text-xs font-semibold text-ink">
      {label}
      {children}
    </label>
  );
}

function Summary({ label, value }) {
  return (
    <div className="border-b border-line pb-2">
      <dt className="text-[11px] text-faint">{label}</dt>
      <dd className="mt-1 font-medium text-ink">{value}</dd>
    </div>
  );
}
