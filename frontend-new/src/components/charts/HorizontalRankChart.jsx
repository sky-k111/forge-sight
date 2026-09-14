import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { axisDefaults, gridDefaults } from "./chartTheme";
import { ChartTooltip } from "./ChartTooltip";

export function HorizontalRankChart({ data, height = 280, unit = "", color = "var(--color-primary)", valueName = "数值" }) {
  const chartHeight = Math.max(height, data.length * 36 + 52);
  const labelWidth = Math.min(
    148,
    Math.max(72, ...data.map((item) => String(item.label ?? "").length * 13)),
  );

  return (
    <div className="chart-frame chart-frame--elevated">
      <div className="chart-frame__backdrop" aria-hidden="true" />
      <div className="chart-frame__inner">
        <ResponsiveContainer width="100%" height={chartHeight}>
          <BarChart data={data} layout="vertical" margin={{ top: 8, right: 20, bottom: 4, left: 4 }}>
            <CartesianGrid {...gridDefaults} horizontal={false} vertical />
            <XAxis type="number" {...axisDefaults} />
            <YAxis type="category" dataKey="label" width={labelWidth} {...axisDefaults} />
            <Tooltip content={<ChartTooltip unit={unit} />} cursor={{ fill: "rgb(17 24 39 / 5%)" }} />
            <Bar dataKey="value" name={valueName} fill={color} radius={[0, 7, 7, 0]} barSize={18} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
