import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import {
  approveReport,
  downloadReportFile,
  getReport,
  publishReport,
  submitReportReview,
} from "../../api/reports";
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

const TERMINAL = new Set([
  "awaiting_review",
  "review_pending",
  "approved",
  "published",
  "partial",
  "failed",
  "validation_failed",
]);
const SEVERITY = {
  critical: ["严重", "red"],
  high: ["高", "red"],
  medium: ["关注", "amber"],
  low: ["低", "gray"],
};
const PROGRESS = {
  completed: ["已完成", "green"],
  on_track: ["按计划", "green"],
  at_risk: ["有风险", "amber"],
  blocked: ["受阻", "red"],
};

function date(value) {
  return value?.slice(0, 10) ?? "—";
}

export default function ReportDetailPage() {
  const { reportId } = useParams();
  const navigate = useNavigate();
  const [report, setReport] = useState(null);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState("");
  const [downloading, setDownloading] = useState("");
  const [action, setAction] = useState("");
  const load = async () => {
    setError(null);
    try {
      setReport(await getReport(reportId));
    } catch (nextError) {
      setError(nextError);
    }
  };
  useEffect(() => {
    load();
  }, [reportId]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!report || TERMINAL.has(report.status)) return undefined;
    const timer = window.setInterval(load, 1800);
    return () => window.clearInterval(timer);
  }, [report?.status]); // eslint-disable-line react-hooks/exhaustive-deps
  const view = useMemo(() => buildReportView(report), [report]);
  const reportTitle = reportText(report?.title) || "本期经营周报";
  if (error)
    return (
      <PageFrame>
        <ErrorNotice
          title="周报加载失败"
          detail={error.message}
          onRetry={load}
        />
      </PageFrame>
    );
  if (!report) return <RouteSkeleton />;
  const files = Object.fromEntries(
    (report.files ?? []).map((file) => [file.format, file]),
  );
  const download = async (format) => {
    setDownloading(format);
    setNotice("");
    try {
      await downloadReportFile(
        files[format],
        `${report.title}-V${report.version?.version ?? 1}`,
      );
    } catch (nextError) {
      setNotice(nextError.message);
    } finally {
      setDownloading("");
    }
  };
  const transition = async (key, operation) => {
    setAction(key);
    setNotice("");
    try {
      setReport(await operation());
      setNotice("报告状态已更新。");
    } catch (nextError) {
      setNotice(nextError.message);
    } finally {
      setAction("");
    }
  };
  if (!view.structured) {
    return (
      <LegacyReportDetail
        report={report}
        files={files}
        downloading={downloading}
        action={action}
        notice={notice}
        onBack={() => navigate("/workspace/reports")}
        onDownload={download}
        onTransition={transition}
      />
    );
  }
  return (
    <PageFrame className="report-editorial max-w-[1440px] gap-0 px-2 pb-16 sm:px-5 xl:px-7">
      <article className="overflow-hidden rounded-section border border-line bg-paper shadow-[0_18px_65px_rgb(24_35_41_/8%)]">
        <header className="report-cover border-b border-line px-5 py-6 sm:px-8 sm:py-8 xl:px-10">
          <div className="flex flex-wrap items-center justify-between gap-4">
            <button
              type="button"
              onClick={() => navigate("/workspace/reports")}
              className="inline-flex min-h-11 items-center gap-2 rounded-control text-sm font-semibold text-muted hover:text-ink"
            >
              <Icon name="chevron" size={15} className="rotate-180" />
              返回周报中心
            </button>
            <div className="flex flex-wrap gap-2">
              <DownloadButton
                format="pdf"
                file={files.pdf}
                busy={downloading}
                onClick={download}
              />
              <DownloadButton
                format="xlsx"
                file={files.xlsx}
                busy={downloading}
                onClick={download}
              />
            </div>
          </div>
          {notice && (
            <p
              className="mt-3 rounded-control border border-warning/25 bg-warning-soft p-3 text-sm text-warning"
              role="alert"
            >
              {notice}
            </p>
          )}
          <div className="mt-7 grid gap-6 xl:grid-cols-[minmax(0,1fr)_300px]">
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <StatusPill
                  tone={
                    report.status === "published"
                      ? "green"
                      : report.status === "failed"
                        ? "red"
                        : "blue"
                  }
                >
                  {report.statusLabel}
                </StatusPill>
                <span className="section-kicker">
                  V{report.version?.version ?? 1} · {report.sourceName}
                </span>
              </div>
              <h1 className="mt-4 max-w-4xl font-report text-[31px] font-semibold leading-[1.2] tracking-tight text-ink">
                {reportTitle}
              </h1>
              <p className="mt-3 text-sm text-muted">
                报告周期 {date(report.periodStart)} — {date(report.periodEnd)} ·
                生成于 {date(report.version?.generatedAt)}
              </p>
              <div className="mt-7 max-w-4xl border-l-[3px] border-primary pl-5">
                <span className="text-[13px] font-semibold text-primary">
                  本周一句话结论
                </span>
                <p className="mt-2 font-report text-[25px] font-semibold leading-[1.5] text-ink">
                  {view.headline}
                </p>
              </div>
              <KeyFindings findings={view.findings} />
            </div>
            <TopAnomaly anomaly={view.topAnomaly} />
          </div>
        </header>

        {!TERMINAL.has(report.status) && (
          <div className="border-b border-line bg-primary-soft px-6 py-4 text-sm text-primary">
            <strong>{report.statusLabel}</strong> · Agent
            正在生成和校验本周期内容，页面会自动更新。
          </div>
        )}

        <section
          aria-labelledby="key-metrics"
          className="border-b border-line px-5 py-6 sm:px-8 xl:px-10"
        >
          <SectionTitle
            id="key-metrics"
            eyebrow="关键指标"
            title="一眼看清本周变化"
            description="本期数值、上期基准与变化方向保持在同一阅读单元。"
          />
          {view.metrics.length ? (
            <div className="mt-5 grid gap-px overflow-hidden rounded-section border border-line bg-line sm:grid-cols-2 xl:grid-cols-6">
              {view.metrics.slice(0, 6).map((metric) => (
                <MetricCell key={metric.key} metric={metric} />
              ))}
            </div>
          ) : (
            <EmptyState
              title="指标仍在生成"
              description="结构化指标通过发布校验后会出现在这里。"
            />
          )}
        </section>

        <section className="border-b border-line bg-surface-2/25 px-5 py-7 sm:px-8 xl:px-10">
          <SectionTitle
            eyebrow="Agent 图表编排"
            title="让数据用适合它的方式说话"
            description={`${date(report.periodStart)} 至 ${date(report.periodEnd)} · Agent 按时间、类别和业务语义选择图形，每张图保持单一口径。`}
          />
          {view.charts.length ? (
            <>
              <div className="mt-6">
                <WeeklyReportCharts
                  charts={view.charts.slice(0, 1)}
                  sourceName={view.appendix.sources.join("、") || report.sourceName}
                />
              </div>
              {view.charts.length > 1 && (
                <div className="mt-5">
                  <WeeklyReportCharts
                    charts={view.charts.slice(1)}
                    sourceName={view.appendix.sources.join("、") || report.sourceName}
                  />
                </div>
              )}
            </>
          ) : (
            <div className="mt-5">
              <EmptyState
                title="图表正在编排"
                description="当专题至少包含两个可比较数据点时，Agent 会自动选择合适图形。"
              />
            </div>
          )}
        </section>

        <section className="border-b border-line px-5 py-7 sm:px-8 xl:px-10">
          <SectionTitle
            eyebrow="异常与行动"
            title="按照影响优先级处理"
            description="严重异常在前；Agent 建议明确标注，不替代业务责任人决策。"
          />
          <div className="mt-6 grid gap-6 xl:grid-cols-[minmax(0,1.15fr)_minmax(320px,0.85fr)]">
            <AnomalyTable anomalies={view.anomalies} />
            <RecommendationList
              recommendations={view.recommendations}
              actions={view.actions}
            />
          </div>
        </section>

        {view.progress.length > 0 && (
          <section className="border-b border-line px-5 py-7 sm:px-8 xl:px-10">
            <SectionTitle eyebrow="事项进展" title="重点事项进展" />
            <div className="mt-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
              {view.progress.map((item) => {
                const [label, tone] = PROGRESS[item.status] ?? [
                  item.status,
                  "gray",
                ];
                return (
                  <Card key={item.title} className="p-4">
                    <div className="flex items-center justify-between gap-3">
                      <h3 className="text-base font-semibold">{item.title}</h3>
                      <StatusPill tone={tone}>{label}</StatusPill>
                    </div>
                    <p className="mt-2 text-sm leading-6 text-muted">
                      {item.note || "没有补充说明"}
                    </p>
                    {item.completionRate != null && (
                      <div className="mt-4">
                        <div className="h-1.5 overflow-hidden rounded-full bg-surface-muted">
                          <span
                            className="block h-full bg-primary"
                            style={{ width: `${item.completionRate}%` }}
                          />
                        </div>
                        <span className="mt-1 block text-right font-mono text-[12px] text-muted">
                          {item.completionRate}%
                        </span>
                      </div>
                    )}
                  </Card>
                );
              })}
            </div>
          </section>
        )}

        <section className="px-5 py-7 sm:px-8 xl:px-10">
          <details className="evidence-disclosure rounded-section border border-line bg-surface-muted">
            <summary className="flex min-h-14 cursor-pointer list-none items-center gap-3 px-4 py-3 font-semibold text-ink">
              <Icon name="shield" size={17} className="text-primary" />
              <span>口径、数据质量与可追溯证据</span>
              <span className="ml-auto text-[13px] font-normal text-muted">
                覆盖率 {view.quality.coverage ?? 0}% ·{" "}
                {view.appendix.sources.length} 个数据来源
              </span>
              <Icon
                name="chevron"
                size={15}
                className="evidence-chevron rotate-90"
              />
            </summary>
            <EvidenceContent view={view} report={report} />
          </details>
        </section>
      </article>
    </PageFrame>
  );
}

function LegacyReportDetail({
  report,
  files,
  downloading,
  action,
  notice,
  onBack,
  onDownload,
  onTransition,
}) {
  const completeCount =
    report.sections?.filter((item) => item.status === "succeeded").length ?? 0;
  return (
    <PageFrame className="max-w-[1280px] gap-4">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex min-w-0 items-start gap-3">
          <button
            type="button"
            aria-label="返回报告列表"
            onClick={onBack}
            className="grid min-h-11 min-w-11 place-items-center rounded-control border border-line text-muted hover:text-ink"
          >
            <Icon name="chevron" size={15} className="rotate-180" />
          </button>
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <StatusPill
                tone={
                  ["failed", "validation_failed"].includes(report.status)
                    ? "red"
                    : "blue"
                }
              >
                {report.statusLabel}
              </StatusPill>
              <span className="section-kicker">
                {report.reportType === "weekly" ? "周报" : "专题报告"} · V
                {report.version?.version ?? 1}
              </span>
            </div>
            <h1 className="mt-2 font-report text-[25px] font-semibold text-ink">
              {reportText(report.title) || "报告"}
            </h1>
            <p className="mt-1 text-sm text-muted">
              {report.sourceName} · {date(report.periodStart)} —{" "}
              {date(report.periodEnd)}
            </p>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          {files.pdf && (
              <DownloadButton
              format="pdf"
              file={files.pdf}
              busy={downloading}
              onClick={onDownload}
            />
          )}
          {report.status === "awaiting_review" && (
            <Button
              variant="primary"
              disabled={Boolean(action)}
              onClick={() =>
                onTransition("review", () => submitReportReview(report.id))
              }
            >
              提交复核
            </Button>
          )}
          {report.status === "review_pending" && (
            <Button
              variant="primary"
              disabled={Boolean(action)}
              onClick={() =>
                onTransition("approve", () => approveReport(report.id))
              }
            >
              通过复核
            </Button>
          )}
          {report.status === "approved" && (
            <Button
              variant="primary"
              disabled={Boolean(action)}
              onClick={() =>
                onTransition("publish", () => publishReport(report.id))
              }
            >
              发布专题报告
            </Button>
          )}
        </div>
      </header>
      {notice && (
        <p
          className="rounded-control border border-line bg-surface p-3 text-sm"
          role="status"
        >
          {notice}
        </p>
      )}
      {report.status === "validation_failed" && (
        <ErrorNotice
          title="周报校验未通过，未自动发布"
          detail={
            report.error?.message ??
            "关键指标、证据或比较周期不满足发布规则，请修正数据后重新生成。"
          }
          onRetry={onBack}
        />
      )}
      {!TERMINAL.has(report.status) && (
        <Card className="p-4 text-sm text-primary">
          Agent 正在生成 · {completeCount}/{report.sections?.length ?? 0}{" "}
          章节完成
        </Card>
      )}
      <Card className="overflow-hidden">
        <section className="border-b border-line px-5 py-5 sm:px-7">
          <span className="section-kicker">MANAGEMENT SUMMARY</span>
          <h2 className="mt-1 font-report text-xl font-semibold">管理摘要</h2>
          <p className="mt-3 whitespace-pre-wrap text-base leading-8 text-ink-2">
            {reportText(report.summary) || "Agent 完成分析后会在这里形成摘要。"}
          </p>
        </section>
        <div className="divide-y divide-line">
          {report.sections?.length ? (
            report.sections.map((section) => (
              <LegacySection key={section.id} section={section} />
            ))
          ) : (
            <EmptyState
              title="暂无章节"
              description="Agent 生成大纲后会显示专题章节。"
            />
          )}
        </div>
      </Card>
    </PageFrame>
  );
}

function LegacySection({ section }) {
  const result = section.content?.result;
  return (
    <section className="px-5 py-5 sm:px-7">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <span className="font-mono text-[11px] text-muted">
            SECTION {String(section.order ?? 0).padStart(2, "0")}
          </span>
          <h2 className="mt-1 text-lg font-semibold">{reportText(section.title) || "未命名章节"}</h2>
        </div>
        {section.confidence > 0 && (
          <StatusPill tone={section.confidence >= 80 ? "green" : "amber"}>
            可信度 {section.confidence}%
          </StatusPill>
        )}
      </div>
      <p className="mt-3 whitespace-pre-wrap text-sm leading-7 text-ink-2">
        {reportText(section.summary) || reportText(section.statusLabel) || "正在生成"}
      </p>
      {result?.columns?.length > 0 && (
        <div className="mt-4 overflow-x-auto rounded-control border border-line">
          <table className="data-table min-w-[560px]">
            <thead>
              <tr>
                {result.columns.map((column) => (
                  <th key={column}>{column}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {(result.rows ?? []).slice(0, 20).map((row, rowIndex) => (
                <tr key={rowIndex}>
                  {row.map((value, cellIndex) => (
                    <td key={cellIndex}>{String(value ?? "—")}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <details className="mt-4 rounded-control bg-surface-muted px-3 py-2 text-sm">
        <summary className="cursor-pointer font-semibold text-primary">
          查看可追溯证据（{section.artifactIds?.length ?? 0}）
        </summary>
        <p className="mt-2 break-all font-mono text-[12px] leading-6 text-muted">
          {section.artifactIds?.join(" · ") || "当前章节暂无证据资产"}
        </p>
      </details>
    </section>
  );
}

function DownloadButton({ format, file, busy, onClick }) {
  const ready = file?.status === "ready";
  return (
    <Button
      onClick={() => onClick(format)}
      disabled={!ready || Boolean(busy)}
      title={
        !file
          ? "文件任务尚未创建"
          : !ready
            ? `文件状态：${file.status}`
            : `下载 ${format.toUpperCase()}`
      }
    >
      <Icon name="download" size={14} />
      {busy === format
        ? "正在下载…"
        : `下载 ${format === "xlsx" ? "Excel" : "PDF"}`}
      {file && !ready ? ` · ${file.status}` : ""}
    </Button>
  );
}
function SectionTitle({ id, eyebrow, title, description }) {
  return (
    <header>
      <span className="section-kicker">{eyebrow}</span>
      <h2
        id={id}
        className="mt-1 font-report text-[25px] font-semibold leading-tight text-ink"
      >
        {title}
      </h2>
      {description && (
        <p className="mt-2 text-sm leading-6 text-muted">{description}</p>
      )}
    </header>
  );
}
function TopAnomaly({ anomaly }) {
  if (!anomaly)
    return (
      <aside className="rounded-section border border-success/25 border-l-4 bg-success-soft p-5">
        <span className="text-[13px] font-semibold text-success">最高风险</span>
        <h2 className="mt-2 text-xl font-semibold text-ink">
          本周未识别到结构化异常
        </h2>
        <p className="mt-2 text-sm leading-6 text-muted">
          仍建议结合数据质量和完整证据阅读。
        </p>
      </aside>
    );
  const [label] = SEVERITY[anomaly.severity] ?? [anomaly.severity];
  return (
    <aside className="rounded-section border border-critical/25 border-l-4 bg-critical-soft p-5">
      <span className="text-[13px] font-semibold text-critical">
        最高风险 · {label}
      </span>
      <h2 className="mt-2 text-xl font-semibold leading-snug text-ink">
        {anomaly.title}
      </h2>
      <p className="mt-2 text-sm leading-6 text-ink-2">{anomaly.description}</p>
      <p className="mt-4 font-mono text-[12px] text-muted">
        可信度 {anomaly.confidence}% ·{" "}
        {anomaly.evidenceStatus === "verified"
          ? "证据已验证"
          : "需结合证据核查"}
      </p>
    </aside>
  );
}
function KeyFindings({ findings = [] }) {
  const confirmed = findings.filter((item) => item?.text && (item.level === "confirmed" || item.evidenceStatus === "verified"));
  const items = (confirmed.length ? confirmed : findings.filter((item) => item?.text)).slice(0, 3);
  if (!items.length) return null;
  return (
    <div className="mt-6 border-t border-line pt-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <span className="section-kicker">关键发现</span>
        <span className="text-[11px] text-faint">优先展示有证据支撑的结论</span>
      </div>
      <ul className="mt-3 grid gap-2 sm:grid-cols-2" aria-label="本周关键发现">
        {items.map((item, index) => (
          <li key={`${item.text}-${index}`} className="flex min-w-0 items-start gap-2.5 rounded-control border border-line bg-surface/75 px-3 py-2.5 text-sm leading-6 text-ink-2">
            <span className="shrink-0 font-mono text-[11px] font-semibold text-primary">{String(index + 1).padStart(2, "0")}</span>
            <span className="min-w-0">{item.text}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
function MetricCell({ metric }) {
  return (
    <div className="bg-paper p-4">
      <span className="text-[13px] font-semibold text-muted">
        {metric.name}
      </span>
      <strong className="mt-2 block font-mono text-[25px] font-semibold tracking-tight text-ink">
        {metric.valueLabel}
      </strong>
      <div className="mt-3 flex items-center justify-between gap-2 text-[12px]">
        <span className="text-muted">{metric.previousLabel}</span>
        <span className={cn("font-mono font-semibold", metric.trend === "down" ? "text-critical" : metric.trend === "up" ? "text-success" : "text-muted")}>
          {metric.trend === "up" ? "↑ " : metric.trend === "down" ? "↓ " : ""}
          {metric.changeLabel}
        </span>
      </div>
      <div className="mt-3 h-1 overflow-hidden rounded-full bg-surface-muted" aria-hidden="true">
        <span
          className={cn("block h-full rounded-full", metric.trend === "down" ? "bg-critical" : metric.trend === "up" ? "bg-success" : "bg-primary")}
          style={{ width: `${Math.max(12, Math.min(100, Math.abs(Number(metric.changeRate ?? 0)) * 100 + 18))}%` }}
        />
      </div>
    </div>
  );
}
function AnomalyTable({ anomalies }) {
  if (!anomalies.length)
    return (
      <EmptyState
        title="本周没有结构化异常"
        description="异常识别结果通过校验后会按严重程度排列。"
      />
    );
  return (
    <div className="overflow-x-auto rounded-section border border-line">
      <table className="data-table min-w-[680px]">
        <thead>
          <tr>
            <th>级别</th>
            <th>异常</th>
            <th>影响说明</th>
            <th className="text-right">可信度</th>
            <th>证据</th>
          </tr>
        </thead>
        <tbody>
          {anomalies.map((item) => {
            const [label, tone] = SEVERITY[item.severity] ?? [
              item.severity,
              "gray",
            ];
            return (
              <tr key={`${item.title}-${item.description}`}>
                <td>
                  <StatusPill tone={tone}>{label}</StatusPill>
                </td>
                <td className="font-semibold text-ink">{item.title}</td>
                <td className="max-w-md whitespace-normal leading-6">
                  {item.description}
                </td>
                <td className="text-right font-mono">{item.confidence}%</td>
                <td>
                  {item.evidenceStatus === "verified"
                    ? "已验证"
                    : item.evidenceStatus === "inferred"
                      ? "推断"
                      : "缺失"}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
function RecommendationList({ recommendations, actions }) {
  const items = recommendations.length
    ? recommendations.map((item) => ({
        title: item.title,
        body: item.rationale,
        priority: item.priority,
        evidenceStatus: item.evidenceStatus,
      }))
    : actions.map((item) => ({
        title: item.text,
        body: item.owner ? `责任人：${item.owner}` : "待分配责任人",
        priority: item.priority,
        evidenceStatus: item.evidenceStatus,
      }));
  return (
    <aside>
      <span className="section-kicker">AGENT 建议</span>
      <div className="mt-3 grid gap-3">
        {items.length ? (
          items.map((item, index) => (
            <div
              key={`${item.title}-${index}`}
              className="border-l-2 border-primary bg-primary-soft/60 p-4"
            >
              <div className="flex items-start gap-3">
                <span className="font-mono text-[12px] font-semibold text-primary">
                  {String(index + 1).padStart(2, "0")}
                </span>
                <div>
                  <h3 className="text-base font-semibold leading-snug text-ink">
                    {item.title}
                  </h3>
                  <p className="mt-2 text-sm leading-6 text-muted">
                    {item.body}
                  </p>
                  <p className="mt-3 text-[12px] text-muted">
                    优先级 {item.priority} ·{" "}
                    {item.evidenceStatus === "verified"
                      ? "证据已验证"
                      : "请结合证据核查"}
                  </p>
                </div>
              </div>
            </div>
          ))
        ) : (
          <EmptyState
            title="本周没有行动建议"
            description="Agent 尚未生成可执行建议。"
          />
        )}
      </div>
    </aside>
  );
}
function EvidenceContent({ view, report }) {
  return (
    <div className="grid gap-px border-t border-line bg-line md:grid-cols-2">
      <EvidenceBlock title="数据质量">
        <p>
          覆盖率 <strong>{view.quality.coverage ?? 0}%</strong>
        </p>
        <p>延迟来源：{view.quality.delayedSources?.join("、") || "无"}</p>
        <p>缺失指标：{view.quality.missingMetrics?.join("、") || "无"}</p>
        <p>失败章节：{view.quality.failedSections?.join("、") || "无"}</p>
      </EvidenceBlock>
      <EvidenceBlock title="指标口径">
        {view.appendix.definitions?.length ? (
          view.appendix.definitions.map((item) => (
            <p key={item.name}>
              <strong>{item.name}</strong>：{item.definition}
            </p>
          ))
        ) : (
          <p>本报告没有额外口径说明。</p>
        )}
      </EvidenceBlock>
      <EvidenceBlock title="数据范围">
        <p>
          报告 ID：<span className="font-mono">{report.id}</span>
        </p>
        <p>
          版本 ID：<span className="font-mono">{report.version?.id}</span>
        </p>
        <p>来源：{view.appendix.sources?.join("、") || report.sourceName}</p>
        <p>
          敏感字段：
          {view.audience.sensitiveFieldsRedacted === false
            ? "未标记脱敏"
            : "已按范围处理"}
        </p>
      </EvidenceBlock>
      <EvidenceBlock title="不可变产物">
        <p>
          {report.sections?.reduce(
            (sum, item) => sum + (item.artifactIds?.length ?? 0),
            0,
          ) ?? 0}{" "}
          个 Artifact 绑定到本版本。
        </p>
        {report.analysisSessionId && (
          <p>需要复核 SQL 与执行过程时，可进入完整分析证据链。</p>
        )}
      </EvidenceBlock>
    </div>
  );
}
function EvidenceBlock({ title, children }) {
  return (
    <section className="grid content-start gap-2 bg-paper p-5 text-sm leading-6 text-muted">
      <h3 className="mb-1 text-base font-semibold text-ink">{title}</h3>
      {children}
    </section>
  );
}
