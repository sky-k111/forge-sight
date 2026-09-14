import cn from "../../lib/cn";

const WEEKDAYS = ["一", "二", "三", "四", "五", "六", "日"];

function parseDay(label) {
  const text = String(label ?? "").trim();
  const match = text.match(/(\d{4})[-/](\d{1,2})[-/](\d{1,2})/);
  if (!match) return null;
  const date = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
  return Number.isNaN(date.getTime()) ? null : date;
}

function toKey(date) {
  return `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`;
}

function hexToRgb(hex) {
  const value = hex.replace("#", "");
  return [parseInt(value.slice(0, 2), 16), parseInt(value.slice(2, 4), 16), parseInt(value.slice(4, 6), 16)];
}

function heatColor(baseHex, ratio) {
  const [r, g, b] = hexToRgb(baseHex);
  const t = Math.max(0, Math.min(1, ratio));
  const mix = (channel) => Math.round(channel * t + 245 * (1 - t));
  return `rgb(${mix(r)}, ${mix(g)}, ${mix(b)})`;
}

export function CalendarHeatmap({ data = [], unit = "", valueName = "数值", color = "#285d72", formatValue }) {
  const entries = data
    .map((item) => ({ date: parseDay(item.label), value: Number(item.value) || 0, label: item.label }))
    .filter((item) => item.date);
  if (entries.length < 2) {
    return <div className="grid min-h-24 place-content-center text-xs text-muted">日历热力需要至少两天的数据</div>;
  }
  entries.sort((a, b) => a.date - b.date);
  const values = entries.map((item) => item.value);
  const low = Math.min(...values);
  const high = Math.max(...values);
  const spread = high - low || 1;
  const byDay = new Map(entries.map((item) => [toKey(item.date), item]));

  const first = entries[0].date;
  const last = entries[entries.length - 1].date;
  const gridStart = new Date(first);
  gridStart.setDate(first.getDate() - ((first.getDay() + 6) % 7));
  const totalDays = Math.round((last - gridStart) / 86400000) + 1;
  const weeks = Math.ceil(totalDays / 7);

  const display = formatValue ?? ((value) => `${value.toLocaleString("zh-CN")}${unit === "%" ? "%" : unit ? ` ${unit}` : ""}`);
  const monthLabel = entries.length ? `${first.getFullYear()} 年 ${first.getMonth() + 1} 月` : "";
  const spansMonths = first.getMonth() !== last.getMonth() || first.getFullYear() !== last.getFullYear();
  const rangeLabel = spansMonths ? `${first.getMonth() + 1} 月至 ${last.getMonth() + 1} 月` : monthLabel;

  return (
    <div className="chart-frame">
      <div className="chart-frame__inner">
        <div className="flex items-center justify-between gap-2 pb-2">
          <span className="text-[10px] font-semibold text-muted">{rangeLabel}</span>
          <span className="flex items-center gap-1 text-[10px] text-faint">
            <span>低</span>
            <i className="inline-block h-2.5 w-2.5 rounded-[2px]" style={{ background: heatColor(color, 0.15) }} />
            <i className="inline-block h-2.5 w-2.5 rounded-[2px]" style={{ background: heatColor(color, 0.5) }} />
            <i className="inline-block h-2.5 w-2.5 rounded-[2px]" style={{ background: heatColor(color, 1) }} />
            <span>高</span>
          </span>
        </div>
        <div className="grid gap-1" style={{ gridTemplateColumns: "18px repeat(7, minmax(0, 1fr))" }}>
          <span />
          {WEEKDAYS.map((day) => <span key={day} className="text-center text-[9px] text-faint">{day}</span>)}
          {Array.from({ length: weeks }, (_, week) => {
            const rowDate = new Date(gridStart);
            rowDate.setDate(gridStart.getDate() + week * 7);
            return (
              <div key={week} className="contents">
                <span className="self-center truncate pr-1 text-right text-[9px] text-faint">{rowDate.getDate()}</span>
                {Array.from({ length: 7 }, (_, weekday) => {
                  const cellDate = new Date(gridStart);
                  cellDate.setDate(gridStart.getDate() + week * 7 + weekday);
                  const entry = byDay.get(toKey(cellDate));
                  if (!entry) return <span key={weekday} className="h-6 rounded-[3px] bg-surface-2/50" />;
                  const ratio = (entry.value - low) / spread;
                  return (
                    <span
                      key={weekday}
                      title={`${entry.label} · ${valueName} ${display(entry.value)}`}
                      className={cn("grid h-6 place-items-center rounded-[3px] font-mono text-[9px]", ratio > 0.6 ? "text-white" : "text-ink-2")}
                      style={{ background: heatColor(color, 0.12 + ratio * 0.88) }}
                    >
                      {cellDate.getDate()}
                    </span>
                  );
                })}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
