import { useEffect, useState } from "react";
import Icon from "../../components/ui/Icon";
import { Card, EmptyState, ErrorNotice, PageFrame, RouteSkeleton, SectionHeader, StatusPill } from "../../components/ui";
import { listAuditLogs } from "../../api/security";

const layers = [
  ["权限", "角色与数据范围"],
  ["语义", "表字段白名单"],
  ["代码", "SQL AST 校验"],
  ["执行", "只读沙箱与限流"],
  ["结果", "脱敏与数值核对"],
];

export default function SecurityPage() {
  const [logs, setLogs] = useState(null);
  const [error, setError] = useState(null);

  const load = () => {
    setError(null);
    setLogs(null);
    listAuditLogs().then(setLogs).catch(setError);
  };
  useEffect(load, []);

  if (error) {
    return (
      <PageFrame>
        <ErrorNotice title="审计记录加载失败" detail="请检查数据连接后重试。" onRetry={load} />
      </PageFrame>
    );
  }
  if (!logs) return <RouteSkeleton />;

  return (
    <PageFrame>
      <Card className="flex flex-wrap items-center gap-6 px-5 py-4">
        <div className="flex min-w-0 items-center gap-3">
          <span className="grid h-11 w-11 shrink-0 place-items-center rounded-card bg-teal-soft text-teal"><Icon name="shield" size={24} /></span>
          <span className="min-w-0">
            <strong className="block text-body font-semibold text-ink">安全策略运行正常</strong>
            <small className="text-[11px] text-faint">过去 24 小时完成 482 次校验，拦截 3 次危险操作</small>
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-5 text-xs text-muted">
          <span><b className="mr-1 font-mono text-base font-semibold text-ink">100%</b>只读查询</span>
          <span><b className="mr-1 font-mono text-base font-semibold text-ink">482</b>已校验执行</span>
          <span><b className="mr-1 font-mono text-base font-semibold text-red">3</b>危险操作拦截</span>
          <span><b className="mr-1 font-mono text-base font-semibold text-ink">0</b>敏感数据泄露</span>
        </div>
      </Card>

      <Card>
        <SectionHeader eyebrow="已启用 5 项" title="安全控制策略" />
        <div className="grid grid-cols-5 gap-2 px-5 pb-5">
          {layers.map(([title, text], index) => (
            <div key={title} className="relative rounded-control border border-line bg-surface-2 px-2.5 py-3">
              <span className="grid h-5 w-5 place-items-center rounded-tag border border-line bg-surface font-mono text-[10px] font-semibold text-muted">{index + 1}</span>
              <strong className="mt-2 block text-xs font-semibold text-ink">{title}控制</strong>
              <small className="mt-0.5 block text-[10px] leading-snug text-faint">{text}</small>
              {index < layers.length - 1 && <span className="absolute -right-2 top-1/2 z-10 -translate-y-1/2 text-faint"><Icon name="chevron" size={14} /></span>}
            </div>
          ))}
        </div>
      </Card>

      <Card>
        <SectionHeader eyebrow="最近 24 小时" title="执行审计记录" action="导出审计日志" />
        {logs.length ? (
          <div className="px-5 pb-5">
            <div className="grid grid-cols-[0.6fr_0.8fr_minmax(0,1.4fr)_1fr_0.7fr] gap-3 py-2.5 text-[11px] font-semibold text-muted">
              <span>时间</span><span>用户</span><span>执行动作</span><span>数据资源</span><span>结果</span>
            </div>
            {logs.map((row, index) => (
              <div key={`${row.time}-${index}`} className="grid grid-cols-[0.6fr_0.8fr_minmax(0,1.4fr)_1fr_0.7fr] items-center gap-3 border-t border-line py-3 text-xs text-ink-2">
                <span><code className="font-mono text-[11px] text-muted">{row.time}</code></span>
                <span className="min-w-0">
                  <b className="block truncate font-semibold text-ink">{row.user}</b>
                  <small className="text-[10px] text-faint">{row.role}</small>
                </span>
                <span className="min-w-0 truncate">{row.action}</span>
                <span className="min-w-0"><code className="block truncate font-mono text-[11px]">{row.resource}</code></span>
                <span><StatusPill tone={row.tone}>{row.result}</StatusPill></span>
              </div>
            ))}
          </div>
        ) : (
          <div className="px-5 pb-5">
            <EmptyState title="暂无审计记录" description="Agent 执行查询后，审计记录会显示在这里。" />
          </div>
        )}
      </Card>
    </PageFrame>
  );
}
