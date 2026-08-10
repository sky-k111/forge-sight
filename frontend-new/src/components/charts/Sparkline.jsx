// 迷你趋势线：统计卡片内的轻量趋势指示。纯 SVG、无坐标轴无交互，颜色取 currentColor。
export function Sparkline({ data = [], width = 72, height = 24, className = "" }) {
  if (data.length < 2) return null;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const span = max - min || 1;
  const step = width / (data.length - 1);
  const points = data.map((value, index) => [
    index * step,
    height - 2 - ((value - min) / span) * (height - 4),
  ]);
  const line = points.map((point, index) => `${index ? "L" : "M"}${point[0].toFixed(1)},${point[1].toFixed(1)}`).join(" ");
  return (
    <svg viewBox={`0 0 ${width} ${height}`} width={width} height={height} className={className} aria-hidden="true">
      <path d={`${line} L${width},${height} L0,${height} Z`} fill="currentColor" opacity={0.08} stroke="none" />
      <path d={line} fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" />
      <circle cx={points.at(-1)[0]} cy={points.at(-1)[1]} r={2} fill="currentColor" stroke="none" />
    </svg>
  );
}
