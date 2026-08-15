function formatValue(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number.toLocaleString("zh-CN", { maximumFractionDigits: 2 }) : String(value ?? "—");
}

export function MatrixHeatmapChart({ rows, columns, cells, rowName, columnName, valueName }) {
  const values = cells.map((cell) => Number(cell.value)).filter(Number.isFinite);
  const min = values.length ? Math.min(...values) : 0;
  const max = values.length ? Math.max(...values) : 0;
  const byKey = new Map(cells.map((cell) => [`${cell.row}\u0000${cell.column}`, cell]));
  const intensity = (value) => {
    const number = Number(value);
    if (!Number.isFinite(number)) return 0;
    if (max === min) return max === 0 ? 0 : 0.72;
    return 0.14 + ((number - min) / (max - min)) * 0.78;
  };
  const gridTemplateColumns = `minmax(108px, 0.8fr) repeat(${columns.length}, minmax(72px, 1fr))`;

  return (
    <div className="chart-frame chart-frame--elevated">
      <div className="overflow-x-auto p-3">
        <div className="min-w-max" role="table" aria-label={`${rowName}与${columnName}的${valueName}热力图`}>
          <div className="grid gap-1.5" style={{ gridTemplateColumns }}>
            <div className="flex items-end px-2 pb-1 text-xs font-semibold text-faint">
              {rowName} × {columnName}
            </div>
            {columns.map((column) => (
              <div key={column} className="truncate px-1 pb-1 text-center text-xs font-medium text-muted" title={column}>
                {column}
              </div>
            ))}
            {rows.map((row) => (
              <HeatmapRow
                key={row}
                row={row}
                columns={columns}
                byKey={byKey}
                intensity={intensity}
                valueName={valueName}
              />
            ))}
          </div>
          <div className="mt-3 flex items-center justify-end gap-2 text-xs text-faint">
            <span>低</span>
            <span
              className="h-2 w-28 rounded-full border border-line"
              style={{ background: "linear-gradient(90deg, color-mix(in srgb, var(--color-primary) 12%, white), var(--color-primary))" }}
            />
            <span>高</span>
            <span className="ml-2 font-mono">{formatValue(min)}–{formatValue(max)}</span>
          </div>
        </div>
      </div>
    </div>
  );
}

function HeatmapRow({ row, columns, byKey, intensity, valueName }) {
  return (
    <>
      <div className="flex items-center truncate px-2 text-xs font-medium text-ink-2" title={row}>{row}</div>
      {columns.map((column) => {
        const cell = byKey.get(`${row}\u0000${column}`);
        const alpha = intensity(cell?.value);
        const readable = alpha > 0.58;
        return (
          <div
            key={`${row}-${column}`}
            role="cell"
            title={`${row} / ${column}：${formatValue(cell?.value)} ${valueName}`}
            className="grid min-h-10 place-items-center rounded-control border border-line/60 px-2 font-mono text-xs transition-transform hover:relative hover:z-10 hover:scale-[1.04] hover:border-primary/35"
            style={{
              backgroundColor: alpha
                ? `color-mix(in srgb, var(--color-primary) ${Math.round(alpha * 100)}%, var(--color-surface))`
                : "var(--color-surface-2)",
              color: readable ? "white" : "var(--color-ink-2)",
            }}
          >
            {cell ? formatValue(cell.value) : "—"}
          </div>
        );
      })}
    </>
  );
}
