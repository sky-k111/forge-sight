export const seriesColors = [
  "var(--color-primary)",
  "var(--color-teal)",
  "var(--color-violet)",
  "var(--color-amber)",
  "var(--color-red)",
  "var(--color-green)",
];

export const axisDefaults = {
  tick: { fill: "var(--color-muted)", fontSize: 12 },
  tickLine: false,
  axisLine: { stroke: "var(--color-line-strong)" },
  tickMargin: 8,
};

// recharts 默认按“未格式化”的原始值测量 Y 轴宽度，而对 <1600000> 这类
// locale 带千分位的数值（1,600,000），格式化后明显更宽，导致刻度左缘被裁。
// 这里改用“格式化后的最大文本长度”估算 Y 轴宽度，保证最左数字不被剪掉。
export function numericAxisWidth(data, keys, formatter) {
  let maxLen = 0;
  for (const row of data ?? []) {
    for (const key of keys ?? []) {
      const text = formatter ? String(formatter(row[key] ?? "") ?? "") : String(row[key] ?? "");
      if (text.length > maxLen) maxLen = text.length;
    }
  }
  // 近似：等宽数字 12px ≈ 7.2px/字符，再留 14px（轴间隙 + 右对齐缓冲）
  return Math.max(44, Math.ceil(maxLen * 7.4) + 14);
}

export const gridDefaults = {
  stroke: "var(--color-line)",
  strokeDasharray: "3 6",
  vertical: false,
};
