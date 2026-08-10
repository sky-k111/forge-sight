import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import Icon from "../../components/ui/Icon";
import cn from "../../lib/cn";
import { Button, Card, EmptyState, ErrorNotice, Modal, PageFrame, RouteSkeleton, StatusPill } from "../../components/ui";
import { listSources } from "../../api/datasources";
import {
  createReport,
  getReportIntegrations,
  listReportSubscriptions,
  listReportTemplates,
  listReports,
} from "../../api/reports";

const ACTIVE_SOURCE_KEY = "forgesight:active-source-id";
const TERMINAL = new Set(["awaiting_review", "review_pending", "approved", "published", "partial", "failed"]);

const statusTone = {
  queued: "gray",
  running_sections: "blue",
  waiting_user: "amber",
  awaiting_review: "teal",
  review_pending: "amber",
  approved: "blue",
  published: "green",
  partial: "amber",
  failed: "red",
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
  const [integrations, setIntegrations] = useState(null);
  const [sourceId, setSourceId] = useState(null);
  const [filter, setFilter] = useState("all");
  const [search, setSearch] = useState("");
  const [error, setError] = useState(null);
  const [createOpen, setCreateOpen] = useState(false);

  const load = async () => {
    setError(null);
    try {
      const availableSources = await listSources();
      const stored = Number(window.sessionStorage.getItem(ACTIVE_SOURCE_KEY));
      const selected = availableSources.find((item) => item.id === stored)
        ?? availableSources.find((item) => item.isDefault)
        ?? availableSources[0];
      const selectedId = selected?.id ?? null;
      setSources(availableSources);
      setSourceId(selectedId);
      if (selectedId) window.sessionStorage.setItem(ACTIVE_SOURCE_KEY, String(selectedId));
      const [reportItems, templateItems, subscriptionItems, integrationState] = await Promise.all([
        listReports(),
        selectedId ? listReportTemplates(selectedId) : [],
        listReportSubscriptions(),
        getReportIntegrations(),
      ]);
      setReports(reportItems);
      setTemplates(templateItems);
      setSubscriptions(subscriptionItems);
      setIntegrations(integrationState);
    } catch (nextError) {
      setError(nextError);
    }
  };

  useEffect(() => {
    load();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!reports?.some((item) => !TERMINAL.has(item.status))) return undefined;
    const timer = window.setInterval(async () => {
      try {
        setReports(await listReports());
      } catch {
        // 页面已有数据时保留当前结果，下一轮继续刷新。
      }
    }, 1800);
    return () => window.clearInterval(timer);
  }, [reports]);

  const visible = useMemo(() => {
    const keyword = search.trim().toLowerCase();
    return (reports ?? []).filter((report) => {
      const matchesSearch = !keyword || `${report.title} ${report.templateName} ${report.sourceName}`.toLowerCase().includes(keyword);
      const matchesFilter = filter === "all"
        || (filter === "review" && ["awaiting_review", "review_pending"].includes(report.status))
        || (filter === "published" && report.status === "published")
        || (filter === "running" && !TERMINAL.has(report.status));
      return matchesSearch && matchesFilter;
    });
  }, [filter, reports, search]);

  const switchSource = async (event) => {
    const nextId = Number(event.target.value);
    setSourceId(nextId);
    window.sessionStorage.setItem(ACTIVE_SOURCE_KEY, String(nextId));
    setTemplates(await listReportTemplates(nextId));
  };

  if (error) {
    return (
      <PageFrame>
        <ErrorNotice title="报告中心加载失败" detail={error.message} onRetry={load} />
      </PageFrame>
    );
  }
  if (!reports) return <RouteSkeleton />;

  const publishedCount = reports.filter((item) => item.status === "published").length;
  const reviewCount = reports.filter((item) => ["awaiting_review", "review_pending"].includes(item.status)).length;
  const runningCount = reports.filter((item) => !TERMINAL.has(item.status)).length;

  return (
    <PageFrame className="gap-4">
      <div className="flex flex-wrap items-end justify-between gap-5">
        <div>
          <span className="text-[11px] font-semibold tracking-[0.08em] text-faint">REPORT WORKSPACE</span>
          <h2 className="mt-1 text-xl font-semibold tracking-tight text-ink">报告中心</h2>
          <p className="mt-1 text-body text-muted">从已验证的数据与口径生成可复核、可追溯的周期报告。</p>
        </div>
        <div className="flex items-center gap-2">
          <label className="flex h-9 items-center gap-2 rounded-control border border-line bg-surface px-3 text-xs text-muted">
            <Icon name="database" size={14} />
            <select
              value={sourceId ?? ""}
              onChange={switchSource}
              className="max-w-44 cursor-pointer bg-transparent font-medium text-ink outline-none"
            >
              {sources.map((source) => <option key={source.id} value={source.id}>{source.name}</option>)}
            </select>
          </label>
          <Button variant="primary" onClick={() => setCreateOpen(true)} disabled={!sourceId}>
            <Icon name="plus" size={15} />创建报告
          </Button>
        </div>
      </div>

      <Card className="grid overflow-hidden md:grid-cols-[1.35fr_0.65fr]">
        <div className="border-b border-line p-5 md:border-b-0 md:border-r">
          <div className="flex items-start justify-between gap-5">
            <div>
              <span className="text-[11px] font-semibold text-primary">本周期工作台</span>
              <h3 className="mt-1 text-lg font-semibold tracking-tight text-ink">
                {reviewCount ? `${reviewCount} 份报告等待业务确认` : "报告流程运行正常"}
              </h3>
              <p className="mt-1.5 max-w-2xl text-body text-muted">
                Agent 负责计算和起草，业务责任人确认解释与行动项后再发布。已发布版本不会被后续重算覆盖。
              </p>
            </div>
            <span className="grid h-10 w-10 shrink-0 place-items-center rounded-control bg-primary-soft text-primary">
              <Icon name="report" size={19} />
            </span>
          </div>
          <div className="mt-5 grid grid-cols-3 divide-x divide-line border-t border-line pt-4">
            <Metric label="生成中" value={runningCount} />
            <Metric label="待确认" value={reviewCount} />
            <Metric label="已发布" value={publishedCount} />
          </div>
        </div>
        <div className="grid content-between gap-5 bg-surface-2 p-5">
          <div>
            <div className="flex items-center justify-between gap-3">
              <span className="text-[11px] font-semibold text-faint">外部送达</span>
              <StatusPill tone={integrations?.feishu?.configured ? "green" : "gray"}>
                {integrations?.feishu?.configured ? "飞书已连接" : "飞书待配置"}
              </StatusPill>
            </div>
            <p className="mt-2 text-body font-semibold text-ink">发布后推送到业务群</p>
            <p className="mt-1 text-xs leading-relaxed text-muted">
              卡片包含周期、数据源、可信度和管理摘要，完整报告仍在 ForgeSight 内查看。
            </p>
          </div>
          <div className="flex items-center justify-between border-t border-line pt-3 text-xs">
            <span className="text-muted">{subscriptions.filter((item) => item.enabled).length} 个启用中的订阅</span>
            <span className="font-medium text-ink">仅推送已发布版本</span>
          </div>
        </div>
      </Card>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_340px]">
        <Card className="min-w-0 overflow-hidden">
          <div className="flex flex-wrap items-center gap-3 border-b border-line px-4 py-3">
            <div className="flex items-center gap-0.5 rounded-control bg-surface-2 p-0.5">
              <FilterTab active={filter === "all"} onClick={() => setFilter("all")}>全部 {reports.length}</FilterTab>
              <FilterTab active={filter === "running"} onClick={() => setFilter("running")}>生成中 {runningCount}</FilterTab>
              <FilterTab active={filter === "review"} onClick={() => setFilter("review")}>待确认 {reviewCount}</FilterTab>
              <FilterTab active={filter === "published"} onClick={() => setFilter("published")}>已发布 {publishedCount}</FilterTab>
            </div>
            <label className="relative ml-auto block w-52">
              <Icon name="search" size={14} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-faint" />
              <input
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="搜索报告"
                className="h-8 w-full rounded-control border border-line bg-surface pl-8 pr-2.5 text-xs outline-none placeholder:text-faint"
              />
            </label>
          </div>
          {visible.length ? (
            <div className="divide-y divide-line">
              {visible.map((report) => (
                <button
                  key={report.id}
                  type="button"
                  onClick={() => navigate(`/workspace/reports/${report.id}`)}
                  className="group grid w-full cursor-pointer grid-cols-[minmax(0,1fr)_auto] gap-5 px-4 py-4 text-left transition-colors hover:bg-surface-2"
                >
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <StatusPill tone={statusTone[report.status] ?? "gray"}>{report.statusLabel}</StatusPill>
                      <span className="truncate text-[11px] text-faint">{report.templateName} · {report.sourceName}</span>
                    </div>
                    <h3 className="mt-2 truncate text-body font-semibold text-ink">{report.title}</h3>
                    <p className="mt-1 line-clamp-2 text-xs leading-relaxed text-muted">
                      {report.summary || (TERMINAL.has(report.status) ? "尚未形成管理摘要。" : "正在执行章节分析，页面会自动更新真实进度。")}
                    </p>
                  </div>
                  <div className="flex min-w-28 flex-col items-end justify-between gap-3">
                    <span className="font-mono text-[11px] text-faint">{report.periodStart?.slice(0, 10)}</span>
                    <span className="inline-flex items-center gap-1 text-xs font-medium text-primary opacity-0 transition-opacity group-hover:opacity-100">
                      打开报告 <Icon name="arrow" size={13} />
                    </span>
                  </div>
                </button>
              ))}
            </div>
          ) : (
            <EmptyState title="这里还没有报告" description="创建第一份周期报告，或调整当前筛选条件。" />
          )}
        </Card>

        <Card className="self-start overflow-hidden">
          <div className="border-b border-line px-4 py-3.5">
            <span className="text-[11px] font-semibold text-primary">TEMPLATE COVERAGE</span>
            <h3 className="mt-1 text-base font-semibold tracking-tight text-ink">当前数据源可生成什么</h3>
          </div>
          <div className="divide-y divide-line">
            {templates.map((template) => (
              <button
                key={template.id}
                type="button"
                onClick={() => setCreateOpen(template.id)}
                className="group flex w-full cursor-pointer items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-surface-2"
              >
                <span className={cn(
                  "grid h-8 w-8 shrink-0 place-items-center rounded-control",
                  template.coverage?.ready ? "bg-teal-soft text-teal" : "bg-surface-3 text-faint",
                )}>
                  <Icon name="report" size={15} />
                </span>
                <span className="min-w-0 flex-1">
                  <strong className="block truncate text-xs font-semibold text-ink">{template.name}</strong>
                  <span className="mt-0.5 block truncate text-[11px] text-muted">{template.audience}</span>
                </span>
                <span className={cn("font-mono text-xs font-semibold", template.coverage?.ready ? "text-teal" : "text-amber")}>
                  {template.coverage?.score ?? 0}%
                </span>
              </button>
            ))}
          </div>
        </Card>
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
    </PageFrame>
  );
}

function ReportCreateWizard({ open, initialTemplateId, sourceId, sources, templates, onClose, onCreated }) {
  const range = useMemo(defaultRange, [open]);
  const [step, setStep] = useState(1);
  const [templateId, setTemplateId] = useState(initialTemplateId ?? "");
  const [selectedSourceId, setSelectedSourceId] = useState(sourceId ?? "");
  const [periodStart, setPeriodStart] = useState(range.start);
  const [periodEnd, setPeriodEnd] = useState(range.end);
  const [prompt, setPrompt] = useState("");
  const [title, setTitle] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!open) return;
    setStep(1);
    setTemplateId(initialTemplateId ?? templates.find((item) => item.coverage?.ready)?.id ?? templates[0]?.id ?? "");
    setSelectedSourceId(sourceId ?? "");
    setPeriodStart(range.start);
    setPeriodEnd(range.end);
    setPrompt("");
    setTitle("");
    setError(null);
  }, [initialTemplateId, open, range.end, range.start, sourceId, templates]);

  const template = templates.find((item) => item.id === templateId);
  const canContinue = step === 1
    ? Boolean(templateId && selectedSourceId && template?.coverage?.ready)
    : step === 2
      ? Boolean(periodStart && periodEnd && (templateId !== "tpl-topic-report" || prompt.trim()))
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
      });
      onCreated(created);
    } catch (nextError) {
      setError(nextError);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal open={open} onClose={submitting ? () => {} : onClose} label="创建报告" maxWidth={760}>
      <div className="flex items-start justify-between border-b border-line px-5 py-4">
        <div>
          <span className="text-[11px] font-semibold tracking-[0.08em] text-faint">CREATE REPORT</span>
          <h3 className="mt-1 text-base font-semibold text-ink">创建一份可复核报告</h3>
        </div>
        <button type="button" onClick={onClose} className="grid h-8 w-8 cursor-pointer place-items-center rounded-control text-muted hover:bg-surface-3">
          <Icon name="close" size={16} />
        </button>
      </div>
      <div className="grid border-b border-line sm:grid-cols-4">
        {["类型与数据源", "范围与问题", "确认大纲", "开始生成"].map((label, index) => (
          <div key={label} className={cn("flex items-center gap-2 px-4 py-3 text-xs", step === index + 1 ? "bg-primary-soft font-semibold text-primary" : "text-muted")}>
            <span className="font-mono text-[10px]">{index + 1}</span>{label}
          </div>
        ))}
      </div>
      <div className="min-h-80 p-5">
        {step === 1 && (
          <div className="grid gap-5 md:grid-cols-[220px_1fr]">
            <Field label="数据源">
              <select value={selectedSourceId} onChange={(event) => setSelectedSourceId(event.target.value)} className="input-control">
                {sources.map((source) => <option key={source.id} value={source.id}>{source.name}</option>)}
              </select>
            </Field>
            <div>
              <span className="text-xs font-semibold text-ink">报告类型</span>
              <div className="mt-2 grid gap-2 sm:grid-cols-2">
                {templates.map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    onClick={() => setTemplateId(item.id)}
                    className={cn(
                      "cursor-pointer rounded-card border p-3 text-left transition-colors",
                      templateId === item.id ? "border-primary/45 bg-primary-soft" : "border-line bg-surface hover:border-line-strong",
                    )}
                  >
                    <span className="flex items-center justify-between gap-2">
                      <strong className="text-xs text-ink">{item.name}</strong>
                      <span className={cn("font-mono text-[11px]", item.coverage?.ready ? "text-teal" : "text-amber")}>{item.coverage?.score}%</span>
                    </span>
                    <span className="mt-1 block text-[11px] leading-relaxed text-muted">{item.description}</span>
                  </button>
                ))}
              </div>
            </div>
          </div>
        )}
        {step === 2 && (
          <div className="mx-auto grid max-w-xl gap-4">
            <div className="grid grid-cols-2 gap-3">
              <Field label="周期开始"><input type="date" value={periodStart} onChange={(event) => setPeriodStart(event.target.value)} className="input-control" /></Field>
              <Field label="周期结束"><input type="date" value={periodEnd} onChange={(event) => setPeriodEnd(event.target.value)} className="input-control" /></Field>
            </div>
            {templateId === "tpl-topic-report" && (
              <Field label="专题问题">
                <textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} rows={4} placeholder="例如：分析 7 月工序良率下降的主要影响对象" className="input-control h-auto resize-none py-2.5" />
              </Field>
            )}
            <Field label="报告标题（可选）"><input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="留空将按周期和模板自动命名" className="input-control" /></Field>
            <div className="rounded-card border border-line bg-surface-2 p-3 text-xs leading-relaxed text-muted">
              默认按已选周期生成。周期不完整或比较基线缺失时，报告会降低可信度并显示限制说明，不会强行给出比较结论。
            </div>
          </div>
        )}
        {step === 3 && (
          <div className="mx-auto max-w-xl">
            <div className="flex items-center justify-between">
              <div>
                <span className="text-xs font-semibold text-ink">{template?.name}</span>
                <p className="mt-1 text-[11px] text-muted">{template?.audience} · {periodStart} 至 {periodEnd}</p>
              </div>
              <StatusPill tone={template?.coverage?.ready ? "green" : "amber"}>覆盖 {template?.coverage?.score}%</StatusPill>
            </div>
            <div className="mt-4 divide-y divide-line rounded-card border border-line">
              {template?.sections?.map((section, index) => (
                <div key={section.code} className="flex items-start gap-3 px-3.5 py-3">
                  <span className="grid h-6 w-6 shrink-0 place-items-center rounded-tag bg-surface-3 font-mono text-[10px] text-ink">{index + 1}</span>
                  <div>
                    <strong className="text-xs font-semibold text-ink">{section.title}</strong>
                    <p className="mt-0.5 text-[11px] leading-relaxed text-muted">{section.question?.replace("{prompt}", prompt) || prompt}</p>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
        {step === 4 && (
          <div className="mx-auto max-w-xl">
            <div className="rounded-panel border border-teal/25 bg-teal-soft p-4">
              <div className="flex items-center gap-2 text-teal"><Icon name="shield" size={17} /><strong className="text-sm">生成前检查已完成</strong></div>
              <p className="mt-2 text-xs leading-relaxed text-ink-2">
                将创建 {template?.sections?.length ?? 0} 个真实分析任务。SQL、结果集、口径和置信度会绑定到对应章节，完成后进入业务复核，不会自动发布。
              </p>
            </div>
            <dl className="mt-4 grid grid-cols-2 gap-x-6 gap-y-3 text-xs">
              <Summary label="数据源" value={sources.find((item) => item.id === Number(selectedSourceId))?.name} />
              <Summary label="报告类型" value={template?.name} />
              <Summary label="统计周期" value={`${periodStart} 至 ${periodEnd}`} />
              <Summary label="指标覆盖" value={`${template?.coverage?.score ?? 0}%`} />
            </dl>
          </div>
        )}
        {error && <div className="mt-4 rounded-control border border-red/25 bg-red-soft px-3 py-2 text-xs text-red">{error.message}</div>}
      </div>
      <div className="flex items-center justify-between border-t border-line bg-surface-2 px-5 py-3">
        <Button variant="ghost" onClick={step === 1 ? onClose : () => setStep((value) => value - 1)} disabled={submitting}>
          {step === 1 ? "取消" : "上一步"}
        </Button>
        {step < 4 ? (
          <Button variant="primary" onClick={() => setStep((value) => value + 1)} disabled={!canContinue}>继续 <Icon name="arrow" size={13} /></Button>
        ) : (
          <Button variant="primary" onClick={submit} disabled={submitting}>{submitting ? "正在创建…" : "开始生成"}</Button>
        )}
      </div>
    </Modal>
  );
}

function Metric({ label, value }) {
  return <div className="px-4 first:pl-0"><strong className="font-mono text-xl font-semibold text-ink">{value}</strong><span className="mt-0.5 block text-[11px] text-muted">{label}</span></div>;
}

function FilterTab({ active, onClick, children }) {
  return <button type="button" onClick={onClick} className={cn("h-7 cursor-pointer rounded-tag px-2.5 text-xs transition-colors", active ? "bg-surface font-semibold text-ink shadow-card" : "text-muted hover:text-ink")}>{children}</button>;
}

function Field({ label, children }) {
  return <label className="grid gap-1.5 text-xs font-semibold text-ink">{label}{children}</label>;
}

function Summary({ label, value }) {
  return <div className="border-b border-line pb-2"><dt className="text-[11px] text-faint">{label}</dt><dd className="mt-1 font-medium text-ink">{value}</dd></div>;
}
