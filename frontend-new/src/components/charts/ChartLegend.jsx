import { seriesColors } from "./chartTheme";

export function ChartLegend({ items, className = "" }) {
  return (
    <div className={`flex flex-wrap items-center gap-x-4 gap-y-1.5 text-[11px] text-muted ${className}`}>
      {items.map((item, index) => {
        const color = item.color ?? seriesColors[index % seriesColors.length];
        return (
          <span key={item.label} className="inline-flex items-center gap-1.5">
            <i aria-hidden="true" data-chart-legend-color={color} className="h-[7px] w-[7px] rounded-full" style={{ background: color }} />
            {item.label}
          </span>
        );
      })}
    </div>
  );
}
