import Icon from "../ui/Icon";
import { StatusPill } from "../ui/StatusPill";
import cn from "../../lib/cn";

const kindMeta = {
  prediction: { icon: "chart", label: "预测结果" },
  group: { icon: "knowledge", label: "自动分组" },
  anomaly: { icon: "alert", label: "异常识别" },
};

/**
 * 模型结果卡：预测 / 分组 / 异常三类共用外壳。
 * 必带依据：模型版本、数据范围、主要影响因素；个人档模型必须显式标注未经审核。
 */
export function PredictionResultCard({ card }) {
  if (!card) return null;
  const meta = kindMeta[card.kind] ?? kindMeta.prediction;
  const personal = card.scope === "personal";
  return (
    <section className="mt-3 overflow-hidden rounded-panel border border-line bg-surface-2/60" aria-label={`${meta.label}卡`}>
      <header className="flex flex-wrap items-center gap-2 border-b border-line px-3.5 py-2.5">
        <span className="grid h-6 w-6 shrink-0 place-items-center rounded-tag bg-primary-soft text-primary"><Icon name={meta.icon} size={13} /></span>
        <strong className="min-w-0 flex-1 truncate text-xs font-semibold text-ink">{meta.label} · {card.headline}</strong>
        <StatusPill tone={personal ? "amber" : "teal"}>{personal ? "个人模型 · 未经审核" : `已发布 v${card.modelVersion}`}</StatusPill>
      </header>
      <div className="grid gap-x-6 gap-y-2 px-3.5 py-3 text-[11px] sm:grid-cols-2">
        {card.kind === "prediction" && card.stats?.mean != null && (
          <>
            <Stat label="预测均值" value={card.stats.mean} />
            <Stat label="范围" value={`${card.stats.min} ~ ${card.stats.max}`} />
          </>
        )}
        {card.kind === "prediction" && card.stats?.share != null && (
          <>
            <Stat label="判定数量" value={card.stats.positive} />
            <Stat label="占比" value={`${card.stats.share}%`} />
          </>
        )}
        {card.kind === "group" && (
          <div className="sm:col-span-2">
            <span className="block text-[11px] text-muted">{card.taskType === "classification" ? "类别分布" : "群分布"}</span>
            <div className="mt-1.5 flex flex-wrap gap-1.5">
              {(card.groups ?? []).map((group) => (
                <span key={group.cluster} className="rounded-tag border border-line bg-surface px-2 py-1 font-mono text-[10px] text-ink-2">
                  {card.taskType === "classification" ? "类别" : "群"} {group.cluster} · {group.size} 条 · {group.share}%
                </span>
              ))}
            </div>
          </div>
        )}
        {card.kind === "anomaly" && card.stats && (
          <>
            <Stat label="异常条数" value={card.stats.anomalyCount} />
            <Stat label="异常占比" value={`${card.stats.share}%`} />
          </>
        )}
        <div className="min-w-0 sm:col-span-2">
          <span className="block text-[11px] text-muted">主要影响因素</span>
          <div className="mt-1 flex flex-wrap gap-1.5">
            {(card.factors ?? []).length ? (card.factors).map((factor) => (
              <span key={factor.name} className={cn("rounded-tag border border-line bg-surface px-2 py-1 text-[10px] text-ink-2")}>
                {factor.name} <b className="font-mono">{factor.importance}%</b>
              </span>
            )) : <span className="text-[11px] text-faint">{card.executionType === "mcp" ? "远程接口未提供特征重要性" : "该算法不提供原生特征重要性"}</span>}
          </div>
        </div>
        <p className="text-[10px] text-faint sm:col-span-2">
          {card.algorithmName} · 目标「{card.targetLabel}」 · {card.executionType === "mcp" ? `基于 ${card.sampleCount} 条${card.dataOrigin === "sample" ? "输入样例" : "查询记录"}` : `基于最近 ${card.sampleCount} 条真实记录`} · 模型 {card.modelId}
        </p>
      </div>
    </section>
  );
}

function Stat({ label, value }) {
  return (
    <div className="flex items-baseline gap-2">
      <span className="text-[11px] text-muted">{label}</span>
      <strong className="font-mono text-sm font-semibold text-ink">{value ?? "—"}</strong>
    </div>
  );
}
