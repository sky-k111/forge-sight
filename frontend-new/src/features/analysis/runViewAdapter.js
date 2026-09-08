import { conciseFindingTitle, parseAnalysisNarrative } from "../../lib/narrative";
import { buildBusinessKpis, buildChart } from "./chartBuilder";

export { parseAnalysisNarrative } from "../../lib/narrative";

const STATUS_MAP = {
  waiting_user: "clarifying",
  modeling_proposal: "modeling-proposal",
};

const STEP_ARTIFACT = {
  "memory.retrieve": "memory-context",
  "skill.retrieve": "analysis-skill",
  "context.retrieve": "context",
  "query.generate": "sql",
  "query.validate": "validation",
  "query.execute": "result",
  "result.explain": "conclusion",
  "modeling.propose": "modeling-proposal",
};

function latestSteps(steps = [], tasks = []) {
  const taskOrders = new Map(tasks.map((task) => [task.id, task.order ?? 999]));
  const selected = new Map();
  for (const step of steps) {
    const key = `${step.taskId ?? "run"}:${step.stepKey}`;
    const previous = selected.get(key);
    if (!previous || step.attempt >= previous.attempt) selected.set(key, step);
  }
  return [...selected.values()].sort((a, b) => {
    const taskOrder = (taskOrders.get(a.taskId) ?? 0) - (taskOrders.get(b.taskId) ?? 0);
    return taskOrder || a.stepOrder - b.stepOrder;
  });
}

function artifactFor(artifacts, step) {
  const type = STEP_ARTIFACT[step.stepKey];
  if (!type) return null;
  return [...artifacts].reverse().find((item) => item.type === type && item.taskId === step.taskId) ?? null;
}

function detailFor(step, artifact) {
  const content = artifact?.content;
  const detail = {
    title: step.label,
    body: step.detail || (step.status === "succeeded" ? "该步骤已完成，产物已写入证据链。" : "该步骤正在处理。"),
    tags: [`状态：${step.status}`, `尝试：${step.attempt}`],
  };
  if (artifact?.type === "sql") {
    detail.body = "SQL 已依据当前数据源元数据和业务口径生成，执行前仍需通过只读安全校验。";
    detail.code = content?.sql;
  } else if (artifact?.type === "memory-context") {
    const memories = content?.memories ?? [];
    const applied = content?.applied ?? [];
    detail.body = content?.enabled
      ? memories.length
        ? `已在当前账号与数据源范围内召回 ${memories.length} 条相关记忆，其中 ${applied.length} 条偏好实际影响了本次展示。`
        : "记忆增强已开启，但当前问题没有命中可用记忆，已按基础链路继续。"
      : "本次运行属于记忆关闭对照组，没有召回或写入长期记忆。";
    detail.tags = memories.length
      ? memories.map((item) => `${item.title} · ${Math.round((item.relevance ?? item.confidence ?? 0) * 100)}%`).slice(0, 6)
      : [content?.enabled ? "实验组：on" : "基线组：off"];
  } else if (artifact?.type === "analysis-skill") {
    const skills = content?.skills ?? [];
    detail.body = content?.enabled
      ? skills.length > 0
        ? `已匹配 ${skills.length} 个经审核的分析技能；它们只提供分析步骤，不扩大数据权限。`
        : "技能增强已开启，但当前问题没有匹配技能，已按基础链路继续。"
      : "本次运行使用基线链路，未启用分析技能增强。";
    detail.tags = skills.length > 0
      ? skills.map((item) => `${item.name} · v${item.version}`)
      : [content?.enabled ? "实验组：on" : "基线组：off"];
  } else if (artifact?.type === "context") {
    detail.body = `命中 ${content?.coverage?.metricCount ?? 0} 个指标口径、${content?.coverage?.tableCount ?? 0} 张数据表。`;
    detail.tags = [
      ...(content?.metrics ?? []).map((item) => `指标：${item.name}`),
      ...(content?.tables ?? []).map((item) => item.name),
    ].slice(0, 8);
  } else if (artifact?.type === "validation") {
    const costGate = content?.costGate;
    detail.body = content?.ok
      ? costGate
        ? `SQL 安全校验已通过；数据库预计扫描 ${Number(costGate.estimated_rows ?? 0).toLocaleString()} 行${costGate.estimated_cost == null ? "" : `，优化器成本 ${Number(costGate.estimated_cost).toLocaleString()}`}。`
        : "SQL 只读性、资源白名单和语法检查已通过。"
      : (content?.reason || "SQL 校验未通过。");
    detail.tags = content?.checks ?? detail.tags;
  } else if (artifact?.type === "result") {
    detail.body = `查询返回 ${content?.row_count ?? content?.rowCount ?? 0} 行${content?.truncated ? "，结果已按安全上限截断" : ""}。`;
    detail.tags = content?.checks ?? detail.tags;
  } else if (artifact?.type === "conclusion") {
    detail.body = content?.text || detail.body;
    if (content?.confidence) {
      detail.tags = [
        `${content.confidence.label}：${content.confidence.score}%`,
        content.metricResolution?.source === "agent_inferred" ? "Agent 临时推导口径" : "企业已发布口径",
      ];
    }
  } else if (artifact?.type === "chart-adjustment") {
    const bit = [];
    if (content?.granularity) bit.push(`横轴聚合到${content.granularity}`);
    if (content?.chartType) bit.push(`切换为${content.chartType}`);
    if (content?.format === "percent") bit.push("纵轴改为百分比");
    detail.body = bit.length ? `Agent 选择“调整当前图表”（${bit.join("；")}），未执行新的数据查询。` : "Agent 选择“调整当前图表”，未执行新的数据查询。";
    detail.tags = ["不触发 SQL", "仅调整展示", `指令：${content?.instruction ?? "图表调整"}`].slice(0, 3);
  }
  if (step.status === "failed" || step.status === "blocked") {
    const costGateReason = artifact?.type === "validation" ? content?.costGate?.reason : "";
    detail.body = step.error?.userSafeMessage
      || step.error?.message
      || costGateReason
      || step.detail
      || "该节点未能完成。";
    detail.tags = [
      `状态：${step.status === "failed" ? "失败" : "已阻断"}`,
      step.error?.code ? `错误码：${step.error.code}` : null,
      content?.costGate?.error_kind ? `原因类型：${content.costGate.error_kind}` : null,
      `尝试：${step.attempt}`,
    ].filter(Boolean);
  }
  return detail;
}

function resultArtifact(snapshot) {
  return [...(snapshot.artifacts ?? [])].reverse().find((item) => item.type === "result");
}

function interpretationArtifact(snapshot) {
  return [...(snapshot.artifacts ?? [])].reverse().find((item) => item.type === "interpretation")?.content ?? null;
}

function isDateKey(key) {
  return /(?:date|time|day|month|year|日期|时间|月份|年度)/i.test(String(key ?? ""));
}

function dateAxisValue(value) {
  const text = String(value ?? "");
  const match = text.match(/^(\d{4})[-/](\d{1,2})(?:[-/](\d{1,2}))?/);
  if (match) return match[3]
    ? `${match[2].padStart(2, "0")}-${match[3].padStart(2, "0")}`
    : `${match[1]}-${match[2].padStart(2, "0")}`;
  return text.length > 10 ? `${text.slice(0, 9)}…` : text;
}

function clampPrecision(digits) {
  if (!Number.isFinite(digits)) return 2;
  return Math.max(0, Math.min(6, Math.round(digits)));
}

const MONTH_AXIS_RE = /^(\d{4})[-/](\d{1,2})$/;
const YYYYMM_RE = /^(\d{6})$/;

export function compressMonthAxis(chart) {
  if (!chart || !Array.isArray(chart.data) || !chart.xKey) return chart;
  const xKey = chart.xKey;
  const parsed = chart.data.map((row) => {
    const raw = String(row?.[xKey] ?? "");
    const dashed = raw.match(MONTH_AXIS_RE);
    if (dashed) return { year: Number(dashed[1]), month: Number(dashed[2]) };
    if (YYYYMM_RE.test(raw)) return { year: Number(raw.slice(0, 4)), month: Number(raw.slice(4, 6)) };
    return null;
  });
  if (!parsed.some(Boolean)) return chart;
  const years = new Set(parsed.filter(Boolean).map((p) => p.year));
  if (years.size !== 1) return chart;
  const data = chart.data.map((row, index) => {
    const p = parsed[index];
    if (!p) return row;
    return { ...row, [xKey]: `${p.month}月` };
  });
  return { ...chart, data };
}

function yearOf(value) {
  const raw = String(value ?? "");
  const dashed = raw.match(/^(\d{4})[-/]\d{1,2}/);
  if (dashed) return dashed[1];
  if (/^\d{6}$/.test(raw)) return raw.slice(0, 4);
  if (/^\d{4}$/.test(raw)) return raw;
  return null;
}


export function aggregateYearAxis(chart, fallbackYear = null) {
  if (!chart || !Array.isArray(chart.data) || !chart.xKey) return chart;
  const xKey = chart.xKey;
  const years = chart.data.map((row) => yearOf(row?.[xKey]));
  const valueKeys = new Set();
  (chart.bars ?? []).forEach((b) => valueKeys.add(b.key));
  (chart.series ?? []).forEach((s) => valueKeys.add(s.key));
  if (valueKeys.size === 0) {
    chart.data.forEach((row) => Object.keys(row).forEach((k) => {
      if (k !== xKey && typeof row[k] === "number") valueKeys.add(k);
    }));
  }
  const sumRows = (rows) => {
    const row = {};
    valueKeys.forEach((k) => {
      let sum = 0; let count = 0;
      rows.forEach((r) => { if (typeof r?.[k] === "number") { sum += r[k]; count += 1; } });
      row[k] = count ? Number(sum.toFixed(2)) : undefined;
    });
    return row;
  };
  let data;
  if (years.some(Boolean)) {
    // 横轴仍含年份（如 2026-01 / 2025-12）：按年分组汇总。
    const distinctYears = [...new Set(years.filter(Boolean))];
    data = distinctYears.map((y) => {
      const rows = chart.data.filter((_, i) => years[i] === y);
      return { [xKey]: y, ...sumRows(rows) };
    });
  } else if (fallbackYear) {
    // 数据已被压缩成“月度无年份”标签（如 1月..7月），但指令给出目标年份：
    // 把所有月份求和成一根该年份柱，满足“横坐标就一个 2026”的诉求。
    data = [{ [xKey]: String(fallbackYear), ...sumRows(chart.data) }];
  } else {
    return chart;
  }
  const values = data.flatMap((row) => [...valueKeys].map((k) => row[k]).filter((v) => typeof v === "number"));
  const max = values.length ? Math.max(...values) : 0;
  const next = { ...chart, data, note: chart.note };
  if (max > 0) next.yDomain = [0, Math.ceil((max * 1.15) / 1000) * 1000];
  if (max < 1) next.yPrecision = 2;
  return next;
}


export function applyChartAdjustment(chart, adjustment = {}) {
  if (!chart) return chart;
  let next = chart;
  if (adjustment.granularity === "year") {
    const fallbackYear = String(adjustment.instruction ?? "").match(/(?:19|20)\d{2}/)?.[0] ?? null;
    next = aggregateYearAxis(next, fallbackYear);
  }
  const target = adjustment.chartType;
  if ((target === "line" || target === "bar" || target === "combo" || target === "donut") && next.type !== target) {
    next = switchChartType(next, target);
  }
  if (adjustment.format === "percent") {
    next = { ...next, unit: "%", yDomain: [0, 100], yPrecision: 1 };
  }
  return next;
}


function switchChartType(chart, target) {
  const valueKey = chart.valueKey || (chart.bars ?? [])[0]?.key || Object.keys(chart.data?.[0] ?? {}).find((k) => k !== chart.xKey);
  if (!valueKey) return chart;
  const series = [{ key: valueKey, name: valueKey }];
  if (target === "line") return { ...chart, type: "line", series, bars: [] };
  if (target === "donut") {
    const data = (chart.data ?? []).map((row) => ({ name: String(row?.[chart.xKey] ?? "未命名"), value: Number(row?.[valueKey] ?? 0) }));
    return { ...chart, type: "donut", data, centerTitle: "合计", centerValue: data.reduce((a, b) => a + (b.value || 0), 0).toLocaleString("zh-CN"), bars: [], series: [] };
  }
  if (target === "combo") return { ...chart, type: "combo", bars: [series[0]], lines: [], series: [] };
  return { ...chart, type: "bar", bars: series, series: [] };
}


function hydrateChart(chart) {
  if (!chart) return chart;
  const next = { ...chart };
  if (isDateKey(next.xKey)) next.xTickFormatter = (value) => dateAxisValue(value);
  if (next.yPrecision != null || next.unit) {
    const digits = clampPrecision(Number(next.yPrecision ?? 2));
    next.yTickFormatter = (value) => Number(value).toLocaleString("zh-CN", {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    });
  }
  return next;
}

function requiresPeriodComparison(tasks = []) {
  return tasks.some((task) => {
    const question = String(
      task.normalizedQuestion
      ?? task.question
      ?? task.intent?.originalQuestion
      ?? task.title
      ?? "",
    );
    const pairedPeriods = [
      [["本月", "这个月"], ["上月", "上个月"]],
      [["本周", "这周"], ["上周"]],
      [["本季度", "这个季度"], ["上季度"]],
      [["今年", "本年"], ["去年", "上年"]],
    ];
    return question.includes("环比") || pairedPeriods.some(([currentTerms, previousTerms]) => (
      currentTerms.some((term) => question.includes(term))
      && previousTerms.some((term) => question.includes(term))
    ));
  });
}

function confidenceWithResultGuard(confidence, tasks, execution) {
  if (!confidence || confidence.cappedByIncompleteResult || !requiresPeriodComparison(tasks)) return confidence ?? null;
  const columns = (execution.columns ?? []).map((item) => String(item ?? "").toLowerCase());
  const rows = execution.rows ?? [];
  const hasCurrentColumn = columns.some((column) => /(?:^|_)(?:current|this)(?:_|$)|本月|本期|当前/.test(column));
  const hasPreviousColumn = columns.some((column) => /(?:^|_)(?:previous|prev|last)(?:_|$)|上月|上期|前期/.test(column));
  if (hasCurrentColumn && hasPreviousColumn) return confidence;

  const timeIndexes = columns
    .map((column, index) => (/month|date|period|year|week|quarter|月份|日期|周期|年度|周|季度/.test(column) ? index : -1))
    .filter((index) => index >= 0);
  const periods = new Set(
    rows.flatMap((row) => timeIndexes.map((index) => row[index])).filter((value) => value != null && value !== ""),
  );
  if (periods.size >= 2) return confidence;

  const issue = periods.size === 1
    ? `比较任务要求至少两个周期，但结果仅覆盖 1 个周期（${[...periods][0]}），无法完成完整比较。`
    : "比较任务的结果中未找到可验证的双周期字段，无法确认两侧数据是否完整。";
  const score = Math.min(Number(confidence.score ?? 100), 60);
  return {
    ...confidence,
    score,
    level: "low",
    label: "低可信度",
    components: {
      ...(confidence.components ?? {}),
      resultCompleteness: periods.size === 1 ? 35 : 50,
    },
    reasons: [...new Set([...(confidence.reasons ?? []), issue])],
    cappedByIncompleteResult: true,
    completenessIssues: [issue],
  };
}

function buildSources(artifacts = []) {
  const sources = new Map();
  for (const artifact of artifacts.filter((item) => item.type === "context")) {
    for (const metric of artifact.content?.metrics ?? []) {
      sources.set(`metric:${metric.id}`, { icon: "knowledge", title: metric.name, caption: `业务指标 · ${metric.version ?? "当前版本"}` });
    }
    for (const table of artifact.content?.tables ?? []) {
      sources.set(`table:${table.name}`, { icon: "database", title: table.name, caption: table.cnName ?? "当前数据源" });
    }
  }
  return [...sources.values()];
}

export function adaptAnalysisRun(snapshot) {
  if (!snapshot) return null;
  const artifacts = snapshot.artifacts ?? [];
  const steps = latestSteps(snapshot.steps, snapshot.tasks);
  const checkpoints = snapshot.checkpoints ?? [];
  const plannerSnapshot = snapshot.planner ?? {};
  const plannerObservations = plannerSnapshot.observations ?? [];
  const plannerObservationByStep = new Map(plannerObservations.map((item) => [item.stepId, item]));
  const businessPlanSteps = (plannerSnapshot.plan?.steps ?? []).map((step, index) => {
    const observation = plannerObservationByStep.get(step.id);
    return {
      id: step.id,
      order: index + 1,
      capability: step.capability,
      purpose: step.purpose,
      dependsOn: step.dependsOn ?? [],
      status: observation?.status ?? (snapshot.status === "succeeded" ? "pending" : "running"),
      summary: observation?.summary ?? "等待前置能力完成",
      reasonCode: observation?.reasonCode,
      quality: observation?.quality ?? {},
      artifactRefs: observation?.artifactRefs ?? [],
    };
  });
  const multi = (snapshot.tasks?.length ?? 0) > 1;
  const plan = steps.map((step) => {
    const task = snapshot.tasks?.find((item) => item.id === step.taskId);
    const checkpoint = [...checkpoints].reverse().find((item) => item.stepKey === step.stepKey && item.summary?.taskId === step.taskId);
    return {
      id: step.id,
      label: multi && task ? `${task.order}. ${step.label}` : step.label,
      caption: step.error?.userSafeMessage
        || step.detail
        || (step.status === "succeeded" ? "已完成并记录产物" : "处理中"),
      duration: step.status === "succeeded" ? "完成" : "—",
      status: step.status,
      taskId: step.taskId,
      taskOrder: task?.order ?? 0,
      taskTitle: task?.title ?? "运行准备",
      stepKey: step.stepKey,
      checkpoint,
    };
  });
  const firstSql = artifacts.find((item) => item.type === "sql")?.content?.sql;
  const contextSource = artifacts.find((item) => item.type === "context")?.content?.source;
  const mysqlFullJoinMismatch = ["mysql", "mariadb"].includes(String(contextSource?.type).toLowerCase())
    && /\bFULL\s+(?:OUTER\s+)?JOIN\b/i.test(firstSql ?? "");
  const dialectFailureMessage = mysqlFullJoinMismatch
    ? "生成的 SQL 使用了 MySQL 不支持的 FULL OUTER JOIN，数据库因此无法生成执行计划。系统应改用 LEFT JOIN 与 UNION 组合后重新校验。"
    : "";
  const planDetails = Object.fromEntries(plan.map((item) => {
    const step = steps.find((candidate) => candidate.id === item.id);
    const detail = detailFor(step, artifactFor(artifacts, step));
    if (dialectFailureMessage && item.stepKey === "query.validate" && item.status === "failed") {
      detail.body = dialectFailureMessage;
      detail.tags = ["状态：失败", "原因：数据库方言不兼容", `错误码：${step.error?.code ?? "QUERY_EXPLAIN_FAILED"}`];
    }
    return [item.id, detail];
  }));
  if (firstSql) planDetails.query = { title: "实际执行 SQL", body: "已通过只读安全链路生成。", code: firstSql };

  const execution = resultArtifact(snapshot)?.content ?? {};
  const tasks = snapshot.tasks ?? [];
  const interpretations = (snapshot.artifacts ?? [])
    .filter((item) => item.type === "interpretation")
    .sort((a, b) => {
      const orderFor = (item, fallback) => tasks.find((t) => t.id === item.taskId)?.order ?? fallback;
      return orderFor(a, 0) - orderFor(b, 0);
    });
  const charts = interpretations
    .flatMap((item) => {
      const content = item.content ?? {};
      const rawCharts = content.charts?.length ? content.charts : (content.chart ? [content.chart] : []);
      return rawCharts.map((raw) => {
        let next = raw ? hydrateChart(raw) : null;
        if (next && !next.title) {
          const task = tasks.find((t) => t.id === item.taskId);
          const taskTitle = (task?.title ?? "").trim();
          next = { ...next, title: taskTitle || (next.xKey ? `按${next.xKey}查看` : "查询结果") };
        }
        return next;
      }).filter(Boolean);
    });
  const chart = charts.length ? charts.at(-1) : buildChart(snapshot, execution);
  const lastInterpKpis = interpretations.at(-1)?.content?.kpis;
  const fallbackKpis = buildBusinessKpis(execution, chart);
  const kpis = lastInterpKpis?.length ? lastInterpKpis : fallbackKpis;
  const validation = [...artifacts].reverse().find((item) => item.type === "validation")?.content ?? {};
  const rowCount = execution.row_count ?? execution.rowCount ?? 0;
  const conversationOnly = snapshot.status === "succeeded"
    && (snapshot.tasks?.length ?? 0) === 0
    && !artifacts.some((item) => item.type === "result");
  const conclusions = artifacts.filter((item) => item.type === "conclusion" && item.content?.text);
  const finalConclusion = conclusions.at(-1)?.content ?? {};
  const displayConfidence = confidenceWithResultGuard(finalConclusion.confidence, snapshot.tasks, execution);
  const rawConclusionText = snapshot.assistantMessage?.content
    || conclusions.map((item) => item.content.text).join("\n\n")
    || (snapshot.status === "succeeded" ? "分析已完成，结果与执行证据已保存。" : "正在执行分析任务。 ");
  const parsedNarrative = parseAnalysisNarrative(rawConclusionText, {
    sectionTitles: snapshot.tasks?.map((task) => task.title) ?? [],
  });
  const narrative = {
    ...parsedNarrative,
    highlights: parsedNarrative.highlights.map((item, index) => ({
      ...item,
      title: conciseFindingTitle(snapshot.tasks?.[index]?.title, item.title),
    })),
  };
  const interactionPrompt = snapshot.interaction?.prompt;
  const startedAt = snapshot.startedAt ? new Date(snapshot.startedAt).getTime() : Number.NaN;
  const finishedAt = snapshot.finishedAt ? new Date(snapshot.finishedAt).getTime() : Number.NaN;
  const totalDurationMs = Number.isFinite(startedAt) && Number.isFinite(finishedAt) && finishedAt >= startedAt
    ? finishedAt - startedAt
    : null;
  const fallbackError = {
    code: snapshot.errorCode,
    message: snapshot.errorCode === "WRITE_INTENT_BLOCKED"
      ? "系统仅支持只读分析；已识别到写操作意图，并在生成 SQL 前阻断。"
      : "本次分析未能完成。",
    suggestion: "可调整问题后重新发起，原运行记录和错误证据会保留。",
  };
  const rawError = snapshot.error ?? fallbackError;
  const taskConstraintMismatch = rawError?.code === "TASK_CONSTRAINT_MISMATCH";
  const businessError = rawError ? {
    ...rawError,
    title: dialectFailureMessage
      ? "SQL 与当前数据库方言不兼容"
      : taskConstraintMismatch
        ? "分析条件未能完整落实"
        : rawError.userSafeTitle,
    message: dialectFailureMessage
      || rawError.userSafeMessage
      || (taskConstraintMismatch
        ? "系统生成的查询没有完整落实时间或分组条件，因此停止执行，避免返回不完整的分析结论。"
        : rawError.message),
    suggestion: dialectFailureMessage
      ? "系统已增加 MySQL 方言检查和自动重试；重新发起该问题即可生成兼容查询。"
      : taskConstraintMismatch
        ? "可查看证据链了解未满足的具体条件，重新发起后系统会自动修正查询。"
        : rawError.suggestion,
    showCodeToUser: rawError.showCodeToUser === true,
  } : null;

  return {
    ...snapshot,
    status: STATUS_MAP[snapshot.status] ?? snapshot.status,
    clarification: interactionPrompt ? {
      interactionId: snapshot.interaction.id,
      question: interactionPrompt.question,
      options: (interactionPrompt.options ?? []).map((item) => typeof item === "string" ? { label: item, value: item } : item),
      fields: (interactionPrompt.fields ?? []).map((field) => ({
        ...field,
        options: (field.options ?? []).map((item) => typeof item === "string" ? { label: item, value: item } : item),
      })),
      allowCustom: interactionPrompt.allowCustom,
    } : null,
    error: businessError,
    chartAdjustment: artifacts.find((item) => item.type === "chart-adjustment")?.content ?? null,
    plan,
    planDetails,
    planner: {
      ...plannerSnapshot,
      steps: businessPlanSteps,
      completed: businessPlanSteps.filter((item) => ["succeeded", "accepted", "skipped"].includes(item.status)).length,
      failed: businessPlanSteps.filter((item) => item.status === "failed").length,
    },
    result: {
      kind: conversationOnly ? "conversation" : "execution",
      kpis: conversationOnly ? [] : kpis,
    },
    chart: conversationOnly ? null : chart,
    charts: conversationOnly ? [] : charts,
    conclusion: {
      text: narrative.summary || "分析已完成，结果与执行证据已保存。",
      highlights: narrative.highlights.length ? narrative.highlights : conclusions.length > 1 ? conclusions.map((item, index) => ({
        title: snapshot.tasks?.find((task) => task.id === item.taskId)?.title ?? `分析结论 ${index + 1}`,
        text: item.content.text,
      })) : [],
      followUp: null,
      confidence: displayConfidence,
      metricResolution: finalConclusion.metricResolution ?? null,
    },
    execution: {
      engine: "只读数据网关",
      durationMs: execution.duration_ms ?? execution.durationMs ?? null,
      processingDurationMs: snapshot.activeDurationMs ?? totalDurationMs,
      totalDurationMs,
      rowCount,
      columns: execution.columns ?? [],
      rows: execution.rows ?? [],
    },
    trust: {
      checks: validation.checks ?? [],
      costGate: validation.costGate ?? null,
      confidence: displayConfidence,
      metricResolution: finalConclusion.metricResolution ?? null,
    },
    sources: buildSources(artifacts),
  };
}
