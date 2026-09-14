import cn from "../../lib/cn";

function formatValue(value, unit = "") {
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  const text = number.toLocaleString("zh-CN", { maximumFractionDigits: 2 });
  return unit === "%" ? `${text}%` : `${text}${unit ? ` ${unit}` : ""}`;
}

export function DataTableBars({ data = [], unit = "", valueName = "数值", categoryName = "项目", color = "var(--color-primary)", height = 240, topN }) {
  const rows = (typeof topN === "number" ? data.slice(0, topN) : data).map((item) => ({
    label: item.label ?? item.name ?? "—",
    value: Number(item.value) || 0,
  }));
  const total = rows.reduce((sum, item) => sum + Math.max(item.value, 0), 0);
  const max = Math.max(...rows.map((item) => Math.abs(item.value)), 0) || 1;
  let cumulative = 0;
  const enriched = rows.map((item) => {
    const share = total ? (Math.max(item.value, 0) / total) * 100 : 0;
    cumulative += share;
    return { ...item, share, cumulative: Math.min(cumulative, 100) };
  });

  return (
    <div className="chart-frame">
      <div className="chart-frame__inner overflow-auto" style={{ maxHeight: height + 40 }}>
        <table className="w-full border-collapse text-xs">
          <thead>
            <tr className="border-b border-line text-left text-[10px] text-faint">
              <th className="w-7 py-1.5 pl-1 font-semibold">#</th>
              <th className="py-1.5 font-semibold">{categoryName}</th>
              <th className="w-[34%] py-1.5 font-semibold">{valueName}</th>
              <th className="w-20 py-1.5 text-right font-semibold">占比</th>
              <th className="w-20 py-1.5 pr-1 text-right font-semibold">累计</th>
            </tr>
          </thead>
          <tbody>
            {enriched.map((item, index) => (
              <tr key={`${item.label}-${index}`} className="border-b border-line/60 last:border-0">
                <td className="py-1.5 pl-1 font-mono text-[10px] text-faint">{index + 1}</td>
                <td className="max-w-0 truncate py-1.5 pr-2 text-ink">{item.label}</td>
                <td className="py-1.5 pr-2">
                  <div className="flex items-center gap-2">
                    <span className="h-2.5 shrink-0 rounded-[2px] transition-[width]" style={{ width: `${Math.max((Math.abs(item.value) / max) * 100, 2)}%`, maxWidth: "60%", background: color, opacity: 0.85 }} />
                    <span className="shrink-0 font-mono text-[11px] text-ink-2">{formatValue(item.value, unit)}</span>
                  </div>
                </td>
                <td className="py-1.5 text-right font-mono text-[11px] text-muted">{item.share.toFixed(1)}%</td>
                <td className={cn("py-1.5 pr-1 text-right font-mono text-[11px]", item.cumulative <= 80 ? "text-primary" : "text-faint")}>{item.cumulative.toFixed(1)}%</td>
              </tr>
            ))}
            {!enriched.length && (
              <tr><td colSpan={5} className="py-6 text-center text-xs text-muted">当前区间没有匹配记录</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
