export const seriesColors = [
  "var(--color-primary)",
  "var(--color-teal)",
  "var(--color-violet)",
  "var(--color-amber)",
  "var(--color-red)",
  "var(--color-green)",
];

export const axisDefaults = {
  tick: { fill: "var(--color-muted)", fontSize: 11 },
  tickLine: false,
  axisLine: { stroke: "var(--color-line-strong)" },
  tickMargin: 8,
};

export const gridDefaults = {
  stroke: "var(--color-line)",
  strokeDasharray: "3 6",
  vertical: false,
};
