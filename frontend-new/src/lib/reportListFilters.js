const SEVERITY_ORDER = { critical: 0, high: 1, medium: 2, low: 3 };

function audienceValues(report) {
  const audience = report.scope?.audience ?? {};
  return [
    ...(audience.organizationIds ?? []),
    ...(audience.businessRoles ?? []),
    ...(audience.positions ?? []),
    ...(audience.userIds ?? []),
  ].map(String);
}

export function reportListMeta(report) {
  const overview = report.reportOverview ?? {};
  const findings = overview.managementSummary?.findings ?? [];
  const anomalies = overview.anomalies ?? [];
  const highest = [...anomalies].sort(
    (left, right) =>
      (SEVERITY_ORDER[left.severity] ?? 9) -
      (SEVERITY_ORDER[right.severity] ?? 9),
  )[0];
  const scope = audienceValues(report);
  return {
    headline:
      findings.find((item) => item.level === "confirmed")?.text ??
      findings[0]?.text ??
      report.summary ??
      "尚未形成管理摘要。",
    highestSeverity: highest?.severity ?? "none",
    anomalyCount: anomalies.length,
    coverage: overview.dataQuality?.coverage ?? null,
    scopeLabel: scope.length ? scope.join("、") : "个人范围",
  };
}

export function filterReportRows(reports = [], filters = {}) {
  const keyword = filters.keyword?.trim().toLocaleLowerCase("zh-CN") ?? "";
  return reports.filter((report) => {
    const meta = reportListMeta(report);
    const searchText =
      `${report.title ?? ""} ${report.templateName ?? ""} ${report.sourceName ?? ""} ${meta.headline}`.toLocaleLowerCase(
        "zh-CN",
      );
    if (keyword && !searchText.includes(keyword)) return false;
    if (filters.reportType && report.reportType !== filters.reportType)
      return false;
    if (filters.scope && !audienceValues(report).includes(filters.scope))
      return false;
    if (filters.severity && meta.highestSeverity !== filters.severity)
      return false;
    if (filters.read === "read" && report.isRead !== true) return false;
    if (filters.read === "unread" && report.isRead === true) return false;
    if (
      filters.periodFrom &&
      (report.periodEnd ?? "").slice(0, 10) < filters.periodFrom
    )
      return false;
    if (
      filters.periodTo &&
      (report.periodStart ?? "").slice(0, 10) > filters.periodTo
    )
      return false;
    return true;
  });
}

export function reportScopeOptions(reports = []) {
  return [...new Set(reports.flatMap(audienceValues))].sort((left, right) =>
    left.localeCompare(right, "zh-CN"),
  );
}
