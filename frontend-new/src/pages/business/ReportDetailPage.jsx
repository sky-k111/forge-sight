import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import Icon from "../../components/ui/Icon";
import cn from "../../lib/cn";
import { conciseFindingTitle, parseAnalysisNarrative } from "../../lib/narrative";
import { Button, Card, EmptyState, ErrorNotice, PageFrame, RouteSkeleton, StatusPill } from "../../components/ui";
import {
  approveReport,
  getReport,
  getReportIntegrations,
  publishReport,
  pushReportToFeishu,
  reportExportUrl,
  submitReportReview,
} from "../../api/reports";

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

export default function ReportDetailPage() {
  const { reportId } = useParams();
  const navigate = useNavigate();
  const [report, setReport] = useState(null);
  const [integration, setIntegration] = useState(null);
  const [selectedSectionId, setSelectedSectionId] = useState(null);
  const [error, setError] = useState(null);
  const [action, setAction] = useState(null);
  const [notice, setNotice] = useState("");

  const load = async () => {
    setError(null);
    try {
      const [nextReport, nextIntegration] = await Promise.all([
        getReport(reportId),
        getReportIntegrations(),
      ]);
      setReport(nextReport);
      setIntegration(nextIntegration);
      setSelectedSectionId((current) => current ?? nextReport.sections?.[0]?.id ?? null);
    } catch (nextError) {
      setError(nextError);
    }
  };

  useEffect(() => {
    load();
  }, [reportId]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!report || TERMINAL.has(report.status)) return undefined;
    const timer = window.setInterval(async () => {
      try {
        setReport(await getReport(reportId));
      } catch {
        // 保留已加载内容，下一轮继续尝试。
      }
    }, 1500);
    return () => window.clearInterval(timer);
  }, [report, reportId]);

  const selected = useMemo(
    () => report?.sections?.find((item) => item.id === selectedSectionId) ?? report?.sections?.[0],
    [report, selectedSectionId],
  );

  const perform = async (key, operation) => {
    setAction(key);
    setNotice("");
    try {
      const next = await operation();
      setReport(next.id ? next : await getReport(reportId));
      setNotice(key === "feishu" ? "已推送到飞书群。" : "报告状态已更新。");
    } catch (nextError) {
      setNotice(nextError.message);
    } finally {
      setAction(null);
    }
  };

  if (error) {
    return <PageFrame><ErrorNotice title="报告加载失败" detail={error.message} onRetry={load} /></PageFrame>;
  }
  if (!report) return <RouteSkeleton />;

  const completeCount = report.sections?.filter((item) => item.status === "succeeded").length ?? 0;
  const totalCount = report.sections?.length ?? 0;

  return (
    <PageFrame className="max-w-none gap-4 xl:px-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex min-w-0 items-start gap-3">
          <button type="button" aria-label="返回报告列表" onClick={() => navigate("/workspace/reports")} className="mt-0.5 grid h-8 w-8 shrink-0 cursor-pointer place-items-center rounded-control border border-line bg-surface text-muted hover:text-ink">
            <Icon name="chevron" size={15} className="rotate-180" />
          </button>
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <StatusPill tone={statusTone[report.status] ?? "gray"}>{report.statusLabel}</StatusPill>
              <span className="text-[10px] text-faint">报告版本 V{report.version?.version}</span>
            </div>
            <h2 className="mt-2 truncate text-xl font-semibold tracking-tight text-ink">{report.title}</h2>
            <p className="mt-1 text-xs text-muted">
              {report.sourceName} · {report.periodStart?.slice(0, 10)} 至 {report.periodEnd?.slice(0, 10)}
            </p>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button onClick={() => window.open(reportExportUrl(report.id), "_blank", "noopener,noreferrer")} disabled={!TERMINAL.has(report.status)}>
            <Icon name="download" size={14} />导出 / 打印 PDF
          </Button>
          {report.analysisSessionId && (
            <Button onClick={() => {
              window.sessionStorage.setItem("forgesight:analysis-session-id", report.analysisSessionId);
              navigate("/workspace/analysis");
            }}>
              <Icon name="link" size={14} />查看完整证据链
            </Button>
          )}
          {report.status === "awaiting_review" && (
            <Button variant="primary" disabled={action} onClick={() => perform("review", () => submitReportReview(report.id))}>提交复核</Button>
          )}
          {report.status === "review_pending" && (
            <Button variant="primary" disabled={action} onClick={() => perform("approve", () => approveReport(report.id))}>通过复核</Button>
          )}
          {report.status === "approved" && (
            <Button variant="primary" disabled={action} onClick={() => perform("publish", () => publishReport(report.id))}>发布报告</Button>
          )}
          {report.status === "published" && (
            <Button
              variant="primary"
              disabled={action || !integration?.feishu?.configured}
              title={integration?.feishu?.configured ? "推送当前已发布版本" : "请先在后端配置飞书群机器人"}
              onClick={() => perform("feishu", () => pushReportToFeishu(report.id))}
            >
              <Icon name="send" size={14} />推送飞书
            </Button>
          )}
        </div>
      </div>

      {notice && <div className={cn("rounded-control border px-3 py-2 text-xs", notice.includes("已") ? "border-teal/25 bg-teal-soft text-teal" : "border-red/25 bg-red-soft text-red")}>{notice}</div>}

      {!TERMINAL.has(report.status) && (
        <Card className="overflow-hidden">
          <div className="flex items-center gap-4 px-4 py-3">
            <span className="grid h-8 w-8 place-items-center rounded-control bg-primary-soft text-primary"><Icon name="refresh" size={15} className="animate-spin" /></span>
            <div className="min-w-0 flex-1">
              <div className="flex items-center justify-between gap-3">
                <strong className="text-xs text-ink">{report.statusLabel}</strong>
                <span className="font-mono text-[11px] text-muted">{completeCount}/{totalCount} 章节</span>
              </div>
              <div className="mt-2 h-1 overflow-hidden rounded-full bg-surface-3">
                <span className="block h-full rounded-full bg-primary transition-[width] duration-500" style={{ width: `${totalCount ? completeCount / totalCount * 100 : 8}%` }} />
              </div>
            </div>
          </div>
        </Card>
      )}

      <div className="grid min-h-0 gap-4 xl:grid-cols-[220px_minmax(0,1fr)_320px]">
        <Card className="self-start overflow-hidden xl:sticky xl:top-4">
          <div className="border-b border-line px-4 py-3">
            <span className="text-[11px] font-semibold text-faint">章节目录</span>
            <p className="mt-1 text-xs font-medium text-ink">{completeCount}/{totalCount} 个章节完成</p>
          </div>
          <nav className="divide-y divide-line">
            {report.sections?.map((section) => (
              <button
                key={section.id}
                type="button"
                onClick={() => setSelectedSectionId(section.id)}
                className={cn(
                  "flex w-full cursor-pointer items-start gap-2.5 px-3.5 py-3 text-left transition-colors",
                  selected?.id === section.id ? "bg-primary-soft" : "hover:bg-surface-2",
                )}
              >
                <span className={cn(
                  "mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded-full border",
                  section.status === "succeeded" ? "border-teal/30 bg-teal-soft text-teal" : section.status === "failed" ? "border-red/30 bg-red-soft text-red" : "border-line bg-surface text-faint",
                )}>
                  {section.status === "succeeded" ? <Icon name="check" size={11} /> : section.order}
                </span>
                <span className="min-w-0">
                  <strong className="block text-xs font-semibold text-ink">{section.title}</strong>
                  <span className="mt-0.5 block text-[10px] text-muted">{section.statusLabel}</span>
                </span>
              </button>
            ))}
          </nav>
        </Card>

        <main className="min-w-0">
          <Card className="overflow-hidden">
            <header className="border-b border-line px-5 py-4">
              <span className="text-[11px] font-semibold tracking-[0.08em] text-primary">MANAGEMENT SUMMARY</span>
              <h3 className="mt-1 text-base font-semibold tracking-tight text-ink">管理摘要</h3>
              <NarrativeBlock
                value={report.summary}
                empty={TERMINAL.has(report.status) ? "本报告尚未形成管理摘要。" : "章节完成后将自动生成跨章节管理摘要。"}
                maxFindings={4}
                titles={report.sections?.map((section) => section.title)}
              />
            </header>
            {selected ? (
              <article>
                <div className="flex flex-wrap items-start justify-between gap-3 px-5 py-4">
                  <div>
                    <span className="font-mono text-[10px] text-faint">SECTION {String(selected.order).padStart(2, "0")}</span>
                    <h3 className="mt-1 text-lg font-semibold tracking-tight text-ink">{selected.title}</h3>
                  </div>
                  {selected.confidence > 0 && <StatusPill tone={selected.confidence >= 85 ? "green" : selected.confidence >= 65 ? "amber" : "red"}>可信度 {selected.confidence}%</StatusPill>}
                </div>
                {selected.summary ? (
                  <div className="border-t border-line px-5 py-4">
                    <h4 className="text-xs font-semibold text-ink">分析结论</h4>
                    <NarrativeBlock value={selected.summary} maxFindings={5} />
                  </div>
                ) : (
                  <div className="border-t border-line"><EmptyState title={selected.statusLabel} description="该章节完成后会在这里显示结论、图表和结果明细。" /></div>
                )}
                <ResultPreview result={selected.content?.result} />
              </article>
            ) : (
              <EmptyState title="暂无章节" description="Agent 生成大纲后会显示报告章节。" />
            )}
          </Card>
        </main>

        <aside className="self-start xl:sticky xl:top-4">
          <Card className="overflow-hidden">
            <div className="border-b border-line px-4 py-3">
              <span className="text-[11px] font-semibold tracking-[0.08em] text-faint">EVIDENCE</span>
              <h3 className="mt-1 text-sm font-semibold text-ink">当前章节依据</h3>
            </div>
            {selected ? <EvidencePanel section={selected} /> : <EmptyState title="选择一个章节" description="这里会展示口径、SQL、结果和可信度说明。" />}
          </Card>
        </aside>
      </div>
    </PageFrame>
  );
}

function NarrativeBlock({ value, empty = "暂无结论。", maxFindings = 3, titles = [] }) {
  const narrative = parseAnalysisNarrative(value ?? "", { sectionTitles: titles });
  const findings = narrative.highlights.slice(0, maxFindings);
  return (
    <div className="mt-2 text-body leading-7 text-ink-2">
      <p>{narrative.summary || empty}</p>
      {findings.length > 0 && (
        <ul className="mt-3 grid gap-2 border-l-2 border-primary/20 pl-3.5">
          {findings.map((finding, index) => (
            <li key={`${finding.title}-${finding.text}`} className="leading-relaxed">
              <strong className="font-semibold text-ink">{conciseFindingTitle(titles[index], finding.title)}：</strong>{finding.text}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function ResultPreview({ result }) {
  if (!result?.ok || !result?.columns?.length) return null;
  const rows = result.rows ?? [];
  const columns = result.columns;
  const numericIndex = columns.findIndex((_, index) => rows.some((row) => Number.isFinite(Number(row[index]))));
  const labelIndex = columns.findIndex((_, index) => index !== numericIndex);
  const chartRows = numericIndex >= 0
    ? rows.slice(0, 8).map((row) => ({ label: String(row[labelIndex >= 0 ? labelIndex : 0]), value: Number(row[numericIndex]) || 0 }))
    : [];
  const max = Math.max(...chartRows.map((item) => Math.abs(item.value)), 1);
  return (
    <div className="border-t border-line px-5 py-4">
      <div className="flex items-center justify-between">
        <h4 className="text-xs font-semibold text-ink">结果预览</h4>
        <span className="text-[11px] text-muted">{result.row_count ?? rows.length} 条记录</span>
      </div>
      {chartRows.length > 1 && (
        <div className="mt-3 grid gap-2 rounded-card border border-line bg-surface-2 p-3">
          {chartRows.map((item, index) => (
            <div key={`${item.label}-${index}`} className="grid grid-cols-[110px_minmax(0,1fr)_70px] items-center gap-2 text-[11px]">
              <span className="truncate text-muted">{item.label}</span>
              <span className="h-2 overflow-hidden rounded-full bg-surface-3"><i className="block h-full rounded-full bg-primary" style={{ width: `${Math.max(2, Math.abs(item.value) / max * 100)}%` }} /></span>
              <strong className="text-right font-mono font-medium text-ink">{item.value.toLocaleString()}</strong>
            </div>
          ))}
        </div>
      )}
      <div className="mt-3 overflow-auto rounded-card border border-line">
        <table className="w-full min-w-[520px] border-collapse text-left text-xs">
          <thead className="bg-surface-2 text-muted"><tr>{columns.map((column) => <th key={column} className="border-b border-line px-3 py-2 font-medium">{column}</th>)}</tr></thead>
          <tbody>{rows.slice(0, 10).map((row, index) => <tr key={index} className="border-b border-line last:border-b-0">{row.map((value, cell) => <td key={cell} className="px-3 py-2 text-ink-2">{String(value ?? "—")}</td>)}</tr>)}</tbody>
        </table>
      </div>
    </div>
  );
}

function EvidencePanel({ section }) {
  const content = section.content ?? {};
  const confidence = content.confidence ?? {};
  const metrics = content.context?.metrics ?? [];
  const sql = content.sql?.sql;
  return (
    <div className="divide-y divide-line">
      <EvidenceBlock title="口径与数据范围" icon="knowledge">
        {metrics.length ? metrics.slice(0, 4).map((metric) => (
          <div key={metric.id ?? metric.name} className="rounded-control bg-surface-2 p-2.5">
            <strong className="text-xs text-ink">{metric.name}</strong>
            <p className="mt-1 text-[11px] leading-relaxed text-muted">{metric.formula || metric.definition}</p>
          </div>
        )) : <p className="text-[11px] text-muted">当前章节未命中已发布指标，若使用临时推导口径会在结论中披露。</p>}
      </EvidenceBlock>
      <EvidenceBlock title="可信度说明" icon="shield">
        <div className="flex items-end justify-between">
          <strong className="font-mono text-2xl text-ink">{section.confidence || 0}%</strong>
          <span className="text-[11px] text-muted">{confidence.label || "待评估"}</span>
        </div>
        {confidence.reasons?.length ? <ul className="mt-2 grid gap-1 text-[11px] leading-relaxed text-muted">{confidence.reasons.map((reason) => <li key={reason}>· {reason}</li>)}</ul> : null}
      </EvidenceBlock>
      <EvidenceBlock title="只读 SQL" icon="code">
        {sql ? <pre className="max-h-52 overflow-auto whitespace-pre-wrap break-all rounded-control bg-ink p-3 font-mono text-[10px] leading-relaxed text-white">{sql}</pre> : <p className="text-[11px] text-muted">SQL 生成后会显示在这里。</p>}
      </EvidenceBlock>
      <EvidenceBlock title="证据资产" icon="link">
        <p className="text-[11px] leading-relaxed text-muted">{section.artifactIds?.length ?? 0} 个不可变产物已绑定到本章节。</p>
        {section.artifactIds?.length ? <p className="text-[10px] leading-relaxed text-faint">完整资产可在分析证据链中核验。</p> : null}
      </EvidenceBlock>
    </div>
  );
}

function EvidenceBlock({ title, icon, children }) {
  return <section className="p-4"><h4 className="flex items-center gap-1.5 text-xs font-semibold text-ink"><Icon name={icon} size={14} className="text-primary" />{title}</h4><div className="mt-3 grid gap-2">{children}</div></section>;
}
