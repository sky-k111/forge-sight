import { CartesianGrid, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis } from "recharts";
import { axisDefaults, gridDefaults, numericAxisWidth, seriesColors } from "./chartTheme";
import { ChartTooltip } from "./ChartTooltip";

export function CorrelationScatterChart({ data, xKey, yKey, xName, yName, height = 260, xUnit = "", yUnit = "" }) {
  const yAxisWidth = numericAxisWidth(data, [yKey], (value) => `${value}${yUnit}`);
  return (
    <ResponsiveContainer width="100%" height={height}>
      <ScatterChart margin={{ top: 8, right: 8, bottom: 0, left: 8 }}>
        <CartesianGrid {...gridDefaults} vertical />
        <XAxis type="number" dataKey={xKey} name={xName} {...axisDefaults} unit={xUnit} />
        <YAxis type="number" dataKey={yKey} name={yName} {...axisDefaults} unit={yUnit} width={yAxisWidth} />
        <Tooltip content={<ChartTooltip />} cursor={{ stroke: "var(--color-line-strong)", strokeDasharray: "4 4" }} />
        <Scatter data={data} fill={seriesColors[0]} fillOpacity={0.7} />
      </ScatterChart>
    </ResponsiveContainer>
  );
}
