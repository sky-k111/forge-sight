// 图表与 KPI 构建：把问析执行结果归约为前端图表描述。
// 本模块为纯函数集合，仅依赖输入数据，供 runViewAdapter 调用并可独立测试。

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

export function buildBusinessKpis(execution, chart) {
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

export function buildChart(snapshot, execution) {
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
