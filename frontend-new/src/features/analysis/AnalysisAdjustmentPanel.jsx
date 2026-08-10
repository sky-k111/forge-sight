import { useEffect, useMemo, useState } from "react";
import Icon from "../../components/ui/Icon";
import { Button } from "../../components/ui";
import cn from "../../lib/cn";

const TIME_OPTIONS = [
  { value: "", label: "保持原范围" },
  { value: "最近 7 天", label: "最近 7 天" },
  { value: "最近 30 天", label: "最近 30 天" },
  { value: "本月至今", label: "本月至今" },
  { value: "custom", label: "自定义" },
];

const CHART_OPTIONS = [
  { value: "", label: "自动选择", icon: "chart" },
  { value: "折线图", label: "折线图", icon: "insight" },
  { value: "柱状图", label: "柱状图", icon: "chart" },
  { value: "环形图", label: "环形图", icon: "overview" },
  { value: "散点图", label: "散点图", icon: "analysis" },
  { value: "组合图", label: "组合图", icon: "model" },
  { value: "热力图", label: "热力图", icon: "scan" },
];

export function buildAdjustmentInstruction(sourceQuestion, adjustments) {
  const clauses = [];
  if (adjustments.timeRange) clauses.push(`时间范围调整为${adjustments.timeRange}`);
  if (adjustments.objectScope) clauses.push(`分析对象限定为${adjustments.objectScope}`);
  if (adjustments.metric) clauses.push(`指标口径调整为${adjustments.metric}`);
  if (adjustments.groupBy) clauses.push(`按${adjustments.groupBy}分组`);
  if (adjustments.chartType) clauses.push(`使用${adjustments.chartType}展示`);
  if (adjustments.note) clauses.push(adjustments.note);
  const changeText = clauses.length ? clauses.join("，") : "按原条件重新分析";
  return `基于原分析“${sourceQuestion}”，${changeText}。保留未调整条件并生成独立分析方案。`;
}

export default function AnalysisAdjustmentPanel({
  open,
  sourceQuestion,
  checkpointLabel,
  submitting = false,
  onClose,
  onSubmit,
}) {
  const [timeChoice, setTimeChoice] = useState("");
  const [customTime, setCustomTime] = useState("");
  const [objectScope, setObjectScope] = useState("");
  const [metric, setMetric] = useState("");
  const [groupBy, setGroupBy] = useState("");
  const [chartType, setChartType] = useState("");
  const [note, setNote] = useState("");

  useEffect(() => {
    if (!open) return;
    setTimeChoice("");
    setCustomTime("");
    setObjectScope("");
    setMetric("");
    setGroupBy("");
    setChartType("");
    setNote("");
  }, [open, sourceQuestion, checkpointLabel]);

  const adjustments = useMemo(() => ({
    timeRange: timeChoice === "custom" ? customTime.trim() : timeChoice,
    objectScope: objectScope.trim(),
    metric: metric.trim(),
    groupBy: groupBy.trim(),
    chartType,
    note: note.trim(),
  }), [chartType, customTime, groupBy, metric, note, objectScope, timeChoice]);

  const changed = Object.values(adjustments).some(Boolean);
  const customTimeMissing = timeChoice === "custom" && !customTime.trim();

  if (!open) return null;

  const submit = () => {
    if (!changed || customTimeMissing || submitting) return;
    const normalized = Object.fromEntries(Object.entries(adjustments).filter(([, value]) => value));
    const onlyPresentationChanged = Object.keys(normalized).every((key) => ["chartType", "note"].includes(key));
    onSubmit({
      adjustments: normalized,
      instruction: buildAdjustmentInstruction(sourceQuestion, normalized),
      reuseArtifacts: onlyPresentationChanged ? "snapshot" : "refresh",
    });
  };

  return (
    <div className="fixed inset-0 z-[80] flex justify-end" role="dialog" aria-modal="true" aria-label="调整分析条件">
      <button type="button" aria-label="关闭调整面板" onClick={onClose} className="absolute inset-0 cursor-default bg-ink/15 backdrop-blur-[1px]" />
      <aside className="relative flex h-full w-[430px] max-w-[94vw] flex-col border-l border-line bg-surface shadow-[-18px_0_48px_rgb(24_34_45/12%)] motion-soft-rise">
        <header className="flex items-start justify-between gap-4 border-b border-line px-5 py-4">
          <div className="min-w-0">
            <span className="text-[10px] font-semibold tracking-wide text-primary">新分析方案</span>
            <h2 className="mt-1 text-[17px] font-semibold tracking-tight text-ink">调整分析条件</h2>
            <p className="mt-1 text-xs leading-relaxed text-muted">原分析保持不变，仅对你修改的条件重新计算。</p>
          </div>
          <button type="button" onClick={onClose} aria-label="关闭" className="grid h-8 w-8 shrink-0 cursor-pointer place-items-center rounded-control text-muted transition-colors hover:bg-surface-2 hover:text-ink">
            <Icon name="close" size={17} />
          </button>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
          <section className="border-l-2 border-primary/45 pl-3">
            <span className="text-[10px] font-semibold text-faint">原分析</span>
            <p className="mt-1 text-xs leading-relaxed text-ink">{sourceQuestion}</p>
            <span className="mt-1.5 block text-[10px] text-muted">从“{checkpointLabel}”调整</span>
          </section>

          <FieldSection title="时间范围" description="不选择时沿用原分析">
            <div className="flex flex-wrap gap-1.5">
              {TIME_OPTIONS.map((option) => (
                <button
                  type="button"
                  key={option.value}
                  onClick={() => setTimeChoice(option.value)}
                  className={cn(
                    "cursor-pointer rounded-full border px-2.5 py-1.5 text-xs transition-colors",
                    timeChoice === option.value
                      ? "border-primary/35 bg-primary-soft font-semibold text-primary"
                      : "border-line bg-surface text-ink-2 hover:border-line-strong hover:bg-surface-2",
                  )}
                >
                  {option.label}
                </button>
              ))}
            </div>
            {timeChoice === "custom" && (
              <input
                autoFocus
                value={customTime}
                onChange={(event) => setCustomTime(event.target.value)}
                placeholder="例如：2026-07-01 至 2026-07-15"
                className="mt-2 h-9 w-full rounded-control border border-line bg-surface px-3 text-xs text-ink outline-none transition placeholder:text-faint focus:border-primary/45 focus:ring-2 focus:ring-primary/10"
              />
            )}
          </FieldSection>

          <FieldSection title="业务条件" description="只填写需要变化的内容">
            <div className="grid gap-2">
              <CompactInput label="分析对象" value={objectScope} onChange={setObjectScope} placeholder="例如：L03 产线、传感器产品" />
              <CompactInput label="指标口径" value={metric} onChange={setMetric} placeholder="例如：工序不良率、最终合格率" />
              <CompactInput label="分组维度" value={groupBy} onChange={setGroupBy} placeholder="例如：产线、班次、产品类别" />
            </div>
          </FieldSection>

          <FieldSection title="展示方式" description="仅换图表时直接复用原查询结果">
            <div className="grid grid-cols-3 gap-1.5">
              {CHART_OPTIONS.map((option) => (
                <button
                  type="button"
                  key={option.value}
                  onClick={() => setChartType(option.value)}
                  className={cn(
                    "flex h-10 cursor-pointer items-center gap-2 rounded-control border px-2.5 text-left text-xs transition-colors",
                    chartType === option.value
                      ? "border-primary/35 bg-primary-soft font-semibold text-primary"
                      : "border-line bg-surface text-ink-2 hover:border-line-strong hover:bg-surface-2",
                  )}
                >
                  <Icon name={option.icon} size={14} />
                  <span>{option.label}</span>
                </button>
              ))}
            </div>
          </FieldSection>

          <FieldSection title="补充说明" description="可选">
            <textarea
              value={note}
              onChange={(event) => setNote(event.target.value)}
              rows={3}
              placeholder="例如：重点解释异常波动，并给出可能原因"
              className="w-full resize-none rounded-control border border-line bg-surface px-3 py-2.5 text-xs leading-relaxed text-ink outline-none transition placeholder:text-faint focus:border-primary/45 focus:ring-2 focus:ring-primary/10"
            />
          </FieldSection>
        </div>

        <footer className="border-t border-line px-5 py-3.5">
          <div className="flex items-center justify-between gap-3">
            <span className="text-[10px] leading-relaxed text-muted">
              {chartType && !timeChoice && !objectScope && !metric && !groupBy
                ? "将复用原结果快照，不重新查询数据库"
                : "涉及业务条件变化时将重新查询最新数据"}
            </span>
            <div className="flex gap-2">
              <Button variant="ghost" onClick={onClose}>取消</Button>
              <Button variant="primary" onClick={submit} disabled={!changed || customTimeMissing || submitting}>
                {submitting ? <Icon name="refresh" size={14} /> : <Icon name="arrow" size={14} />}
                {submitting ? "正在创建" : "生成新方案"}
              </Button>
            </div>
          </div>
        </footer>
      </aside>
    </div>
  );
}

function FieldSection({ title, description, children }) {
  return (
    <section className="mt-5 border-t border-line pt-4 first:border-0">
      <div className="mb-2.5 flex items-baseline justify-between gap-3">
        <h3 className="text-xs font-semibold text-ink">{title}</h3>
        <span className="text-[10px] text-faint">{description}</span>
      </div>
      {children}
    </section>
  );
}

function CompactInput({ label, value, onChange, placeholder }) {
  return (
    <label className="grid grid-cols-[72px_minmax(0,1fr)] items-center rounded-control border border-line bg-surface px-3 transition focus-within:border-primary/45 focus-within:ring-2 focus-within:ring-primary/10">
      <span className="text-[11px] font-semibold text-ink-2">{label}</span>
      <input
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        className="h-9 min-w-0 border-0 bg-transparent text-xs text-ink outline-none placeholder:text-faint"
      />
    </label>
  );
}
