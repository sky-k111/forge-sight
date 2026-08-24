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
    } : null,
    error: businessError,
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
