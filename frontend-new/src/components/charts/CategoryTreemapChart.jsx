import { ResponsiveContainer, Tooltip, Treemap } from "recharts";

import { seriesColors } from "./chartTheme";
import { ChartTooltip } from "./ChartTooltip";

function TreemapCell({ x, y, width, height, name, value, index = 0, color }) {
  if (width < 2 || height < 2) return null;
  const fill = color ?? seriesColors[index % seriesColors.length];
  const showName = width > 74 && height > 34;
  const showValue = width > 88 && height > 54;
  return (
    <g>
      <rect x={x} y={y} width={width} height={height} rx="5" ry="5" fill={fill} fillOpacity="0.84" stroke="var(--color-surface)" strokeWidth="3" />
      {showName && <text x={x + 9} y={y + 19} fill="white" fontSize="11" fontWeight="650">{String(name).slice(0, Math.max(5, Math.floor(width / 12)))}</text>}
      {showValue && <text x={x + 9} y={y + 38} fill="white" fillOpacity="0.82" fontSize="10">{Number(value).toLocaleString("zh-CN")}</text>}
    </g>
  );
}

export function CategoryTreemapChart({ data, height = 220, unit = "" }) {
  return (
    <div className="chart-frame chart-frame--elevated">
      <div className="chart-frame__backdrop" aria-hidden="true" />
      <div className="chart-frame__inner">
        <ResponsiveContainer width="100%" height={height}>
          <Treemap data={data} dataKey="value" nameKey="name" aspectRatio={4 / 3} content={<TreemapCell />}>
            <Tooltip content={<ChartTooltip unit={unit} />} />
          </Treemap>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
