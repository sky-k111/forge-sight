import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { axisDefaults, gridDefaults, numericAxisWidth } from "../../components/charts/chartTheme";
import { formatNumber, numeric } from "./alertPresentation";

function EvidenceTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="monitoring-chart-tooltip">
      <strong>{label}</strong>
      {payload.map((entry) => (
        <div key={entry.dataKey}>
          <i style={{ background: entry.color }} />
          <span>{entry.name}</span>
          <b>{formatNumber(entry.value)}</b>
        </div>
      ))}
    </div>
  );
}
export default function MonitoringEvidenceChart({ items, rule }) {
  const numericRows = items.filter(
    (item) => item.currentValue !== null || item.baselineValue !== null
  );
  if (!numericRows.length)
    return (
      <div className="monitoring-chart-empty" role="status">
        暂无可绘制的数值证据，缺失值不会按 0 展示。
      </div>
    );
  const fixed = !rule?.condition?.baselineMethod;
  const open = fixed ? numeric(rule?.condition?.openThreshold) : null;
  const close = fixed ? numeric(rule?.condition?.closeThreshold) : null;
  const axisRows = [...items, { currentValue: open, baselineValue: close }];
  return (
    <div className="monitoring-evidence-chart" role="img" aria-label="异常指标历史趋势">
      <div className="monitoring-chart-legend">
        <span>
          <i />
          观测值
        </span>
        <span>
          <i className="is-baseline" />
          历史基线
        </span>
        {open !== null && (
          <span>
            <i className="is-trigger" />
            触发阈值 {formatNumber(open)}
          </span>
        )}
        {close !== null && (
          <span>
            <i className="is-recovery" />
            恢复阈值 {formatNumber(close)}
          </span>
        )}
      </div>
      <ResponsiveContainer width="100%" height={270} minWidth={0}>
        <LineChart
          data={items}
          margin={{ top: 20, right: 18, left: 0, bottom: 8 }}
          accessibilityLayer
        >
          <CartesianGrid {...gridDefaults} />
          <XAxis
            dataKey="logicalPeriod"
            {...axisDefaults}
            minTickGap={25}
            interval="preserveStartEnd"
            tickFormatter={(value) => String(value).replace(/^\d{4}-/, "")}
          />
          <YAxis
            {...axisDefaults}
            width={numericAxisWidth(axisRows, ["currentValue", "baselineValue"], formatNumber)}
            domain={["auto", "auto"]}
            tickFormatter={formatNumber}
          />
          <Tooltip
            content={<EvidenceTooltip />}
            cursor={{ stroke: "var(--color-line-strong)", strokeDasharray: "4 4" }}
          />
          {open !== null && (
            <ReferenceLine
              y={open}
              stroke="var(--color-red)"
              strokeDasharray="4 4"
              ifOverflow="extendDomain"
            />
          )}
          {close !== null && (
            <ReferenceLine
              y={close}
              stroke="var(--color-teal)"
              strokeDasharray="4 4"
              ifOverflow="extendDomain"
            />
          )}
          <Line
            type="linear"
            name="历史基线"
            dataKey="baselineValue"
            stroke="var(--color-faint)"
            strokeDasharray="5 5"
            strokeWidth={1.5}
            dot={{ r: 2 }}
            connectNulls={false}
            isAnimationActive={false}
          />
          <Line
            type="linear"
            name="观测值"
            dataKey="currentValue"
            stroke="var(--color-primary)"
            strokeWidth={2}
            dot={{ r: 3, fill: "var(--color-surface)", strokeWidth: 2 }}
            activeDot={{ r: 5 }}
            connectNulls={false}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
      <p className="monitoring-chart-caption">
        仅显示该异常关联的观测记录，不代表完整生产历史；缺失值保留为空。
        {!fixed && "动态规则的控制上下界未由此接口提供，不在图中推算。"}
      </p>
    </div>
  );
}
