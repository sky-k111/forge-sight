import { useEffect, useState } from "react";
import { useOutletContext } from "react-router-dom";
import Icon from "../../components/ui/Icon";
import cn from "../../lib/cn";
import { Card, ErrorNotice, PageFrame, RouteSkeleton, SectionHeader, StatusPill } from "../../components/ui";
import { Sparkline } from "../../components/charts";
import { getAdminOverview } from "../../api/overview";

const healthTones = {
  blue: "bg-primary-soft text-primary",
  teal: "bg-teal-soft text-teal",
  violet: "bg-violet/8 text-violet",
  green: "bg-green/8 text-green",
};

const sparkTones = {
  blue: "text-primary",
  teal: "text-teal",
  violet: "text-violet",
  green: "text-green",
};

export default function AdminOverviewPage() {
  const { navigatePage } = useOutletContext();
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  const load = () => {
    setError(null);
    setData(null);
    getAdminOverview().then(setData).catch(setError);
  };
  useEffect(load, []);

  if (error) {
    return (
      <PageFrame>
        <ErrorNotice title="治理总览加载失败" detail="请检查数据连接后重试。" onRetry={load} />
      </PageFrame>
    );
  }
  if (!data) return <RouteSkeleton />;

  return (
    <PageFrame>
      <section className="flex flex-wrap items-end justify-between gap-6">
        <div className="min-w-0">
          <span className="text-[11px] font-semibold tracking-wide text-faint">今日治理待办</span>
          <h2 className="mt-1.5 text-xl font-semibold tracking-tight text-ink">3 类治理事项</h2>
          <p className="mt-1.5 text-body text-muted">3 个指标待审核 · 2 个字段待映射 · 1 条拦截记录待复核</p>
        </div>
        <div className="flex items-center gap-2.5 rounded-card border border-line bg-surface px-3.5 py-2.5 shadow-card">
          <span className="h-[7px] w-[7px] rounded-full bg-green ring-2 ring-green/15" />
          <div>
            <strong className="block text-xs font-semibold text-ink">治理基线正常</strong>
            <small className="text-[10px] text-faint">最后巡检 10:36</small>
          </div>
        </div>
      </section>

      <div className="grid gap-3.5 sm:grid-cols-2 xl:grid-cols-4">
        {data.health.map((item) => (
          <button
            type="button"
            key={item.label}
            onClick={() => navigatePage(item.page)}
            className="group cursor-pointer rounded-card border border-line bg-surface p-4 text-left shadow-card transition-shadow hover:shadow-card-hover"
          >
            <div className="flex items-center justify-between">
              <span className={cn("grid h-9 w-9 place-items-center rounded-control", healthTones[item.tone] ?? healthTones.blue)}>
                <Icon name={item.icon} size={18} />
              </span>
              <span className="text-faint transition-colors group-hover:text-ink"><Icon name="arrow" size={15} /></span>
            </div>
            <span className="mt-3 block text-[11px] text-muted">{item.label}</span>
            <strong className="mt-1 block font-mono text-2xl font-semibold tracking-tight text-ink">{item.value}</strong>
            <div className="mt-1.5 flex items-end justify-between gap-3">
              <span className="min-w-0">
                <small className="block truncate text-[11px] text-faint">{item.note}</small>
                <small className="mt-0.5 block font-mono text-[10px] text-faint">{item.delta}</small>
              </span>
              <Sparkline data={item.trend} className={cn("shrink-0", sparkTones[item.tone] ?? sparkTones.blue)} />
            </div>
          </button>
        ))}
      </div>

      <div className="grid gap-3.5 xl:grid-cols-2">
        <Card>
          <SectionHeader eyebrow="按优先级排序" title="待处理事项" />
          <div className="grid gap-1 px-5 pb-5">
            {data.queue.map((item) => (
              <button
                type="button"
                key={item.index}
                onClick={() => navigatePage(item.page)}
                className="grid w-full cursor-pointer grid-cols-[28px_minmax(0,1fr)_auto] items-center gap-3 rounded-control border-t border-line px-1 py-3 text-left transition-colors first:border-t-0 hover:bg-surface-2"
              >
                <span className="font-mono text-xs font-semibold text-faint">{item.index}</span>
                <span className="min-w-0">
                  <strong className="block truncate text-body font-semibold text-ink">{item.title}</strong>
                  <small className="mt-0.5 block truncate text-xs text-faint">{item.note}</small>
                </span>
                <StatusPill tone={item.tone}>{item.status}</StatusPill>
              </button>
            ))}
          </div>
        </Card>

        <Card>
          <SectionHeader eyebrow="4 个步骤" title="知识发布流程" />
          <div className="grid grid-cols-4 gap-2 px-5">
            {[["定义", "业务对象与指标"], ["映射", "表、字段与关系"], ["校验", "样例与规则检查"], ["发布", "进入 Agent 运行时"]].map(([title, text], index) => (
              <div key={title} className="relative rounded-control border border-line bg-surface-2 px-2.5 py-3">
                <span className="grid h-5 w-5 place-items-center rounded-tag border border-line bg-surface font-mono text-[10px] font-semibold text-muted">{index + 1}</span>
                <strong className="mt-2 block text-xs font-semibold text-ink">{title}</strong>
                <small className="mt-0.5 block text-[10px] leading-snug text-faint">{text}</small>
                {index < 3 && <span className="absolute -right-2 top-1/2 z-10 -translate-y-1/2 text-faint"><Icon name="chevron" size={14} /></span>}
              </div>
            ))}
          </div>
          <div className="grid grid-cols-3 gap-3 px-5 pb-5 pt-4">
            {[["生产主题", "92%"], ["质量主题", "96%"], ["设备主题", "84%"]].map(([label, value]) => (
              <div key={label} className="rounded-control border border-line px-3 py-2.5">
                <span className="block text-[11px] text-muted">{label}</span>
                <strong className="mt-0.5 block font-mono text-lg font-semibold tracking-tight text-ink">{value}</strong>
              </div>
            ))}
          </div>
        </Card>
      </div>
    </PageFrame>
  );
}
