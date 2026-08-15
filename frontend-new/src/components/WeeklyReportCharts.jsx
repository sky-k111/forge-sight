import {
  ComboMetricChart,
  HorizontalRankChart,
  MatrixHeatmapChart,
  ShareDonutChart,
  TrendLineChart,
} from "./charts";

const TYPE_LABELS = {
  line: "趋势折线",
  area: "趋势面积",
  bar: "横向排行",
  pareto: "帕累托分析",
  donut: "构成占比",
  heatmap: "矩阵热力",
};
const ROLE_COLORS = {
  production: "var(--color-primary)",
  quality: "var(--color-amber)",
  equipment: "var(--color-violet)",
  inventory: "var(--color-teal)",
  neutral: "var(--color-muted)",
};

export function WeeklyReportCharts({ charts = [], sourceName = "业务数据源" }) {
  if (!charts.length) return null;
  return (
    <div className={charts.length > 1 ? "grid gap-5 xl:grid-cols-2" : "grid gap-5"}>
      {charts.map((chart) => (
        <ChartArticle key={chart.key} chart={chart} sourceName={sourceName} />
      ))}
    </div>
  );
}

function ChartArticle({ chart, sourceName }) {
  const evidenceCount = chart.evidenceArtifactIds?.length ?? 0;
  return (
    <article className="min-w-0 rounded-section border border-line bg-paper p-4 shadow-[0_12px_34px_rgb(24_35_41_/5%)] sm:p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="section-kicker">{TYPE_LABELS[chart.type] ?? "数据图表"}</p>
          <h3 className="mt-1 font-report text-xl font-semibold leading-snug text-ink">{chart.title}</h3>
        </div>
        <span className="rounded-full border border-line bg-surface-2 px-2.5 py-1 font-mono text-xs text-muted">
          {chart.unit}
        </span>
      </div>
      <p className="mt-2 min-h-10 text-sm leading-6 text-muted">{chart.description}</p>
      <div className="mt-4" aria-label={`${chart.title}图表`}>
        <ChartVisual chart={chart} />
      </div>
      <div className="mt-3 flex flex-wrap items-center justify-between gap-2 border-t border-line pt-3 text-xs text-faint">
        <span>来源：{sourceName}</span>
        <span>{chart.legacy ? "兼容旧版数据" : `证据 ${evidenceCount} 项`}</span>
      </div>
      <ChartDataTable chart={chart} />
    </article>
  );
}

function ChartVisual({ chart }) {
  const color = ROLE_COLORS[chart.colorRole] ?? ROLE_COLORS.neutral;
  const data = chart.points ?? [];
  if (chart.type === "bar") {
    return <HorizontalRankChart data={data} unit={chart.unit} color={color} />;
  }
  if (chart.type === "pareto") {
    return (
      <ComboMetricChart
        data={data}
        xKey="label"
        bars={[{ key: "value", name: chart.valueLabel, color }]}
        lines={[{ key: "secondaryValue", name: "累计贡献率", color: "var(--color-red)" }]}
        leftUnit={chart.unit}
        rightUnit="%"
        height={280}
      />
    );
  }
  if (chart.type === "donut") {
    const total = data.reduce((sum, item) => sum + (Number(item.value) || 0), 0);
    return (
      <ShareDonutChart
        data={data.map((item, index) => ({ name: item.label, value: item.value, color: index === 0 ? color : undefined }))}
        unit={chart.unit}
        centerTitle="合计"
        centerValue={formatValue(total, chart.unit)}
        height={280}
      />
    );
  }
  if (chart.type === "heatmap") {
    const rows = [...new Set(data.map((item) => item.label))];
    const columns = [...new Set(data.map((item) => item.group).filter(Boolean))];
    return (
      <MatrixHeatmapChart
        rows={rows}
        columns={columns}
        cells={data.map((item) => ({ row: item.label, column: item.group, value: item.value }))}
        rowName={chart.categoryLabel}
        columnName="分组"
        valueName={`${chart.valueLabel}（${chart.unit}）`}
      />
    );
  }
  return (
    <TrendLineChart
      data={data}
      xKey="label"
      series={[{ key: "value", name: chart.valueLabel, color }]}
      unit={chart.unit}
      area={chart.type === "area"}
      height={280}
    />
  );
}

function ChartDataTable({ chart }) {
  return (
    <details className="mt-3">
      <summary className="flex min-h-11 cursor-pointer items-center py-2 text-xs font-semibold text-primary">查看精确数据</summary>
      <div className="mt-2 max-h-64 overflow-auto">
        <table className="data-table min-w-[420px]">
          <thead>
            <tr>
              <th>{chart.categoryLabel}</th>
              {chart.points?.some((item) => item.group) && <th>分组</th>}
              <th className="text-right">{chart.valueLabel}（{chart.unit}）</th>
              {chart.type === "pareto" && <th className="text-right">累计贡献率</th>}
            </tr>
          </thead>
          <tbody>
            {chart.points?.map((item, index) => (
              <tr key={`${item.label}-${item.group ?? index}`}>
                <td>{item.label}</td>
                {chart.points.some((point) => point.group) && <td>{item.group ?? "—"}</td>}
                <td className="text-right font-mono">{formatValue(item.value)}</td>
                {chart.type === "pareto" && <td className="text-right font-mono">{formatValue(item.secondaryValue)}%</td>}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}

function formatValue(value, unit = "") {
  const number = Number(value);
  const formatted = Number.isFinite(number)
    ? number.toLocaleString("zh-CN", { maximumFractionDigits: 2 })
    : "—";
  return `${formatted}${unit === "%" ? "%" : unit ? ` ${unit}` : ""}`;
}
