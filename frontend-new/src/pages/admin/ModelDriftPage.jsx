import { useEffect, useState } from "react";
import { useOutletContext } from "react-router-dom";
import Icon from "../../components/ui/Icon";
import cn from "../../lib/cn";
import {
  Button,
  ErrorNotice,
  PageFrame,
  RouteSkeleton,
  StatusPill,
} from "../../components/ui";
import { getDriftReport } from "../../api/modeling";

const statusMeta = {
  drift: { tone: "red", label: "漂移告警" },
  watch: { tone: "amber", label: "观察" },
  normal: { tone: "green", label: "正常" },
  pending: { tone: "gray", label: "待回填" },
};

export default function ModelDriftPage() {
  const { navigatePage } = useOutletContext();
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  const load = () => {
    setError(null);
    setData(null);
    getDriftReport().then(setData).catch(setError);
  };
  useEffect(load, []);

  if (error) {
    return (
      <PageFrame>
        <ErrorNotice title="偏差监控加载失败" detail="请检查数据连接后重试。" onRetry={load} />
      </PageFrame>
    );
  }
  if (!data) return <RouteSkeleton />;

  const items = data.items ?? [];
  const summary = data.summary ?? { models: 0, drift: 0, watch: 0, pending: 0 };
  const thresholds = data.thresholds ?? { watch: 10, drift: 25 };

  return (
    <PageFrame className="max-w-[1360px] gap-4">
      <header className="flex h-12 shrink-0 flex-wrap items-center gap-2 border-b border-line px-1">
        <h2 className="text-sm font-semibold tracking-tight text-ink">偏差监控</h2>
        <span className="hidden h-4 w-px bg-line sm:block" aria-hidden="true" />
        <p className="hidden min-w-0 flex-1 truncate text-[11px] text-muted md:block">
          预测留痕自动回填实际值后的模型健康度 · 观察 ≥ {thresholds.watch}% · 告警 ≥ {thresholds.drift}%
        </p>
        <div className="ml-auto flex items-center gap-1.5">
          <Button size="sm" variant="ghost" onClick={load}><Icon name="refresh" size={13} />重新回填</Button>
          <Button size="sm" onClick={() => navigatePage("model-governance")}>去复核发布</Button>
        </div>
      </header>

      <div className="grid grid-cols-2 divide-y divide-line overflow-hidden rounded-panel border border-line bg-paper sm:grid-cols-4 sm:divide-y-0 sm:divide-x">
        <div className="px-4 py-3"><span className="block text-[11px] text-muted">监控模型</span><strong className="mt-1 block font-mono text-base font-semibold leading-none text-ink">{summary.models}</strong></div>
        <div className="px-4 py-3"><span className="block text-[11px] text-muted">漂移告警</span><strong className={cn("mt-1 block font-mono text-base font-semibold leading-none", summary.drift ? "text-red" : "text-ink")}>{summary.drift}</strong></div>
        <div className="px-4 py-3"><span className="block text-[11px] text-muted">观察中</span><strong className="mt-1 block font-mono text-base font-semibold leading-none text-amber">{summary.watch}</strong></div>
        <div className="px-4 py-3"><span className="block text-[11px] text-muted">待回填</span><strong className="mt-1 block font-mono text-base font-semibold leading-none text-ink">{summary.pending}</strong></div>
      </div>

      <section className="overflow-hidden rounded-panel border border-line bg-surface">
        <header className="flex items-center justify-between gap-3 border-b border-line px-4 py-3">
          <h3 className="text-sm font-semibold text-ink">模型偏差报表</h3>
          <span className="font-mono text-[11px] text-faint">实际值由系统按预测窗口自动回填</span>
        </header>
        {items.length ? (
          <div>
            <div className="grid grid-cols-[minmax(0,1.4fr)_0.9fr_0.7fr_1.2fr_0.8fr_auto] gap-3 px-4 py-2.5 text-[11px] font-semibold text-muted">
              <span>模型</span><span>指标口径</span><span>留痕/回填</span><span>平均偏差</span><span>最近预测</span><span>状态</span>
            </div>
            {items.map((item) => {
              const meta = statusMeta[item.status] ?? statusMeta.pending;
              const width = item.avgDeviation == null ? 0 : Math.min(100, item.avgDeviation);
              return (
                <div key={item.modelId} className="grid grid-cols-[minmax(0,1.4fr)_0.9fr_0.7fr_1.2fr_0.8fr_auto] items-center gap-3 border-t border-line px-4 py-2.5 text-xs text-ink-2">
                  <span className="min-w-0">
                    <strong className="block truncate font-semibold text-ink">{item.modelName} <span className="font-mono text-[10px] text-faint">v{item.modelVersion}</span></strong>
                    <small className="mt-0.5 block truncate text-[11px] text-faint">任务：{item.taskTitle}</small>
                  </span>
                  <span className="truncate">{item.metricKind === "mean" ? "预测均值 vs 实际均值" : "预测占比 vs 实际占比"}</span>
                  <span className="font-mono text-[11px]">{item.filled} / {item.traces}</span>
                  <span className="flex items-center gap-2">
                    <span className="h-1.5 min-w-0 flex-1 overflow-hidden rounded-full bg-surface-3">
                      <i className={cn("block h-full rounded-full", item.status === "drift" ? "bg-red" : item.status === "watch" ? "bg-amber" : "bg-teal")} style={{ width: `${width}%` }} />
                    </span>
                    <b className="shrink-0 font-mono text-[11px] text-ink">{item.avgDeviation == null ? "—" : `${item.avgDeviation}%`}</b>
                  </span>
                  <span className="font-mono text-[11px] text-faint">{item.lastPredictedAt}</span>
                  <StatusPill tone={meta.tone}>{meta.label}</StatusPill>
                </div>
              );
            })}
          </div>
        ) : (
          <div className="grid place-content-center gap-1 px-6 py-12 text-center">
            <p className="text-sm font-semibold text-ink">还没有预测留痕</p>
            <p className="max-w-md text-[11px] leading-relaxed text-muted">模型发布并被调用后，系统会记录每次聚合预测；实际值产生后自动回填并计算偏差。</p>
            <button type="button" onClick={() => navigatePage("modeling")} className="mt-2 text-xs font-semibold text-primary hover:opacity-75">去建模工作台 →</button>
          </div>
        )}
      </section>
    </PageFrame>
  );
}
