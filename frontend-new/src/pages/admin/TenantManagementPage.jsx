import { useEffect, useState } from "react";
import { listSources } from "../../api/datasources";
import { getCurrentTenant, listCurrentTenantMembers } from "../../api/tenants";
import Icon from "../../components/ui/Icon";
import { Card, ErrorNotice, PageFrame, RouteSkeleton, StatusPill } from "../../components/ui";
import { getToken } from "../../lib/auth";

const roleLabels = { owner: "所有者", admin: "管理员", member: "成员", viewer: "只读成员" };

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

  return (
    <PageFrame>
      <section className="overflow-hidden rounded-card border border-line bg-surface shadow-card">
        <div className="grid gap-0 lg:grid-cols-[minmax(0,1.45fr)_minmax(360px,0.75fr)]">
          <div className="px-6 py-6 lg:px-8 lg:py-8">
            <div className="flex items-center gap-2 text-xs font-semibold text-primary"><Icon name="shield" size={16} />当前企业边界</div>
            <h2 className="mt-3 text-2xl font-semibold tracking-tight text-ink">{data.tenant.name}</h2>
            <p className="mt-2 max-w-[68ch] text-sm leading-6 text-muted">数据源、指标口径、调查会话和长期记忆均以该企业为第一层隔离边界；企业内部的部门与岗位权限在此边界内继续生效。</p>
            <div className="mt-5 flex flex-wrap gap-2">
              <StatusPill tone="green">运行中</StatusPill>
              <span className="rounded-tag border border-line bg-surface-2 px-2 py-1 text-[11px] text-muted">租户标识 {data.tenant.slug}</span>
              <span className="rounded-tag border border-line bg-surface-2 px-2 py-1 text-[11px] text-muted">我的角色 {roleLabels[data.tenant.role] ?? data.tenant.role}</span>
            </div>
          </div>
          <div className="grid grid-cols-2 border-t border-line bg-surface-2 lg:border-l lg:border-t-0">
            {[
              ["成员", data.members.length, "账号与角色"],
              ["数据源", data.sources.length, "物理连接隔离"],
              ["隔离层级", "2", "企业 + 数据源"],
              ["访问模式", "只读", "分析执行边界"],
            ].map(([label, value, note], index) => (
              <div key={label} className={`px-5 py-5 ${index % 2 ? "border-l" : ""} ${index > 1 ? "border-t" : ""} border-line`}>
                <span className="text-xs text-muted">{label}</span>
                <strong className="mt-2 block text-xl font-semibold tracking-tight text-ink">{value}</strong>
                <small className="mt-1 block text-[10px] text-faint">{note}</small>
              </div>
            ))}
          </div>
        </div>
      </section>

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(340px,0.62fr)]">
        <Card>
          <div className="flex items-end justify-between gap-4 border-b border-line px-5 py-4">
            <div><h3 className="text-base font-semibold text-ink">企业成员</h3><p className="mt-1 text-xs text-muted">成员只能进入已授权的企业空间，企业角色不替代内部部门权限。</p></div>
            <span className="text-xs text-faint">共 {data.members.length} 人</span>
          </div>
          <div className="divide-y divide-line px-5">
            {data.members.map((member) => (
              <div key={member.id} className="grid grid-cols-[minmax(0,1fr)_100px_74px] items-center gap-4 py-3.5">
                <span className="min-w-0"><strong className="block truncate text-sm font-semibold text-ink">{member.displayName}</strong><small className="mt-0.5 block truncate text-[11px] text-faint">{member.username}</small></span>
                <span className="text-xs text-muted">{roleLabels[member.role] ?? member.role}</span>
                <StatusPill tone={member.enabled ? "green" : "red"}>{member.enabled ? "已启用" : "已停用"}</StatusPill>
              </div>
            ))}
            {!data.authenticated && (
              <div className="py-6 text-center text-xs text-muted">成员名单仅对已登录的企业管理员开放，登录后可查看完整列表。</div>
            )}
          </div>
        </Card>

        <Card>
          <div className="border-b border-line px-5 py-4"><h3 className="text-base font-semibold text-ink">数据边界</h3><p className="mt-1 text-xs text-muted">这里只展示当前企业可见的数据源。</p></div>
          <div className="divide-y divide-line px-5">
            {data.sources.map((source) => (
              <div key={source.id} className="flex items-center gap-3 py-3.5">
                <span className="grid h-9 w-9 shrink-0 place-items-center rounded-control bg-primary-soft text-primary"><Icon name="database" size={17} /></span>
                <span className="min-w-0 flex-1"><strong className="block truncate text-sm font-semibold text-ink">{source.name}</strong><small className="mt-0.5 block truncate text-[10px] text-faint">{source.typeLabel} · {source.database}</small></span>
                <StatusPill tone={source.status === "连接正常" ? "green" : "amber"}>{source.status}</StatusPill>
              </div>
            ))}
          </div>
          <div className="border-t border-line bg-surface-2 px-5 py-3 text-[11px] leading-5 text-muted">切换企业空间后，前端会清除当前会话缓存并重新加载，避免把上一个企业的调查状态带入新空间。</div>
        </Card>
      </div>
    </PageFrame>
  );
}
