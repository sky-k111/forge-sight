import { useEffect, useState } from "react";
import { listSources } from "../../api/datasources";
import { getCurrentTenant, listCurrentTenantMembers } from "../../api/tenants";
import Icon from "../../components/ui/Icon";
import cn from "../../lib/cn";
import { ErrorNotice, PageFrame, RouteSkeleton, StatusPill } from "../../components/ui";
import { getToken } from "../../lib/auth";

const roleLabels = { owner: "所有者", admin: "管理员", member: "成员", viewer: "只读成员" };

function formatJoined(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value).slice(0, 10) : date.toLocaleDateString("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit" });
}

export default function TenantManagementPage() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  const load = () => {
    setError(null);
    // 成员名单仅对登录账号开放；演示模式的匿名访问只展示企业边界概览。
    const authenticated = Boolean(getToken());
    Promise.all([
      getCurrentTenant(),
      authenticated ? listCurrentTenantMembers() : Promise.resolve({ items: [] }),
      listSources(),
    ])
      .then(([tenant, memberResult, sources]) => setData({
        tenant,
        members: memberResult.items ?? [],
        authenticated,
        sources,
      }))
      .catch(setError);
  };

  useEffect(load, []);

  if (error) return <PageFrame><ErrorNotice title="企业空间加载失败" detail={error.message || "请检查当前账号的企业权限后重试。"} onRetry={load} /></PageFrame>;
  if (!data) return <RouteSkeleton />;

  const enabledCount = data.members.filter((member) => member.enabled).length;
  const roleCounts = data.members.reduce((acc, member) => ({ ...acc, [member.role]: (acc[member.role] ?? 0) + 1 }), {});

  return (
    <PageFrame className="max-w-[1360px] gap-4">
      <section className="flex flex-wrap items-center justify-between gap-3 border-b border-line pb-3">
        <div className="flex min-w-0 items-center gap-2.5">
          <span className="grid h-8 w-8 shrink-0 place-items-center rounded-control bg-primary-soft text-primary"><Icon name="shield" size={16} /></span>
          <div className="min-w-0">
            <h2 className="truncate text-sm font-semibold tracking-tight text-ink">{data.tenant.name}</h2>
            <p className="mt-0.5 truncate text-[11px] text-muted">租户标识 <span className="font-mono">{data.tenant.slug}</span> · 数据源、指标、会话与记忆均以企业为第一隔离边界</p>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          <StatusPill tone="green">运行中</StatusPill>
          <StatusPill tone="blue">我的角色 · {roleLabels[data.tenant.role] ?? data.tenant.role}</StatusPill>
          <span className="rounded-full border border-line bg-surface px-2 py-1 text-[11px] text-muted">只读访问</span>
        </div>
      </section>

      <div className="grid grid-cols-2 divide-y divide-line overflow-hidden rounded-panel border border-line bg-paper sm:grid-cols-4 sm:divide-y-0 sm:divide-x">
        <Fact label="成员" value={data.members.length} note={data.authenticated ? `${enabledCount} 人已启用` : "登录后可见"} />
        <Fact label="数据源" value={data.sources.length} note="物理连接隔离" />
        <Fact label="隔离层级" value="企业 + 数据源" note="两级边界" />
        <Fact label="执行模式" value="只读" note="分析执行边界" />
      </div>

      <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1.6fr)_minmax(320px,1fr)]">
        <section className="min-w-0 overflow-hidden rounded-panel border border-line bg-surface">
          <header className="flex items-center justify-between gap-3 border-b border-line px-4 py-3">
            <div className="flex items-baseline gap-2">
              <h3 className="text-sm font-semibold text-ink">企业成员</h3>
              <span className="font-mono text-[11px] text-faint">{data.members.length} 人</span>
            </div>
            {data.authenticated && Object.keys(roleCounts).length > 0 && (
              <div className="flex flex-wrap gap-1.5">
                {Object.entries(roleCounts).map(([role, count]) => (
                  <span key={role} className="rounded-tag border border-line bg-surface-2 px-1.5 py-0.5 text-[10px] text-muted">{roleLabels[role] ?? role} {count}</span>
                ))}
              </div>
            )}
          </header>
          {data.members.length ? (
            <div className="divide-y divide-line/70">
              <div className="grid grid-cols-[minmax(0,1fr)_88px_72px_84px] gap-3 bg-surface-2/50 px-4 py-1.5 text-[10px] font-semibold text-faint">
                <span>成员</span><span>角色</span><span>状态</span><span className="text-right">加入时间</span>
              </div>
              {data.members.map((member) => (
                <div key={member.id} className="grid grid-cols-[minmax(0,1fr)_88px_72px_84px] items-center gap-3 px-4 py-2.5">
                  <span className="min-w-0">
                    <strong className="block truncate text-[13px] font-medium text-ink">{member.displayName}</strong>
                    <small className="mt-0.5 block truncate font-mono text-[10px] text-faint">@{member.username}</small>
                  </span>
                  <span className={cn("truncate text-[11px]", member.role === "owner" || member.role === "admin" ? "text-ink-2" : "text-muted")}>{roleLabels[member.role] ?? member.role}</span>
                  <StatusPill tone={member.enabled ? "green" : "red"}>{member.enabled ? "已启用" : "已停用"}</StatusPill>
                  <span className="text-right font-mono text-[10px] text-faint">{formatJoined(member.joinedAt)}</span>
                </div>
              ))}
            </div>
          ) : (
            <div className="px-5 py-8 text-center text-xs text-muted">
              {data.authenticated ? "该企业还没有成员。" : "成员名单仅对已登录的企业管理员开放，登录后可查看完整列表。"}
            </div>
          )}
        </section>

        <aside className="grid min-w-0 content-start gap-4">
          <section className="overflow-hidden rounded-panel border border-line bg-surface">
            <h3 className="flex items-center justify-between border-b border-line px-4 py-2.5 text-[11px] font-semibold text-muted">
              数据边界
              <span className="font-mono text-faint">{data.sources.length} 个源</span>
            </h3>
            {data.sources.length ? (
              <div className="divide-y divide-line/70">
                {data.sources.map((source) => (
                  <div key={source.id} className="flex items-center gap-2.5 px-4 py-2.5">
                    <span className="shrink-0 text-muted"><Icon name="database" size={15} /></span>
                    <span className="min-w-0 flex-1">
                      <strong className="block truncate text-[13px] font-medium text-ink">{source.name}</strong>
                      <small className="mt-0.5 block truncate text-[10px] text-faint">{source.typeLabel} · {source.database}</small>
                    </span>
                    <StatusPill tone={source.status === "连接正常" ? "green" : "amber"}>{source.status}</StatusPill>
                  </div>
                ))}
              </div>
            ) : <p className="px-4 py-6 text-center text-[11px] text-muted">当前企业尚未接入数据源。</p>}
            <p className="border-t border-line bg-surface-2 px-4 py-2.5 text-[10px] leading-5 text-faint">切换企业空间后，前端会清除当前会话缓存并重新加载，避免把上一个企业的调查状态带入新空间。</p>
          </section>

          <section className="overflow-hidden rounded-panel border border-line bg-surface">
            <h3 className="border-b border-line px-4 py-2.5 text-[11px] font-semibold text-muted">成员角色分布</h3>
            {data.authenticated && data.members.length ? (
              <div className="grid gap-2.5 px-4 py-3">
                {Object.entries(roleLabels).filter(([role]) => roleCounts[role]).map(([role, label]) => (
                  <div key={role}>
                    <div className="flex items-baseline justify-between text-[11px]">
                      <span className="text-ink-2">{label}</span>
                      <span className="font-mono text-faint">{roleCounts[role]} 人</span>
                    </div>
                    <span className="mt-1 block h-1.5 overflow-hidden rounded-full bg-surface-3">
                      <span className="block h-full rounded-full bg-primary" style={{ width: `${(roleCounts[role] / data.members.length) * 100}%` }} />
                    </span>
                  </div>
                ))}
              </div>
            ) : <p className="px-4 py-6 text-center text-[11px] text-muted">登录后展示角色分布。</p>}
          </section>
        </aside>
      </div>
    </PageFrame>
  );
}

function Fact({ label, value, note }) {
  return (
    <div className="px-4 py-3">
      <span className="block text-[11px] text-muted">{label}</span>
      <strong className="mt-1 block font-mono text-base font-semibold leading-none tracking-tight text-ink">{value}</strong>
      <small className="mt-1.5 block truncate text-[10px] text-faint">{note}</small>
    </div>
  );
}
