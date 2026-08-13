const SEVERITY_ORDER = { critical: 0, high: 1, medium: 2, low: 3 };

export function mergeTrendSeries(series = []) {
  const rows = new Map();
  series.forEach((item) =>
    item.points?.forEach((point) => {
      const row = rows.get(point.label) ?? { label: point.label };
      row[item.metricKey] = point.value;
      rows.set(point.label, row);
    }),
  );
  return [...rows.values()];
}

export function buildReportView(report = {}) {
  report = report ?? {};
  const content =
    report.content?.schemaVersion === "weekly-report/v1" ? report.content : {};
  const findings = content.managementSummary?.findings ?? [];
  const metrics = (content.metrics ?? []).map((metric) => ({
    ...metric,
    valueLabel: formatMetric(metric.value, metric.unit),
    previousLabel:
      metric.previousValue == null
        ? "无上期值"
        : `上期 ${formatMetric(metric.previousValue, metric.unit)}`,
    changeLabel:
      metric.changeRate == null
        ? "—"
        : `${metric.changeRate > 0 ? "+" : ""}${(metric.changeRate * 100).toFixed(2)}%`,
  }));
  const anomalies = [...(content.anomalies ?? [])].sort(
    (a, b) =>
      (SEVERITY_ORDER[a.severity] ?? 9) - (SEVERITY_ORDER[b.severity] ?? 9),
  );
  const headline =
    findings.find((item) => item.level === "confirmed")?.text ??
    findings[0]?.text ??
    report.summary ??
    "本周期尚未形成总体结论。";
  return {
    structured: Boolean(content.schemaVersion),
    headline,
    findings,
    actions: content.managementSummary?.actions ?? [],
    metrics,
    trends: content.trends ?? [],
    trendRows: mergeTrendSeries(content.trends),
    anomalies,
    topAnomaly: anomalies[0] ?? null,
    progress: content.progress ?? [],
    recommendations: content.recommendations ?? [],
    quality: content.dataQuality ?? {
      coverage: 0,
      delayedSources: [],
      missingMetrics: [],
      temporaryMetrics: [],
      failedSections: [],
    },
    appendix: content.appendix ?? { sources: [], definitions: [] },
    audience: content.audience ?? {},
  };
}

export function formatMetric(value, unit = "") {
  const numeric = Number(value);
  const formatted = Number.isFinite(numeric)
    ? numeric.toLocaleString("zh-CN", { maximumFractionDigits: 2 })
    : "—";
  return `${formatted}${unit === "%" ? "%" : unit ? ` ${unit}` : ""}`;
}
