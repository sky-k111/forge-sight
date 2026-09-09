export function ChartTooltip({ active, payload, label, unit = "", formatter, labelFormatter }) {
  if (!active || !payload?.length) return null;
  const displayLabel = labelFormatter && label != null ? labelFormatter(label) : label;
  return (
    <div className="min-w-36 rounded-card border border-line-strong/70 bg-white/95 px-3 py-2.5 shadow-[0_14px_32px_rgb(24_34_45_/8%)] backdrop-blur-sm">
      {displayLabel != null && displayLabel !== "" && <div className="mb-1.5 text-[11px] font-semibold text-ink-2">{displayLabel}</div>}
      <div className="grid gap-1">
        {payload.map((entry) => (
          <div key={`${entry.dataKey}-${entry.name}`} className="flex items-center gap-2 text-xs">
            <span className="h-2 w-2 shrink-0 rounded-full ring-2 ring-white/85" style={{ background: entry.color ?? entry.payload?.fill }} />
            <span className="text-muted">{entry.name}</span>
            <span className="ml-auto pl-3 font-mono font-semibold text-ink">
              {formatter ? formatter(entry.value, entry) : `${typeof entry.value === "number" ? entry.value.toLocaleString() : entry.value}${unit}`}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
