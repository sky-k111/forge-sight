import { useId } from "react";
import { Bar, CartesianGrid, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { axisDefaults, gridDefaults, seriesColors } from "./chartTheme";
import { ChartTooltip } from "./ChartTooltip";

export function ComboMetricChart({
  data,
  xKey,
  bars,
  lines,
  height = 260,
  xTickFormatter,
  leftUnit = "",
  rightUnit = "",
}) {
  const gradientPrefix = useId().replaceAll(":", "");
  const barSeries = bars.map((item, index) => ({
    ...item,
    color: item.color ?? seriesColors[index % seriesColors.length],
    gradientId: `${gradientPrefix}-combo-${item.key}`,
  }));
  const lineSeries = lines.map((item, index) => ({
    ...item,
    color: item.color ?? seriesColors[(barSeries.length + index) % seriesColors.length],
  }));
  const lineKeys = new Set(lineSeries.map((item) => item.key));
  const xInterval = data.length > 10 ? Math.max(1, Math.ceil(data.length / 7) - 1) : 0;
  const formatTooltip = (value, entry) => {
    const unit = lineKeys.has(entry.dataKey) ? rightUnit : leftUnit;
    return `${Number.isFinite(Number(value)) ? Number(value).toLocaleString() : value}${unit}`;
  };

  return (
    <div className="chart-frame chart-frame--elevated">
      <div className="chart-frame__backdrop" aria-hidden="true" />
      <div className="chart-frame__inner">
        <ResponsiveContainer width="100%" height={height}>
          <ComposedChart data={data} margin={{ top: 12, right: 4, bottom: 4, left: -4 }}>
            <defs>
              {barSeries.map((item) => (
                <linearGradient key={item.key} id={item.gradientId} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={item.color} stopOpacity="0.92" />
                  <stop offset="100%" stopColor={item.color} stopOpacity="0.5" />
                </linearGradient>
              ))}
            </defs>
            <CartesianGrid {...gridDefaults} />
            <XAxis dataKey={xKey} {...axisDefaults} interval={xInterval} minTickGap={12} tickFormatter={xTickFormatter} />
            <YAxis yAxisId="left" {...axisDefaults} tickFormatter={(value) => `${value}${leftUnit}`} />
            <YAxis yAxisId="right" orientation="right" {...axisDefaults} tickFormatter={(value) => `${value}${rightUnit}`} />
            <Tooltip content={<ChartTooltip formatter={formatTooltip} />} cursor={{ fill: "rgb(17 24 39 / 5%)" }} />
            {barSeries.map((item) => (
              <Bar
                key={item.key}
                yAxisId="left"
                dataKey={item.key}
                name={item.name}
                fill={`url(#${item.gradientId})`}
                stroke={item.color}
                strokeOpacity={0.22}
                barSize={20}
                radius={[6, 6, 0, 0]}
              />
            ))}
            {lineSeries.map((item) => (
              <Line
                key={item.key}
                yAxisId="right"
                type="monotone"
                dataKey={item.key}
                name={item.name}
                stroke={item.color}
                strokeWidth={2.2}
                dot={false}
                activeDot={{ r: 3.5, strokeWidth: 0 }}
              />
            ))}
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
