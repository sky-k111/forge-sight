import { useId } from "react";
import { Bar, CartesianGrid, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { axisDefaults, gridDefaults, seriesColors } from "./chartTheme";
import { ChartTooltip } from "./ChartTooltip";

export function RankBarChart({ data, xKey, bars, lines = [], height = 240, unit = "", yDomain, yTickFormatter, xTickFormatter, barSize = 22 }) {
  const gradientPrefix = useId().replaceAll(":", "");
  const barSeries = bars.map((item, index) => ({
    ...item,
    color: item.color ?? seriesColors[index % seriesColors.length],
    gradientId: `${gradientPrefix}-rank-bar-${item.key}`,
  }));
  const lineSeries = lines.map((item, index) => ({
    ...item,
    color: item.color ?? seriesColors[(bars.length + index) % seriesColors.length],
  }));
  const xInterval = data.length > 8 ? Math.max(1, Math.ceil(data.length / 6) - 1) : 0;

  return (
    <div className="chart-frame chart-frame--elevated" data-chart-series-colors={barSeries.map((item) => item.color).join(",")}>
      <div className="chart-frame__backdrop" aria-hidden="true" />
      <div className="chart-frame__inner">
        <ResponsiveContainer width="100%" height={height}>
          <ComposedChart data={data} margin={{ top: 12, right: 12, bottom: 4, left: -4 }}>
            <defs>
              {barSeries.map((item) => {
                return (
                  <linearGradient key={item.key} id={item.gradientId} x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor={item.color} stopOpacity="0.96" />
                    <stop offset="58%" stopColor={item.color} stopOpacity="0.78" />
                    <stop offset="100%" stopColor={item.color} stopOpacity="0.46" />
                  </linearGradient>
                );
              })}
            </defs>
            <CartesianGrid {...gridDefaults} />
            <XAxis dataKey={xKey} {...axisDefaults} interval={xInterval} tickFormatter={xTickFormatter} minTickGap={12} />
            <YAxis {...axisDefaults} domain={yDomain} tickFormatter={yTickFormatter} />
            <Tooltip content={<ChartTooltip unit={unit} />} cursor={{ fill: "rgb(17 24 39 / 6%)", opacity: 1 }} />
            {barSeries.map((item) => (
              <Bar
                key={item.key}
                dataKey={item.key}
                name={item.name}
                fill={`url(#${item.gradientId})`}
                stroke={item.color}
                strokeOpacity={0.2}
                strokeWidth={1}
                barSize={barSize}
                radius={[7, 7, 0, 0]}
              />
            ))}
            {lineSeries.map((item) => (
              <Line
                key={item.key}
                type="monotone"
                dataKey={item.key}
                name={item.name}
                stroke={item.color}
                strokeWidth={1.8}
                strokeDasharray="6 5"
                dot={false}
                activeDot={{ r: 3.5, strokeWidth: 0, fill: item.color }}
              />
            ))}
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
