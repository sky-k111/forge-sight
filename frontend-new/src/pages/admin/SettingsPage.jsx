import { useEffect, useState } from "react";
import Icon from "../../components/ui/Icon";
import cn from "../../lib/cn";
import { Button, Card, PageFrame, SectionHeader, StatusPill } from "../../components/ui";
import { getRuntimeConfiguration } from "../../api/security";
import { getSkillSettings, updateSkillSettings } from "../../api/skills";
import { getPlannerSettings, updatePlannerSettings } from "../../api/agentSettings";

export default function SettingsPage() {
  const [runtime, setRuntime] = useState(null);
  const [runtimeError, setRuntimeError] = useState("");
  const [runtimeLoading, setRuntimeLoading] = useState(true);
  const [runtimeRetry, setRuntimeRetry] = useState(0);
  const configs = runtime?.dependencies ?? [];
  const [skillSetting, setSkillSetting] = useState({ enabled: false, experiment: {} });
  const [skillLoading, setSkillLoading] = useState(true);
  const [skillError, setSkillError] = useState("");
  const [plannerSetting, setPlannerSetting] = useState({ enabled: true, rolloutPercent: 100, modelPlanningEnabled: true, experiment: {} });
  const [plannerLoading, setPlannerLoading] = useState(true);
  const [plannerError, setPlannerError] = useState("");

  useEffect(() => {
    let active = true;
    setRuntimeLoading(true);
    setRuntimeError("");
    getRuntimeConfiguration().then((data) => active && setRuntime(data))
      .catch((error) => active && setRuntimeError(error.message))
      .finally(() => active && setRuntimeLoading(false));
    return () => { active = false; };
  }, [runtimeRetry]);

  useEffect(() => {
    let active = true;
    getSkillSettings()
      .then((data) => active && setSkillSetting(data))
      .catch((error) => active && setSkillError(error.message))
      .finally(() => active && setSkillLoading(false));
    getPlannerSettings()
      .then((data) => active && setPlannerSetting(data))
      .catch((error) => active && setPlannerError(error.message))
      .finally(() => active && setPlannerLoading(false));
    return () => { active = false; };
  }, []);

  const toggleSkillEnhancement = async () => {
    const next = !skillSetting.enabled;
    setSkillLoading(true);
    setSkillError("");
    try {
      const changed = await updateSkillSettings(next);
      setSkillSetting((current) => ({ ...current, ...changed }));
    } catch (error) {
      setSkillError(error.message);
    } finally {
      setSkillLoading(false);
    }
  };

  const savePlannerSetting = async (patch) => {
    const next = { ...plannerSetting, ...patch };
    setPlannerLoading(true);
    setPlannerError("");
    try {
      const changed = await updatePlannerSettings({
        enabled: next.enabled,
        rolloutPercent: next.rolloutPercent,
        modelPlanningEnabled: next.modelPlanningEnabled,
      });
      setPlannerSetting((current) => ({ ...current, ...changed }));
    } catch (error) {
      setPlannerError(error.message);
    } finally {
      setPlannerLoading(false);
    }
  };
  return (
    <PageFrame className="max-w-[1360px] gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line pb-3">
        <div className="flex min-w-0 items-center gap-2.5">
          <span className="grid h-8 w-8 shrink-0 place-items-center rounded-control bg-primary-soft text-primary"><Icon name="settings" size={16} /></span>
          <div className="min-w-0">
            <h2 className="text-sm font-semibold tracking-tight text-ink">运行依赖与执行策略</h2>
            <p className="mt-0.5 text-[11px] text-muted">数据连接、模型引擎、Agent 安全边界与灰度实验的集中配置</p>
          </div>
        </div>
        <Button disabled={runtimeLoading} onClick={() => setRuntimeRetry((value) => value + 1)}>刷新配置</Button>
      </div>
      {runtimeError && <p role="alert" className="text-xs text-red">配置读取失败：{runtimeError}。已有信息仅为上次快照，请刷新重试。</p>}
      <p className="text-xs text-muted">{runtimeLoading ? "正在读取运行配置…" : runtime?.scope}</p>

      <div className="grid grid-cols-3 divide-x divide-line overflow-hidden rounded-panel border border-line bg-paper">
        <div className="px-4 py-3"><span className="block text-[11px] text-muted">运行依赖</span><strong className="mt-1 block font-mono text-base font-semibold leading-none text-ink">{runtime ? configs.length : "—"}</strong></div>
        <div className="px-4 py-3"><span className="block text-[11px] text-muted">安全策略</span><strong className="mt-1 block font-mono text-base font-semibold leading-none text-ink">{runtime?.policies?.length ?? "—"}</strong></div>
        <div className="px-4 py-3"><span className="block text-[11px] text-muted">灰度实验</span><strong className="mt-1 block font-mono text-base font-semibold leading-none text-ink">2</strong></div>
      </div>

      <div className="grid items-start gap-4 xl:grid-cols-2">
        <Card>
          <SectionHeader eyebrow="运行依赖" title="连接与引擎" />
          <div className="grid gap-1 px-4 pb-4">
            {configs.map((config) => (
              <div
                key={config.name}
                className="grid w-full grid-cols-[36px_minmax(0,1fr)_auto] items-center gap-3 rounded-control px-2 py-2.5 text-left"
              >
                <span className="grid h-9 w-9 place-items-center rounded-control border border-line bg-surface-2 text-muted"><Icon name={config.icon} size={18} /></span>
                <span className="min-w-0">
                  <strong className="block truncate text-body font-semibold text-ink">{config.name}</strong>
                  <small className="mt-0.5 block truncate text-xs text-faint">{config.type} · {config.detail}</small>
                </span>
                <StatusPill tone={config.tone}>{config.status}</StatusPill>
              </div>
            ))}
          </div>
        </Card>

        <Card>
          <SectionHeader eyebrow="Agent 执行策略" title="默认安全边界" />
          <div className="grid gap-1 px-4 pb-4">
            {(runtime?.policies ?? []).map((policy) => <PolicyRow key={policy.title} {...policy} />)}
            <p className="mt-2 text-xs text-muted">安全边界由服务端强制执行。调整环境配置后需重启对应进程，再刷新核对；此处不提供关闭只读或假保存操作。</p>
          </div>
        </Card>
      </div>

      <Card>
        <SectionHeader eyebrow="受治理经验学习" title="分析技能增强实验" />
        <div className="px-5 pt-4 pb-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex min-w-0 flex-wrap items-center gap-2">
              <strong className="text-body font-semibold text-ink">在基础问析链路中召回已审核的分析步骤</strong>
              <StatusPill tone={skillSetting.enabled ? "green" : "gray"}>{skillSetting.enabled ? "实验已开启" : "基线模式"}</StatusPill>
            </div>
            <div className="flex shrink-0 items-center gap-4">
              <ExperimentMetric label="关闭组" value={skillSetting.experiment?.off?.runs ?? 0} />
              <ExperimentMetric label="开启组" value={skillSetting.experiment?.on?.runs ?? 0} />
              <button
                type="button"
                disabled={skillLoading}
                onClick={toggleSkillEnhancement}
                aria-pressed={skillSetting.enabled}
                aria-label="切换分析技能增强实验"
                className={cn("relative h-6 w-11 rounded-full transition-colors disabled:opacity-50", skillSetting.enabled ? "bg-green" : "bg-line-strong")}
              >
                <i className={cn("absolute top-1 h-4 w-4 rounded-full bg-white shadow-card transition-[left]", skillSetting.enabled ? "left-6" : "left-1")} />
              </button>
            </div>
          </div>
          <p className="mt-2 max-w-3xl text-xs leading-relaxed text-muted">
            开启后只会注入管理员已发布的程序性技能；数据权限、只读校验和 SQL 白名单保持不变。每次运行都会固化 on/off 变体，便于比较成功率、Token 与人工反馈。
          </p>
          {skillError && <p className="mt-2 text-xs text-red">{skillError}</p>}
        </div>
      </Card>
      <Card>
        <SectionHeader eyebrow="受限业务规划" title="Agent Planner 灰度实验" />
        <div className="px-5 pt-4 pb-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex min-w-0 flex-wrap items-center gap-2">
              <strong className="text-body font-semibold text-ink">复杂目标启用业务级规划，简单问析继续走固定快速链路</strong>
              <StatusPill tone={plannerSetting.enabled ? "blue" : "gray"}>{plannerSetting.enabled ? `灰度 ${plannerSetting.rolloutPercent}%` : "仅基线链路"}</StatusPill>
            </div>
            <div className="flex shrink-0 flex-wrap items-center justify-end gap-3">
              <ExperimentMetric label="基线组" value={plannerSetting.experiment?.baseline?.runs ?? 0} />
              <ExperimentMetric label="Planner 组" value={plannerSetting.experiment?.planner?.runs ?? 0} />
              <div className="flex rounded-control border border-line bg-surface-2 p-0.5" aria-label="Planner 灰度比例">
                {[25, 50, 100].map((percent) => (
                  <button
                    type="button"
                    key={percent}
                    disabled={plannerLoading || !plannerSetting.enabled}
                    onClick={() => savePlannerSetting({ rolloutPercent: percent })}
                    className={cn(
                      "h-7 min-w-10 rounded-[7px] px-2 text-[10px] font-semibold transition-colors disabled:opacity-40",
                      plannerSetting.rolloutPercent === percent ? "bg-white text-ink shadow-card" : "text-muted hover:text-ink",
                    )}
                  >{percent}%</button>
                ))}
              </div>
              <button
                type="button"
                disabled={plannerLoading}
                onClick={() => savePlannerSetting({ enabled: !plannerSetting.enabled })}
                aria-pressed={plannerSetting.enabled}
                aria-label="切换 Agent Planner 灰度实验"
                className={cn("relative h-6 w-11 rounded-full transition-colors disabled:opacity-50", plannerSetting.enabled ? "bg-primary" : "bg-line-strong")}
              >
                <i className={cn("absolute top-1 h-4 w-4 rounded-full bg-white shadow-card transition-[left]", plannerSetting.enabled ? "left-6" : "left-1")} />
              </button>
            </div>
          </div>
          <p className="mt-2 max-w-3xl text-xs leading-relaxed text-muted">
            Planner 只能选择注册后的业务能力，SQL 仍必须经过只读、白名单、方言与成本校验。运行创建时会冻结实验变体和权限范围，可对照成功率、Token 与耗时。
          </p>
          {plannerError && <p className="mt-2 text-xs text-red">{plannerError}</p>}
        </div>
      </Card>
    </PageFrame>
  );
}

function ExperimentMetric({ label, value }) {
  return <div className="text-right"><span className="block text-[10px] text-faint">{label}</span><strong className="font-mono text-sm text-ink">{value}</strong></div>;
}

function PolicyRow({ icon, title, description, value }) {
  return (
    <div className="grid grid-cols-[32px_minmax(0,1fr)_auto] items-center gap-3 rounded-control px-2 py-2.5">
      <span className="grid h-8 w-8 place-items-center rounded-control border border-line bg-surface-2 text-muted"><Icon name={icon} size={16} /></span>
      <div className="min-w-0">
        <strong className="block truncate text-body font-semibold text-ink">{title}</strong>
        <small className="mt-0.5 block text-xs text-faint">{description}</small>
      </div>
      <div className="flex justify-end">{value && <code className="rounded-tag bg-surface-3 px-1.5 py-1 font-mono text-[10px] font-semibold text-ink-2">{value}</code>}</div>
    </div>
  );
}
