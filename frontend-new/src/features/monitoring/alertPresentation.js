// Presentation follows monitoring API enums; it does not infer units or detector health.
export const ALERT_STATES = {
  firing: { label: "告警中", tone: "red" },
  recovering: { label: "恢复观察", tone: "amber" },
  resolved: { label: "已恢复", tone: "green" },
};
export const ALERT_SEVERITIES = {
  critical: { label: "严重", tone: "red", rank: 0 },
  high: { label: "高", tone: "amber", rank: 1 },
  medium: { label: "中", tone: "blue", rank: 2 },
  low: { label: "低", tone: "gray", rank: 3 },
};
export const EVALUATION_STATES = {
  firing: "触发告警",
  pending: "触发观察",
  normal: "正常观测",
  no_data: "数据不足",
  recovering: "恢复观察",
  resolved: "已恢复",
};
export const QUALITY_STATES = {
  ok: "通过",
  insufficient_baseline: "基线不足",
  insufficient_samples: "样本不足",
  no_data: "无数据",
  error: "检查失败",
};
export const GRAINS = { hour: "小时", day: "天", shift: "班次" };
export const BASELINE_METHODS = {
  median_mad: "历史中位数 / MAD",
  same_weekday: "同星期历史基线",
  same_shift: "同班次历史基线",
  shewhart: "均值 / 标准差控制界限",
};
export const CHANNELS = {
  email: "邮件",
  feishu: "飞书",
  dingtalk: "钉钉",
  inbox: "站内消息",
  in_app: "站内消息",
};
export const DELIVERY_STATES = {
  sent: { label: "已发送", tone: "green" },
  succeeded: { label: "已发送", tone: "green" },
  pending: { label: "待发送", tone: "gray" },
  queued: { label: "排队中", tone: "gray" },
  sending: { label: "发送中", tone: "blue" },
  retrying: { label: "重试中", tone: "amber" },
  retry_wait: { label: "等待重试", tone: "amber" },
  failed: { label: "发送失败", tone: "red" },
  dead: { label: "已停止重试", tone: "red" },
  suppressed: { label: "已抑制", tone: "gray" },
};
const numberFormat = new Intl.NumberFormat("zh-CN", { maximumSignificantDigits: 12 });
export function numeric(value) {
  if (value === null || value === undefined || value === "") return null;
  if (typeof value !== "number" && typeof value !== "string") return null;
  if (typeof value === "string" && !value.trim()) return null;
  const result = Number(value);
  return Number.isFinite(result) ? result : null;
}
export function formatNumber(value) {
  const number = numeric(value);
  return number === null ? "—" : numberFormat.format(number);
}
export function formatTime(value, full = false) {
  if (!value) return "—";
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    ...(full ? { year: "numeric" } : {}),
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}
export function timestamp(value) {
  const result = new Date(value ?? "").getTime();
  return Number.isFinite(result) ? result : 0;
}
export function summarizeAlerts(items) {
  const counts = { firing: 0, recovering: 0, resolved: 0, affected: 0 };
  const affected = new Set();
  items.forEach((item) => {
    if (Object.hasOwn(ALERT_STATES, item.status)) counts[item.status] += 1;
    if (item.status === "firing" || item.status === "recovering") {
      affected.add(
        [item.sourceId, item.entityType, item.entityId || item.entityName || item.id].join(":")
      );
    }
  });
  counts.affected = affected.size;
  return counts;
}
export function filterAlerts(
  items,
  { status = "", severity = "", metric = "", search = "", sort = "latest" } = {}
) {
  const query = search.trim().toLocaleLowerCase();
  return items
    .filter(
      (item) =>
        (!status || item.status === status) &&
        (!severity || item.severity === severity) &&
        (!metric || item.metricName === metric) &&
        (!query ||
          [item.title, item.metricName, item.entityName, item.entityId]
            .join(" ")
            .toLocaleLowerCase()
            .includes(query))
    )
    .sort((a, b) => {
      if (sort === "severity") {
        const difference =
          (ALERT_SEVERITIES[a.severity]?.rank ?? 4) - (ALERT_SEVERITIES[b.severity]?.rank ?? 4);
        if (difference) return difference;
      }
      return timestamp(b.lastSeenAt) - timestamp(a.lastSeenAt);
    });
}
export function evidenceRows(items) {
  return items
    .map((item) => ({
      ...item,
      currentValue: numeric(item.currentValue),
      baselineValue: numeric(item.baselineValue),
    }))
    .sort(
      (a, b) =>
        timestamp(a.logicalPeriod || a.createdAt) - timestamp(b.logicalPeriod || b.createdAt)
    );
}
export function latestTimestamp(items, field) {
  return items.reduce(
    (latest, item) => (timestamp(item[field]) > timestamp(latest) ? item[field] : latest),
    null
  );
}
export function ruleCondition(rule) {
  if (!rule?.condition || !Object.keys(rule.condition).length) return "未提供规则条件";
  const condition = rule.condition;
  const periods = condition.pendingPeriods ?? 1;
  if (condition.baselineMethod) {
    const method = BASELINE_METHODS[condition.baselineMethod] || condition.baselineMethod;
    const direction =
      { lower: "低于下界", upper: "高于上界", two_sided: "超出上下界" }[condition.direction] ||
      "超出控制界限";
    return `${method} · ${direction}，连续 ${periods} 个周期触发`;
  }
  const operator =
    { lt: "低于", lte: "不高于", gt: "高于", gte: "不低于" }[condition.operator] ||
    condition.operator ||
    "阈值";
  return `${operator} ${formatNumber(condition.openThreshold)}，连续 ${periods} 个周期触发`;
}
export function recoveryCondition(rule) {
  const condition = rule?.condition;
  if (!condition || !Object.keys(condition).length) return "未提供恢复条件";
  if (condition.baselineMethod) {
    const boundary =
      { lower: "不低于动态下界", upper: "不高于动态上界" }[condition.direction] || "回到控制界限内";
    return `${boundary}，连续 ${condition.recoveryPeriods ?? 1} 个周期恢复`;
  }
  const operator = ["lt", "lte"].includes(condition.operator)
    ? "不低于"
    : ["gt", "gte"].includes(condition.operator)
      ? "不高于"
      : "恢复阈值";
  return `${operator} ${formatNumber(condition.closeThreshold ?? condition.openThreshold)}，连续 ${condition.recoveryPeriods ?? 1} 个周期恢复`;
}
export function valueScale(rule) {
  // Ratio is an explicit plan type, not a guess from the metric name.
  return rule?.queryPlan?.kind === "ratio" ? "比例原值（不换算百分比）" : "规则原值";
}
