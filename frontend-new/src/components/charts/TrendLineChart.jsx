import { Area, AreaChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { axisDefaults, gridDefaults, numericAxisWidth, seriesColors } from "./chartTheme";
import { ChartTooltip } from "./ChartTooltip";

export function TrendLineChart({ data, xKey, series, height = 240, unit = "", area = false, yDomain, yTickFormatter, xTickFormatter }) {
  const Chart = area ? AreaChart : LineChart;
  const yAxisWidth = numericAxisWidth(data, series.map((item) => item.key), yTickFormatter);
  return (
    <div className="chart-frame">
      <div className="chart-frame__inner">
        <ResponsiveContainer width="100%" height={height}>
          <Chart data={data} margin={{ top: 12, right: 12, bottom: 4, left: 8 }}>
            <CartesianGrid {...gridDefaults} />
            <XAxis dataKey={xKey} {...axisDefaults} interval="preserveStartEnd" minTickGap={18} tickFormatter={xTickFormatter} />
            <YAxis {...axisDefaults} domain={yDomain} tickFormatter={yTickFormatter} width={yAxisWidth} />
            <Tooltip content={<ChartTooltip unit={unit} labelFormatter={xTickFormatter} />} cursor={{ stroke: "var(--color-line-strong)", strokeDasharray: "4 4" }} />
            {series.map((item, index) => {
              const color = item.color ?? seriesColors[index % seriesColors.length];
              return area ? (
                <Area key={item.key} type="monotone" dataKey={item.key} name={item.name} stroke={color} strokeWidth={2} fill={color} fillOpacity={0.1} dot={false} activeDot={{ r: 3 }} />
              ) : (
                <Line key={item.key} type="monotone" dataKey={item.key} name={item.name} stroke={color} strokeWidth={2} dot={false} activeDot={{ r: 3 }} />
              );
            })}
          </Chart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
