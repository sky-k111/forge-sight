import { useEffect, useState } from "react";
import Icon from "../../components/ui/Icon";
import cn from "../../lib/cn";
import { Button, Card, PageFrame, SectionHeader, StatusPill } from "../../components/ui";
import { getSkillSettings, updateSkillSettings } from "../../api/skills";

const configs = [
  { icon: "database", name: "华东制造数据底座", type: "PostgreSQL 16", detail: "manufacturing · 只读账号", status: "连接正常", tone: "green" },
  { icon: "cpu", name: "DeepSeek 主模型", type: "OpenAI Compatible", detail: "deepseek-chat · 32K context", status: "服务正常", tone: "green" },
  { icon: "model", name: "Python 建模引擎", type: "隔离执行环境", detail: "scikit-learn · 2 CPU / 4GB", status: "可用", tone: "green" },
  { icon: "knowledge", name: "语义检索服务", type: "Embedding + PostgreSQL", detail: "77 个已发布知识项", status: "已同步", tone: "blue" },
];

export default function SettingsPage() {
  const [savedAt, setSavedAt] = useState("");
  const [skillSetting, setSkillSetting] = useState({ enabled: false, experiment: {} });
  const [skillLoading, setSkillLoading] = useState(true);
  const [skillError, setSkillError] = useState("");

  useEffect(() => {
    let active = true;
    getSkillSettings()
      .then((data) => active && setSkillSetting(data))
      .catch((error) => active && setSkillError(error.message))
      .finally(() => active && setSkillLoading(false));
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
  return (
    <PageFrame>
      <div className="grid items-start gap-3.5 xl:grid-cols-2">
        <Card>
          <SectionHeader eyebrow="运行依赖" title="连接与引擎" action="添加配置" />
          <div className="grid gap-1 px-4 pb-4">
            {configs.map((config) => (
              <button
                type="button"
                key={config.name}
                className="grid w-full cursor-pointer grid-cols-[36px_minmax(0,1fr)_auto_15px] items-center gap-3 rounded-control px-2 py-2.5 text-left transition-colors hover:bg-surface-2"
              >
                <span className="grid h-9 w-9 place-items-center rounded-control border border-line bg-surface-2 text-muted"><Icon name={config.icon} size={18} /></span>
                <span className="min-w-0">
                  <strong className="block truncate text-body font-semibold text-ink">{config.name}</strong>
                  <small className="mt-0.5 block truncate text-xs text-faint">{config.type} · {config.detail}</small>
                </span>
                <StatusPill tone={config.tone}>{config.status}</StatusPill>
                <span className="text-faint"><Icon name="chevron" size={15} /></span>
              </button>
            ))}
          </div>
        </Card>

        <Card>
          <SectionHeader eyebrow="Agent 执行策略" title="默认安全边界" />
          <div className="grid gap-1 px-4 pb-4">
            <PolicyRow icon="lock" title="数据库只读" description="仅允许 SELECT 与安全公共表表达式" enabled />
            <PolicyRow icon="clock" title="查询超时" description="单次查询最长执行 30 秒" enabled value="30s" />
            <PolicyRow icon="database" title="最大返回行数" description="超量结果必须聚合或分页" enabled value="10,000" />
            <PolicyRow icon="code" title="Python 隔离执行" description="禁止网络、系统命令和任意文件读写" enabled />
            <PolicyRow icon="shield" title="结论数值核对" description="自动检查结论数字是否来自结果集" enabled />
            <Button variant="primary" className="mt-2 justify-self-end" onClick={() => setSavedAt("策略已保存 · 10:38")}>
              {savedAt || "保存策略"}
            </Button>
          </div>
        </Card>
      </div>
      <Card>
        <SectionHeader eyebrow="受治理经验学习" title="分析技能增强实验" />
        <div className="grid gap-4 px-5 pb-5 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center">
          <div className="max-w-3xl">
            <div className="flex flex-wrap items-center gap-2">
              <strong className="text-body font-semibold text-ink">在基础问析链路中召回已审核的分析步骤</strong>
              <StatusPill tone={skillSetting.enabled ? "green" : "gray"}>{skillSetting.enabled ? "实验已开启" : "基线模式"}</StatusPill>
            </div>
            <p className="mt-2 text-xs leading-relaxed text-muted">
              开启后只会注入管理员已发布的程序性技能；数据权限、只读校验和 SQL 白名单保持不变。每次运行都会固化 on/off 变体，便于比较成功率、Token 与人工反馈。
            </p>
            {skillError && <p className="mt-2 text-xs text-red">{skillError}</p>}
          </div>
          <div className="flex items-center gap-4">
            <ExperimentMetric label="关闭组" value={skillSetting.experiment?.off?.runs ?? 0} />
            <ExperimentMetric label="开启组" value={skillSetting.experiment?.on?.runs ?? 0} />
            <button
              type="button"
              disabled={skillLoading}
              onClick={toggleSkillEnhancement}
              aria-pressed={skillSetting.enabled}
              className={cn("relative h-6 w-11 rounded-full transition-colors disabled:opacity-50", skillSetting.enabled ? "bg-green" : "bg-line-strong")}
            >
              <i className={cn("absolute top-1 h-4 w-4 rounded-full bg-white shadow-card transition-[left]", skillSetting.enabled ? "left-6" : "left-1")} />
            </button>
          </div>
        </div>
      </Card>
    </PageFrame>
  );
}

function ExperimentMetric({ label, value }) {
  return <div className="text-right"><span className="block text-[10px] text-faint">{label}</span><strong className="font-mono text-sm text-ink">{value}</strong></div>;
}

function PolicyRow({ icon, title, description, enabled, value }) {
  const [active, setActive] = useState(enabled);
  return (
    <div className="grid grid-cols-[32px_minmax(0,1fr)_auto_auto] items-center gap-3 rounded-control px-2 py-2.5 transition-colors hover:bg-surface-2">
      <span className="grid h-8 w-8 place-items-center rounded-control border border-line bg-surface-2 text-muted"><Icon name={icon} size={16} /></span>
      <div className="min-w-0">
        <strong className="block truncate text-body font-semibold text-ink">{title}</strong>
        <small className="mt-0.5 block truncate text-xs text-faint">{description}</small>
      </div>
      {value && <code className="rounded-tag bg-surface-3 px-1.5 py-1 font-mono text-[10px] font-semibold text-ink-2">{value}</code>}
      <button
        type="button"
        onClick={() => setActive((current) => !current)}
        aria-pressed={active}
        aria-label={`切换${title}`}
        className={cn(
          "relative h-5 w-9 cursor-pointer rounded-full transition-colors",
          active ? "bg-green" : "bg-line-strong",
        )}
      >
        <i className={cn("absolute top-0.5 h-4 w-4 rounded-full bg-white shadow-card transition-[left]", active ? "left-[18px]" : "left-0.5")} />
      </button>
    </div>
  );
}
