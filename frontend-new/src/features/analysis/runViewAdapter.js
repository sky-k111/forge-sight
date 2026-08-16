import { conciseFindingTitle, parseAnalysisNarrative } from "../../lib/narrative";

export { parseAnalysisNarrative } from "../../lib/narrative";

const STATUS_MAP = {
  waiting_user: "clarifying",
  modeling_proposal: "modeling-proposal",
};

const STEP_ARTIFACT = {
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

function chartTitleFor(snapshot, xKey, numeric) {
  const taskTitle = snapshot.tasks?.at(-1)?.title?.trim();
  if (taskTitle && !/^(你好|hi|hello|test|测试)[！!。，,.\s]*$/iu.test(taskTitle)) return taskTitle;
  return numeric.length ? `按${xKey}查看${numeric[0]}分布` : "查询结果";
}

function isDateDimension(key, rows) {
  if (/(?:date|time|day|month|year|日期|时间|月份|年度)/i.test(String(key))) return true;
  const values = rows.slice(0, 8).map((row) => String(row[key] ?? "")).filter(Boolean);
  return values.length > 0 && values.every((value) => /^\d{4}[-/]\d{1,2}(?:[-/]\d{1,2})?(?:[ T].*)?$/.test(value));
}

function formatAxisLabel(value, dateAxis = false) {
  const text = String(value ?? "");
  if (dateAxis) {
    const match = text.match(/^(\d{4})[-/](\d{1,2})(?:[-/](\d{1,2}))?/);
    if (match) return match[3] ? `${match[2].padStart(2, "0")}-${match[3].padStart(2, "0")}` : `${match[1]}-${match[2].padStart(2, "0")}`;
  }
  return text.length > 10 ? `${text.slice(0, 9)}…` : text;
}

function isRateMetric(key) {
  return /(?:率|占比|比例|百分比|rate|ratio|percent|pct)/i.test(String(key));
}

function isRateBenchmarkMetric(key, rows) {
  if (!/(?:目标|标准|基准|阈值|target|standard|benchmark|threshold)/i.test(String(key))) return false;
  const values = rows
    .map((row) => Number(row[key]))
    .filter(Number.isFinite);
  return values.length > 0 && values.every((value) => value >= 0 && value <= 100);
}

function isAdditiveMetric(key) {
  return /(?:数量|产量|件数|次数|时长|分钟|金额|库存|qty|quantity|count|amount|minutes|duration|total)/i.test(String(key))
    && !isRateMetric(key);
}

function formatKpiValue(value, key) {
  const number = Number(value);
  if (!Number.isFinite(number)) return String(value ?? "—");
  const formatted = number.toLocaleString("zh-CN", { maximumFractionDigits: 2 });
  return isRateMetric(key) ? `${formatted}%` : formatted;
}

function niceAxisStep(rawStep) {
  if (!Number.isFinite(rawStep) || rawStep <= 0) return 1;
  const exponent = Math.floor(Math.log10(rawStep));
  const magnitude = 10 ** exponent;
  const fraction = rawStep / magnitude;
  const niceFraction = fraction <= 1 ? 1 : fraction <= 2 ? 2 : fraction <= 5 ? 5 : 10;
  return niceFraction * magnitude;
}

function buildAutoYAxis(data, keys) {
  const values = data
    .flatMap((row) => keys.map((key) => Number(row[key])))
    .filter(Number.isFinite);
  if (!values.length) return {};

  const minimum = Math.min(...values);
  const maximum = Math.max(...values);
  const rateScale = keys.length > 0 && keys.every(isRateMetric) && minimum >= 0 && maximum <= 100;
  const range = maximum - minimum;
  const padding = range > 0
    ? Math.max(range * 0.18, rateScale ? 0.1 : 0)
    : Math.max(rateScale ? 0.1 : 1, Math.abs(maximum) * 0.05);
  const rawMinimum = rateScale ? Math.max(0, minimum - padding) : minimum - padding;
  const rawMaximum = rateScale ? Math.min(100, maximum + padding) : maximum + padding;
  const step = niceAxisStep(Math.max(rawMaximum - rawMinimum, Number.EPSILON) / 4);
  let domainMinimum = Math.floor(rawMinimum / step) * step;
  let domainMaximum = Math.ceil(rawMaximum / step) * step;
  if (rateScale) {
    domainMinimum = Math.max(0, domainMinimum);
    domainMaximum = Math.min(100, domainMaximum);
  }
  if (domainMinimum === domainMaximum) domainMaximum = domainMinimum + step;

  const precision = step < 0.01 ? 3 : step < 0.1 ? 2 : step < 1 ? 1 : 0;
  return {
    yDomain: [Number(domainMinimum.toFixed(precision + 1)), Number(domainMaximum.toFixed(precision + 1))],
    yTickFormatter: (value) => Number(value).toLocaleString("zh-CN", {
      minimumFractionDigits: precision,
      maximumFractionDigits: precision,
    }),
    unit: rateScale ? "%" : "",
  };
}

function buildBusinessKpis(execution, chart) {
  const columns = execution?.columns ?? [];
  const rawRows = execution?.rows ?? [];
  if (!columns.length || !rawRows.length) return [];
  const rows = rawRows.map((row) => (
    Array.isArray(row) ? Object.fromEntries(columns.map((column, index) => [column, row[index]])) : row
  ));
  const numeric = columns.filter((column) => rows.some((row) => row[column] !== "" && row[column] != null && Number.isFinite(Number(row[column]))));
  if (!numeric.length) return [];
  const dimension = columns.find((column) => !numeric.includes(column));

  if (rows.length === 1) {
    return numeric.slice(0, 3).map((key) => ({
      label: key,
      value: formatKpiValue(rows[0][key], key),
      caption: dimension ? String(rows[0][dimension] ?? "当前查询结果") : "当前查询结果",
    }));
  }

  const primary = chart?.valueKey
    ?? chart?.series?.[0]?.key
    ?? chart?.bars?.[0]?.key
    ?? chart?.lines?.[0]?.key
    ?? numeric[0];
  const points = rows
    .map((row) => ({ row, value: Number(row[primary]) }))
    .filter((item) => Number.isFinite(item.value));
  if (!points.length) return [];
  const maximum = points.reduce((best, item) => item.value > best.value ? item : best);
  const minimum = points.reduce((best, item) => item.value < best.value ? item : best);
  const total = points.reduce((sum, item) => sum + item.value, 0);
  const average = total / points.length;
  const labelFor = (item) => dimension ? String(item.row[dimension] ?? "对应记录") : "对应记录";
  const result = [];
  if (isAdditiveMetric(primary)) {
    result.push({
      label: `${primary}合计`,
      value: formatKpiValue(total, primary),
      caption: `基于 ${points.length} 条返回记录`,
    });
  } else {
    result.push({
      label: `${primary}平均值`,
      value: formatKpiValue(average, primary),
      caption: `基于 ${points.length} 条返回记录`,
    });
  }
  result.push({
    label: `${primary}最高`,
    value: formatKpiValue(maximum.value, primary),
    caption: labelFor(maximum),
  });
  result.push({
    label: `${primary}最低`,
    value: formatKpiValue(minimum.value, primary),
    caption: labelFor(minimum),
  });
  return result.slice(0, 3);
}

function buildChart(snapshot, execution) {
  const columns = execution?.columns ?? [];
  const rawRows = execution?.rows ?? [];
  if (columns.length < 2 || rawRows.length === 0) return null;
  const rows = rawRows.map((row) => {
    if (!Array.isArray(row)) return row;
    return Object.fromEntries(columns.map((column, index) => [column, row[index]]));
  });
  const numericAll = columns.filter((column) => rows.some((row) => row[column] !== "" && row[column] != null && Number.isFinite(Number(row[column]))));
  const categorical = columns.filter((column) => !numericAll.includes(column));
  const xKey = columns.find((column) => !numericAll.includes(column)) ?? columns[0];
  const numeric = numericAll.filter((column) => column !== xKey);
  if (numeric.length === 0) return null;
  const analysisTypes = new Set((snapshot.tasks ?? []).map((task) => task.analysisType));
  const questionText = [snapshot.question, ...(snapshot.tasks ?? []).flatMap((task) => [task.title, task.normalizedQuestion])].filter(Boolean).join(" ");
  const taskRequestedChart = [...(snapshot.tasks ?? [])].reverse()
    .map((task) => task.requestedChart ?? task.intent?.requestedChart)
    .find(Boolean);
  const requestedChart = taskRequestedChart
    ?? (/组合图|双轴图|柱线图|柱线组合图/.test(questionText) ? "combo"
      : /热力图|热度图|矩阵图/.test(questionText) ? "heatmap"
        : /折线图|趋势图|曲线图/.test(questionText) ? "line"
      : /柱状图|条形图/.test(questionText) ? "bar"
        : /饼图|环形图/.test(questionText) ? "donut"
          : /散点图/.test(questionText) ? "scatter"
            : /表格展示|用表格|以表格/.test(questionText) ? "table" : null);
  const dateAxis = isDateDimension(xKey, rows);
  if (requestedChart === "table") return null;
  const correlation = (requestedChart === "scatter" || (!requestedChart && analysisTypes.has("correlation"))) && numericAll.length >= 2;
  const heatmap = requestedChart === "heatmap"
    || (!requestedChart && categorical.length >= 2 && /交叉|矩阵|热力|密度|分布在哪/.test(questionText));
  const explicitRateKeys = numeric.filter(isRateMetric);
  const rateKeys = numeric.filter((key) => (
    isRateMetric(key)
    || (explicitRateKeys.length > 0 && isRateBenchmarkMetric(key, rows))
  ));
  const absoluteKeys = numeric.filter((key) => !rateKeys.includes(key));
  const combo = requestedChart === "combo"
    || (!requestedChart
      && numeric.length >= 2
      && rateKeys.length > 0
      && absoluteKeys.length > 0
      && /对比|比较|与|和|同时|关系/.test(questionText)
      && !analysisTypes.has("correlation"));
  const share = requestedChart === "donut"
    || (!requestedChart && !dateAxis && numeric.length === 1 && /占比|构成|比例|份额|分布/.test(questionText) && rows.length <= 10);
  const trend = requestedChart === "line" || (!requestedChart && (analysisTypes.has("trend") || dateAxis));
  const displayLimit = trend ? 36 : 12;
  const chartRows = rows.slice(0, displayLimit).map((row) => ({
    ...row,
    ...Object.fromEntries(numericAll.map((column) => [column, Number(row[column])])),
  }));
  const axisFormatter = (value) => formatAxisLabel(value, dateAxis);
  const note = rows.length > displayLimit
    ? `图表展示前 ${displayLimit} 项，共返回 ${rows.length} 条记录；完整结果可导出。`
    : `图表展示全部 ${rows.length} 条返回记录。`;

  if (correlation) {
    const [xNumericKey, yNumericKey] = numericAll;
    return {
      type: "scatter",
      kicker: "相关性分析 · 实时生成",
      title: chartTitleFor(snapshot, xNumericKey, [yNumericKey]),
      data: chartRows,
      xNumericKey,
      yNumericKey,
      xName: xNumericKey,
      yName: yNumericKey,
      legend: [{ label: `${xNumericKey} × ${yNumericKey}` }],
      note,
    };
  }

  if (heatmap && categorical.length >= 2) {
    const [rowKey, columnKey] = categorical;
    const valueKey = numeric[0];
    const rowLabels = [...new Set(rows.map((row) => String(row[rowKey] ?? "未命名")))].slice(0, 12);
    const columnLabels = [...new Set(rows.map((row) => String(row[columnKey] ?? "未命名")))].slice(0, 10);
    const cellMap = new Map();
    for (const row of rows) {
      const rowLabel = String(row[rowKey] ?? "未命名");
      const columnLabel = String(row[columnKey] ?? "未命名");
      if (!rowLabels.includes(rowLabel) || !columnLabels.includes(columnLabel)) continue;
      const key = `${rowLabel}\u0000${columnLabel}`;
      cellMap.set(key, (cellMap.get(key) ?? 0) + Number(row[valueKey] ?? 0));
    }
    return {
      type: "heatmap",
      kicker: "二维交叉分析 · 实时生成",
      title: chartTitleFor(snapshot, `${rowKey} × ${columnKey}`, [valueKey]),
      rows: rowLabels,
      columns: columnLabels,
      cells: [...cellMap].map(([key, value]) => {
        const [row, column] = key.split("\u0000");
        return { row, column, value };
      }),
      rowName: rowKey,
      columnName: columnKey,
      valueName: valueKey,
      valueKey,
      legend: [],
      note: `热力图展示 ${rowLabels.length} × ${columnLabels.length} 个交叉位置；颜色越深代表${valueKey}越高。`,
    };
  }

  if (share) {
    const valueKey = numeric[0];
    const donutData = chartRows.map((row) => ({ name: String(row[xKey] ?? "未命名"), value: Number(row[valueKey]) }));
    return {
      type: "donut",
      kicker: "结构占比 · 实时生成",
      title: chartTitleFor(snapshot, xKey, numeric),
      data: donutData,
      centerTitle: "合计",
      centerValue: donutData.reduce((sum, item) => sum + item.value, 0).toLocaleString("zh-CN"),
      legend: donutData.map((item) => ({ label: item.name })),
      unit: "",
      note,
    };
  }

  if (combo && numeric.length >= 2) {
    const barKeys = (absoluteKeys.length ? absoluteKeys : numeric.slice(0, 1)).slice(0, 2);
    const lineKeys = (rateKeys.length ? rateKeys : numeric.filter((key) => !barKeys.includes(key))).slice(0, 2);
    const bars = barKeys.map((key) => ({ key, name: key }));
    const lines = lineKeys.map((key) => ({ key, name: key }));
    return {
      type: "combo",
      kicker: "多指标对照 · 实时生成",
      title: chartTitleFor(snapshot, xKey, [...barKeys, ...lineKeys]),
      data: chartRows,
      xKey,
      bars,
      lines,
      leftUnit: "",
      rightUnit: lineKeys.every(isRateMetric) ? "%" : "",
      xTickFormatter: axisFormatter,
      valueKey: barKeys[0],
      legend: [...bars, ...lines].map((item) => ({ label: item.name })),
      note,
    };
  }

  if (trend) {
    const seriesKeys = numeric.slice(0, 4);
    const yAxis = buildAutoYAxis(chartRows, seriesKeys);
    return {
      type: "line",
      kicker: "趋势分析 · 实时生成",
      title: chartTitleFor(snapshot, xKey, numeric),
      data: chartRows,
      xKey,
      series: seriesKeys.map((key) => ({ key, name: key })),
      valueKey: numeric[0],
      ...yAxis,
      xTickFormatter: axisFormatter,
      note,
    };
  }

  return {
    type: "bar",
    kicker: "查询结果 · 实时生成",
    title: chartTitleFor(snapshot, xKey, numeric),
    data: chartRows,
    xKey,
    bars: numeric.slice(0, 3).map((key) => ({ key, name: key })),
    valueKey: numeric[0],
    unit: "",
    xTickFormatter: axisFormatter,
    note,
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
  const mysqlFullJoinMismatch = String(contextSource?.type).toLowerCase() === "mysql"
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
  const chart = buildChart(snapshot, execution);
  const businessKpis = buildBusinessKpis(execution, chart);
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
      inputPlaceholder: interactionPrompt.inputPlaceholder,
    } : null,
    error: businessError,
    report: snapshot.assistantMessage?.metadata?.report ?? null,
    plan,
    planDetails,
    result: {
      kind: conversationOnly ? "conversation" : "execution",
      kpis: conversationOnly ? [] : businessKpis,
    },
    chart: conversationOnly ? null : chart,
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
