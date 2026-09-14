export const DEFAULT_COMPOSITION = {
  schemaVersion: "report-composition/v1",
  name: "我的制造周报",
  description: "",
  blocks: [],
};

export const CHART_OPTIONS = {
  kpi: [["number", "指标数字"]],
  trend: [["line", "折线图"], ["area", "面积图"], ["column", "柱形图"]],
  breakdown: [["bar", "横向条形图"], ["donut", "环形图"], ["pareto", "帕累托图"]],
};

const defaults = {
  kpi: { chart: "number", width: "half", height: "compact", compare: true },
  trend: { chart: "line", width: "full", height: "standard", compare: false },
  breakdown: { chart: "bar", width: "half", height: "standard", compare: false },
};

export function fromStarter(starter) {
  return {
    ...DEFAULT_COMPOSITION,
    name: starter.id === "starter-blank" ? "我的制造周报" : starter.name.replace("起始模板", "周报"),
    description: starter.description ?? "",
    blocks: (starter.blocks ?? []).map((block) => ({ ...block })),
  };
}

export function addMetricBlock(composition, metric, kind, dimension) {
  const base = defaults[kind];
  const block = {
    id: `block-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
    kind,
    title: kind === "kpi" ? metric.name : kind === "trend" ? `${metric.name}趋势` : `按${dimension}分析${metric.name}`,
    metricId: metric.id,
    dimension: kind === "breakdown" ? dimension : null,
    grain: "week",
    ...base,
    accent: "default",
    topN: 12,
  };
  return { ...composition, blocks: [...composition.blocks, block] };
}

export function updateBlock(composition, id, changes) {
  return {
    ...composition,
    blocks: composition.blocks.map((block) => block.id === id ? { ...block, ...changes } : block),
  };
}

export function removeBlock(composition, id) {
  return { ...composition, blocks: composition.blocks.filter((block) => block.id !== id) };
}

export function moveBlock(composition, from, to) {
  if (from === to || from < 0 || to < 0 || from >= composition.blocks.length || to >= composition.blocks.length) return composition;
  const blocks = [...composition.blocks];
  const [item] = blocks.splice(from, 1);
  blocks.splice(to, 0, item);
  return { ...composition, blocks };
}

export function previewSeries(item) {
  const columns = item.columns ?? [];
  const rows = item.rows ?? [];
  if (!rows.length) return [];
  const numericIndex = columns.length - 1;
  if (item.block.kind === "kpi") return [{ label: columns[numericIndex], value: Number(rows[0]?.[numericIndex]) }];
  return rows.map((row) => ({ label: String(row[0] ?? "—"), value: Number(row[numericIndex]) || 0 }));
}
