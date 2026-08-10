import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";
import { seriesColors } from "./chartTheme";
import { ChartTooltip } from "./ChartTooltip";

export function ShareDonutChart({ data, height = 220, unit = "", centerTitle, centerValue }) {
  return (
    <div className="relative">
      <ResponsiveContainer width="100%" height={height}>
        <PieChart>
          <Pie data={data} dataKey="value" nameKey="name" innerRadius="62%" outerRadius="86%" paddingAngle={2} strokeWidth={0}>
            {data.map((entry, index) => (
              <Cell key={entry.name} fill={entry.color ?? seriesColors[index % seriesColors.length]} />
            ))}
          </Pie>
          <Tooltip content={<ChartTooltip unit={unit} />} />
        </PieChart>
      </ResponsiveContainer>
      {centerValue != null && (
        <div className="pointer-events-none absolute inset-0 grid place-content-center text-center">
          <strong className="font-mono text-lg font-semibold tracking-tight text-ink">{centerValue}</strong>
          {centerTitle && <span className="mt-0.5 text-[11px] text-faint">{centerTitle}</span>}
        </div>
      )}
    </div>
  );
}
