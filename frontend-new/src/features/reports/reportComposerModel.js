export const DEFAULT_COMPOSITION = {
  schemaVersion: "report-composition/v1",
  name: "我的制造周报",
  description: "",
  blocks: [],
};

export const BLOCK_LIMIT = 24;

export const TOPIC_ORDER = ["生产", "质量", "设备", "库存", "其他"];
export const TOPIC_COLORS = {
  生产: "#285d72",
  质量: "#176b57",
  设备: "#8a5a12",
  库存: "#6557a8",
  其他: "#58636d",
};

export const KIND_LABELS = { kpi: "指标概览", trend: "趋势", breakdown: "维度分析" };
export const KIND_ICONS = { kpi: "overview", trend: "insight", breakdown: "chart" };

const TOPIC_KEYWORDS = [
  ["质量", ["良率", "不良", "缺陷", "返工", "抽检", "合格"]],
  ["设备", ["设备", "停机", "故障"]],
  ["库存", ["库存"]],
  ["生产", ["产量", "产出", "产线", "产中", "计划", "工序"]],
];

export function topicOf(metric) {
  const topic = metric?.topic;
  if (topic && topic !== "其他" && TOPIC_ORDER.includes(topic)) return topic;
  const name = metric?.name ?? "";
  for (const [topic2, words] of TOPIC_KEYWORDS) {
    if (words.some((word) => name.includes(word))) return topic2;
  }
  return "其他";
}

export function groupMetricsByTopic(metrics) {
  const groups = new Map(TOPIC_ORDER.map((topic) => [topic, []]));
  for (const metric of metrics ?? []) groups.get(topicOf(metric)).push(metric);
  return TOPIC_ORDER.filter((topic) => groups.get(topic).length).map((topic) => ({
    topic,
    color: TOPIC_COLORS[topic],
    metrics: groups.get(topic),
  }));
}

export function filterMetrics(metrics, query) {
  const keyword = (query ?? "").trim().toLowerCase();
  if (!keyword) return metrics ?? [];
  return (metrics ?? []).filter((metric) =>
    [metric.name, metric.definition, metric.topic, metric.unit, ...(metric.dimensions ?? [])]
      .filter(Boolean)
      .some((field) => String(field).toLowerCase().includes(keyword)),
  );
}

export function usageCounts(composition) {
  const counts = new Map();
  for (const block of composition.blocks ?? []) {
    if (!block.metricId) continue;
    counts.set(block.metricId, (counts.get(block.metricId) ?? 0) + 1);
  }
  return counts;
}

export function structureChecklist(composition, metricById) {
  const kinds = new Set((composition.blocks ?? []).map((block) => block.kind));
  const topics = new Set((composition.blocks ?? []).map((block) => topicOf(metricById[block.metricId])));
  return [
    { id: "kpi", label: "至少一个指标概览", done: kinds.has("kpi") },
    { id: "trend", label: "至少一个趋势分析", done: kinds.has("trend") },
    { id: "breakdown", label: "至少一个维度分析", done: kinds.has("breakdown") },
    { id: "topics", label: "覆盖两个及以上主题", done: topics.size >= 2 },
    { id: "size", label: `内容在 1 至 ${BLOCK_LIMIT} 项之间`, done: (composition.blocks?.length ?? 0) >= 1 },
  ];
}

export const CHART_OPTIONS = {
  kpi: [["number", "指标数字"]],
  trend: [["line", "折线图"], ["area", "面积图"], ["column", "柱形图"], ["calendar", "日历热力"]],
  breakdown: [["bar", "横向条形图"], ["donut", "环形图"], ["pareto", "帕累托图"], ["table", "数据条表格"]],
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
  if ((composition.blocks?.length ?? 0) >= BLOCK_LIMIT) return composition;
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
