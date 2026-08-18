import { useEffect, useMemo, useRef, useState } from "react";
import Icon from "../../components/ui/Icon";
import cn from "../../lib/cn";
import { Button, Card, EmptyState, ErrorNotice, Modal, PageFrame, RouteSkeleton, StatusPill } from "../../components/ui";
import { deleteMetric, listMetrics, listObjects, listRules, listTopics, publishMetric, saveMetric } from "../../api/knowledge";
import { getSemanticIndexStatus, listSources } from "../../api/datasources";
import SourceTypeMark from "../../components/ui/SourceTypeMark";

const topicTones = {
  blue: "bg-primary-soft text-primary",
  teal: "bg-teal-soft text-teal",
  amber: "bg-amber-soft text-amber",
};

function SourceBindingPicker({ sources, value, onChange }) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef(null);
  const selected = sources.find((source) => source.id === value) ?? sources[0];

  useEffect(() => {
    const close = (event) => {
      if (!rootRef.current?.contains(event.target)) setOpen(false);
    };
    document.addEventListener("pointerdown", close);
    return () => document.removeEventListener("pointerdown", close);
  }, []);

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        aria-label="指标物理绑定数据源"
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => setOpen((current) => !current)}
        className={cn(
          "group flex h-10 min-w-[218px] cursor-pointer items-center gap-2.5 rounded-control border bg-surface px-3 text-left transition",
          open ? "border-primary/45 ring-2 ring-primary/15" : "border-line hover:border-line-strong hover:bg-surface-2",
        )}
      >
        <SourceTypeMark type={selected?.type} className="h-7 w-7 rounded-tag" imageClassName="h-[18px] w-[18px]" />
        <span className="min-w-0 flex-1 leading-tight">
          <span className="block text-[10px] font-medium text-faint">当前绑定数据源</span>
          <span className="mt-0.5 block truncate text-xs font-semibold text-ink">{selected?.name ?? "未选择数据源"}</span>
        </span>
        <Icon
          name="chevron"
          size={14}
          className={cn("shrink-0 text-faint transition-transform", open ? "-rotate-90" : "rotate-90")}
        />
      </button>

      {open && (
        <div
          role="listbox"
          aria-label="可用数据源"
          className="absolute right-0 top-[calc(100%+6px)] z-40 min-w-full overflow-hidden rounded-control border border-line bg-surface p-1 shadow-[0_16px_40px_rgb(18_29_38_/12%)]"
        >
          <div className="border-b border-line px-2.5 py-2 text-[10px] font-semibold tracking-wide text-faint">选择物理绑定环境</div>
          <div className="max-h-64 overflow-y-auto py-1">
            {sources.map((source) => {
              const active = source.id === selected?.id;
              return (
                <button
                  key={source.id}
                  type="button"
                  role="option"
                  aria-selected={active}
                  onClick={() => {
                    setOpen(false);
                    if (!active) onChange(source.id);
                  }}
                  className={cn(
                    "flex w-full cursor-pointer items-center gap-2.5 rounded-tag px-2.5 py-2 text-left transition-colors",
                    active ? "bg-primary-soft" : "hover:bg-surface-2",
                  )}
                >
                  <SourceTypeMark
                    type={source.type}
                    className={cn("h-7 w-7 rounded-tag", active ? "border-primary/20" : "bg-surface-2")}
                    imageClassName="h-[18px] w-[18px]"
                  />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-xs font-semibold text-ink">{source.name}</span>
                    <span className="mt-0.5 flex items-center gap-1.5 text-[10px] text-faint">
                      <span>{source.typeLabel ?? source.type?.toUpperCase()}</span>
                      <span aria-hidden="true">·</span>
                      <span>{source.database}</span>
                    </span>
                  </span>
                  {active && <Icon name="check" size={14} className="shrink-0 text-primary" />}
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

export default function KnowledgePage() {
  const [tab, setTab] = useState("metrics");
  const [search, setSearch] = useState("");
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [selectedMetricId, setSelectedMetricId] = useState(null);
  const [currentSourceId, setCurrentSourceId] = useState(null);
  const [editor, setEditor] = useState(null);
  const [actionError, setActionError] = useState("");
  const [metricFilterOpen, setMetricFilterOpen] = useState(false);
  const [metricStatusFilter, setMetricStatusFilter] = useState("all");
  const [metricTopicFilter, setMetricTopicFilter] = useState("all");
  const [semanticIndex, setSemanticIndex] = useState(null);

  const load = async () => {
    setError(null);
    setActionError("");
    setData(null);
    try {
      const [sources, objects, rules, topics] = await Promise.all([listSources(), listObjects(), listRules(), listTopics()]);
      const sourceId = currentSourceId ?? sources.find((item) => item.isDefault)?.id ?? sources[0]?.id ?? null;
      const metrics = await listMetrics({ sourceId });
      setCurrentSourceId(sourceId);
      setData({ metrics, objects, rules, topics, sources });
      setSelectedMetricId((current) => current ?? metrics[0]?.id ?? null);
    } catch (nextError) {
      setError(nextError);
    }
  };
  // 仅挂载时加载一次；重试通过 onRetry={load} 手动触发
  useEffect(() => {
    load();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!currentSourceId) return undefined;
    let active = true;
    const refresh = async () => {
      try {
        const next = await getSemanticIndexStatus(currentSourceId);
        if (active) setSemanticIndex(next);
      } catch {
        if (active) setSemanticIndex(null);
      }
    };
    refresh();
    const timer = window.setInterval(refresh, 2500);
    return () => { active = false; window.clearInterval(timer); };
  }, [currentSourceId]);

  const refreshMetrics = async (selectId) => {
    const metrics = await listMetrics({ sourceId: currentSourceId });
    setData((current) => ({ ...current, metrics }));
    setSelectedMetricId(selectId ?? metrics[0]?.id ?? null);
  };

  const handleDelete = async (id) => {
    setActionError("");
    try {
      await deleteMetric(id);
      await refreshMetrics();
    } catch (nextError) {
      setActionError(nextError.message);
    }
  };

  const changeSource = async (sourceId) => {
    setCurrentSourceId(sourceId);
    setActionError("");
    try {
      const metrics = await listMetrics({ sourceId });
      setData((current) => ({ ...current, metrics }));
      setSelectedMetricId((current) => metrics.some((metric) => metric.id === current) ? current : metrics[0]?.id ?? null);
    } catch (nextError) {
      setActionError(nextError.message);
    }
  };

  const handlePublish = async (id) => {
    setActionError("");
    try {
      await publishMetric(id, currentSourceId);
      await refreshMetrics(id);
    } catch (nextError) {
      setActionError(nextError.message);
    }
  };

  const visibleMetrics = useMemo(
    () => (data?.metrics ?? []).filter((metric) => {
      const matchesSearch = `${metric.name}${metric.source}${metric.topic}`.includes(search.trim());
      const matchesStatus = metricStatusFilter === "all"
        || (metricStatusFilter === "available" ? metric.availableForAnalysis : !metric.availableForAnalysis);
      const matchesTopic = metricTopicFilter === "all" || metric.topic === metricTopicFilter;
      return matchesSearch && matchesStatus && matchesTopic;
    }),
    [search, data, metricStatusFilter, metricTopicFilter],
  );
  const selectedMetric = data?.metrics.find((metric) => metric.id === selectedMetricId) ?? data?.metrics[0];
  const knowledgeCount = data ? data.metrics.length + data.objects.length + data.rules.length + data.topics.length : 0;
  const linkedMetricCount = data?.metrics.filter((metric) => metric.source).length ?? 0;
  const linkedCoverage = data?.metrics.length ? Math.round((linkedMetricCount / data.metrics.length) * 100) : 0;
  const pendingCount = data?.metrics.filter((metric) => !metric.availableForAnalysis).length ?? 0;

  const tabs = [
    { id: "metrics", label: "指标口径", count: data?.metrics.length },
    { id: "objects", label: "业务对象", count: data?.objects.length },
    { id: "rules", label: "业务规则", count: data?.rules.length },
    { id: "topics", label: "分析主题", count: data?.topics.length },
  ];

  if (error) {
    return (
      <PageFrame>
        <ErrorNotice title="知识库加载失败" detail="请检查数据连接后重试。" onRetry={load} />
      </PageFrame>
    );
  }
  if (!data) return <RouteSkeleton />;

  return (
    <PageFrame>
      <Card className="flex flex-wrap items-center gap-5 px-5 py-4">
        <div className="flex min-w-0 items-center gap-3">
          <span className="grid h-9 w-9 shrink-0 place-items-center rounded-control bg-primary-soft text-primary"><Icon name="knowledge" /></span>
          <span className="min-w-0">
            <strong className="block text-body font-semibold text-ink">业务知识库</strong>
            <small className="text-[11px] text-faint">最近同步：今天 10:12</small>
          </span>
        </div>
        <div className="flex items-center gap-5 text-xs text-muted">
          <span><b className="mr-1 font-mono text-base font-semibold text-ink">{knowledgeCount}</b>知识项</span>
          <span><b className="mr-1 font-mono text-base font-semibold text-ink">{linkedCoverage}%</b>指标已关联数据</span>
          <span><b className="mr-1 font-mono text-base font-semibold text-amber">{pendingCount}</b>待审核或验证</span>
        </div>
        <div className="ml-auto">
          <div className="mb-2 flex justify-end">
            <span className="inline-flex items-center gap-1.5 rounded-tag border border-line bg-surface-2 px-2 py-1 text-[10px] font-semibold text-muted" title={semanticIndex?.latestJob?.reason ?? "当前数据源语义索引状态"}>
              <span className={cn("h-1.5 w-1.5 rounded-full", semanticIndex?.status === "failed" ? "bg-red" : semanticIndex?.status === "succeeded" ? "bg-green" : "bg-amber")} />
              {semanticIndex?.statusLabel ?? "读取索引状态"}
              {semanticIndex?.status === "succeeded" && semanticIndex.documents > 0 ? ` · ${semanticIndex.documents} 项` : ""}
            </span>
          </div>
          <SourceBindingPicker
            sources={data.sources ?? []}
            value={currentSourceId}
            onChange={changeSource}
          />
        </div>
        <Button variant="primary" onClick={() => setEditor({})}><Icon name="plus" size={15} />新增知识</Button>
      </Card>

      {actionError && (
        <div role="alert" className="flex items-center gap-2 rounded-control border border-red/25 bg-red-soft px-3 py-2 text-xs text-red">
          <Icon name="alert" size={15} />{actionError}
        </div>
      )}

      <div className="flex items-center gap-0.5 self-start rounded-control border border-line bg-surface-2 p-0.5">
        {tabs.map((item) => (
          <button
            type="button"
            key={item.id}
            onClick={() => setTab(item.id)}
            className={cn(
              "flex h-7 cursor-pointer items-center gap-1.5 rounded-tag px-3 text-xs font-medium transition-colors",
              tab === item.id ? "bg-surface text-ink shadow-card" : "text-muted hover:text-ink",
            )}
          >
            {item.label}<span className="font-mono text-[10px] text-faint">{item.count}</span>
          </button>
        ))}
      </div>

      {tab === "metrics" && (
        <div className="grid items-start gap-3.5 xl:grid-cols-[minmax(0,1.45fr)_minmax(320px,0.9fr)]">
          <Card>
            <div className="flex items-center gap-2 border-b border-line px-4 py-3">
              <label className="relative block flex-1">
                <span className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-faint"><Icon name="search" size={15} /></span>
                <input
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                  placeholder="搜索指标名称、主题或来源表"
                  className="h-9 w-full rounded-control border border-line bg-surface pl-8 pr-2.5 text-xs text-ink outline-none transition placeholder:text-faint focus:border-primary/50 focus:ring-2 focus:ring-primary/25"
                />
              </label>
              <div className="relative">
                <Button
                  size="sm"
                  aria-expanded={metricFilterOpen}
                  aria-haspopup="dialog"
                  onClick={() => setMetricFilterOpen((value) => !value)}
                >
                  <Icon name="filter" size={14} />筛选
                </Button>
                {metricFilterOpen && (
                  <div role="dialog" aria-label="筛选指标" className="absolute right-0 top-[calc(100%+8px)] z-30 grid min-w-[240px] gap-3 rounded-card border border-line bg-surface p-3 shadow-card-hover">
                    <label className="grid gap-1 text-[11px] font-semibold text-muted">
                      指标状态
                      <select aria-label="指标状态" value={metricStatusFilter} onChange={(event) => setMetricStatusFilter(event.target.value)} className="h-9 rounded-control border border-line bg-surface-2 px-2 text-xs font-normal text-ink">
                        <option value="all">全部状态</option>
                        <option value="available">可分析</option>
                        <option value="pending">待审核或验证</option>
                      </select>
                    </label>
                    <label className="grid gap-1 text-[11px] font-semibold text-muted">
                      所属主题
                      <select aria-label="指标主题" value={metricTopicFilter} onChange={(event) => setMetricTopicFilter(event.target.value)} className="h-9 rounded-control border border-line bg-surface-2 px-2 text-xs font-normal text-ink">
                        <option value="all">全部主题</option>
                        {[...new Set((data.metrics ?? []).map((metric) => metric.topic).filter(Boolean))].map((topic) => <option key={topic} value={topic}>{topic}</option>)}
                      </select>
                    </label>
                    <Button size="sm" onClick={() => { setMetricStatusFilter("all"); setMetricTopicFilter("all"); setMetricFilterOpen(false); }}>清除筛选</Button>
                  </div>
                )}
              </div>
            </div>
            {visibleMetrics.length ? (
              <div className="px-4 pb-4">
                <div className="grid grid-cols-[minmax(0,2fr)_0.8fr_0.7fr_0.9fr] gap-3 py-2.5 text-[11px] font-semibold text-muted">
                  <span>指标</span>
                  <span className="knowledge-metric-col--right text-right">主题</span>
                  <span className="knowledge-metric-col--right text-right">负责人</span>
                  <span className="knowledge-metric-col--right text-right">状态</span>
                </div>
                {visibleMetrics.map((metric) => (
                  <button
                    type="button"
                    key={metric.id}
                    onClick={() => setSelectedMetricId(metric.id)}
                    className={cn(
                      "grid w-full cursor-pointer grid-cols-[minmax(0,2fr)_0.8fr_0.7fr_0.9fr] items-center gap-3 rounded-control border-t border-line px-1 py-3 text-left text-xs text-ink-2 transition-colors",
                      selectedMetric?.id === metric.id ? "bg-surface-2 ring-1 ring-inset ring-line" : "hover:bg-surface-2",
                    )}
                  >
                    <span className="min-w-0">
                      <b className="block truncate text-body font-semibold text-ink">{metric.name}</b>
                      <small className="mt-0.5 block truncate font-mono text-[10px] text-faint">{metric.id} · {metric.source}</small>
                    </span>
                    <span className="knowledge-metric-col--right text-right">{metric.topic}</span>
                    <span className="knowledge-metric-col--right text-right">{metric.owner}</span>
                    <span className="knowledge-metric-col--right flex justify-end"><StatusPill tone={metric.availableForAnalysis ? "green" : "amber"}>{metric.availableForAnalysis ? "可分析" : metric.bindingStatus}</StatusPill></span>
                  </button>
                ))}
              </div>
            ) : (
              <div className="p-4"><EmptyState title="没有匹配的指标" description="调整搜索关键词后重试。" /></div>
            )}
          </Card>
          {selectedMetric && (
            <MetricDetail
              metric={selectedMetric}
              onEdit={() => setEditor({ metric: selectedMetric })}
              onDelete={() => handleDelete(selectedMetric.id)}
              onPublish={() => handlePublish(selectedMetric.id)}
            />
          )}
        </div>
      )}
      {tab === "objects" && <KnowledgeObjects objects={data.objects} />}
      {tab === "rules" && <KnowledgeRules rules={data.rules} />}
      {tab === "topics" && <KnowledgeTopics topics={data.topics} onOpen={(topic) => { setTab("metrics"); setSearch(topic.name); }} />}
      {editor && (
        <MetricEditorModal
          metric={editor.metric}
          sourceId={currentSourceId}
          onClose={() => setEditor(null)}
          onSaved={async (saved) => {
            setEditor(null);
            await refreshMetrics(saved.id);
          }}
        />
      )}
    </PageFrame>
  );
}

function MetricDetail({ metric, onEdit, onDelete, onPublish }) {
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  useEffect(() => setConfirmingDelete(false), [metric.id]);
  return (
    <Card className="grid gap-3.5 p-4.5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <span className="font-mono text-[10px] font-semibold tracking-wide text-faint">{metric.id}</span>
          <h3 className="mt-0.5 truncate text-base font-semibold tracking-tight text-ink">{metric.name}</h3>
        </div>
        <StatusPill tone={metric.status === "已发布" ? "green" : "amber"}>{metric.status}</StatusPill>
      </div>
      <div>
        <span className="text-[11px] font-semibold text-muted">业务定义</span>
        <p className="mt-1 text-xs leading-relaxed text-ink-2">{metric.definition ?? "暂无业务定义，编辑指标后补充。"}</p>
      </div>
      <div>
        <span className="text-[11px] font-semibold text-muted">企业统一业务公式</span>
        <code className="mt-1.5 block rounded-control bg-ink px-3 py-2.5 font-mono text-[11px] text-white/90">{metric.formula}</code>
      </div>
      <div className="rounded-control border border-line bg-surface-2 p-3">
        <div className="flex items-center justify-between gap-2">
          <span className="text-[11px] font-semibold text-muted">当前数据源物理实现</span>
          <StatusPill tone={metric.bindingStatus === "已验证" ? "green" : "amber"}>{metric.bindingStatus}</StatusPill>
        </div>
        <code className="mt-2 block break-words font-mono text-[11px] leading-relaxed text-ink">{metric.physicalFormula || "尚未配置物理公式"}</code>
        <p className="mt-1.5 text-[10px] text-faint">{metric.sourceName || "当前数据源"} · {metric.sqlDialect || "未指定方言"} · 绑定版本 v{metric.bindingVersion ?? "—"}</p>
      </div>
      <div className="grid gap-2 border-t border-line pt-3 text-xs">
        {[
          ["默认时间字段", metric.timeField || "—"],
          ["可用维度", metric.dimensions?.length ? metric.dimensions.join("、") : "—"],
          ["数据来源", metric.source],
          ["展示精度", metric.precision || "—"],
          ["负责人", metric.owner],
          ["最近更新", metric.updated],
        ].map(([label, value]) => (
          <div key={label} className="flex items-baseline justify-between gap-3">
            <span className="shrink-0 text-muted">{label}</span>
            <strong className="min-w-0 truncate text-right font-medium text-ink">{value}</strong>
          </div>
        ))}
      </div>
      {metric.synonyms?.length > 0 && (
        <div className="border-t border-line pt-3">
          <span className="text-[11px] font-semibold text-muted">业务同义词</span>
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {metric.synonyms.map((synonym) => (
              <i className="rounded-full border border-line bg-surface-2 px-2 py-0.5 text-[11px] not-italic text-ink-2" key={synonym}>{synonym}</i>
            ))}
          </div>
        </div>
      )}
      <div className="flex items-center gap-1.5 rounded-control bg-surface-2 px-2.5 py-2 text-[11px] text-muted">
        <Icon name="link" size={15} />已被 {metric.referenceCount ?? 0} 个业务流程引用
      </div>
      <div className="flex gap-2">
        {!metric.availableForAnalysis && <Button variant="primary" className="flex-1" onClick={onPublish}>审核并验证</Button>}
        <Button className="flex-1" onClick={onEdit}>编辑指标</Button>
        <Button
          variant={confirmingDelete ? "danger" : "ghost"}
          className={cn(!confirmingDelete && "text-red hover:bg-red-soft hover:text-red")}
          onClick={() => (confirmingDelete ? onDelete() : setConfirmingDelete(true))}
        >
          {confirmingDelete ? "确认删除" : "删除"}
        </Button>
      </div>
      {confirmingDelete && <p className="text-[11px] text-red">再次点击将删除该指标，引用它的分析模板将失效。</p>}
    </Card>
  );
}

const TOPIC_OPTIONS = ["质量分析", "生产分析", "设备分析", "库存分析"];

const inputClass = "h-9 w-full rounded-control border border-line bg-surface px-2.5 text-xs text-ink outline-none transition placeholder:text-faint focus:border-primary/50 focus:ring-2 focus:ring-primary/25";

function MetricEditorModal({ metric, sourceId, onClose, onSaved }) {
  const [form, setForm] = useState({
    name: metric?.name ?? "",
    topic: metric?.topic ?? "质量分析",
    formula: metric?.formula ?? "",
    physicalFormula: metric?.physicalFormula ?? "",
    source: metric?.source ?? "",
    timeField: metric?.timeField ?? "",
    owner: metric?.owner ?? "",
    definition: metric?.definition ?? "",
    synonyms: metric?.synonyms?.join("、") ?? "",
  });
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const set = (key) => (event) => setForm((current) => ({ ...current, [key]: event.target.value }));
  const valid = form.name.trim() && form.formula.trim() && form.physicalFormula.trim() && form.source.trim();

  const save = async () => {
    if (!valid || saving) return;
    setSaving(true);
    setSaveError("");
    try {
      const saved = await saveMetric({
        ...(metric ?? {}),
        ...form,
        sourceId,
        name: form.name.trim(),
        owner: form.owner.trim() || "数据治理组",
        synonyms: form.synonyms.split(/[、,，]/).map((item) => item.trim()).filter(Boolean),
        status: metric?.status ?? "待审核",
        updated: "刚刚",
      });
      await onSaved(saved);
    } catch (error) {
      setSaveError(error.message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal open onClose={onClose} label={metric ? "编辑指标" : "新增指标"} maxWidth={520}>
      <div className="border-b border-line px-5 py-4">
        <span className="text-[11px] font-semibold tracking-wide text-faint">{metric ? metric.id : "新建知识项 · 指标口径"}</span>
        <h3 className="mt-1 text-base font-semibold tracking-tight text-ink">{metric ? `编辑指标：${metric.name}` : "新增指标口径"}</h3>
      </div>
      <div className="grid gap-3.5 px-5 py-4">
        <div className="grid gap-3.5 sm:grid-cols-2">
          <label className="grid gap-1.5 text-[11px] font-semibold text-muted">
            指标名称 *
            <input className={inputClass} value={form.name} onChange={set("name")} placeholder="如：设备可用率" />
          </label>
          <label className="grid gap-1.5 text-[11px] font-semibold text-muted">
            所属主题
            <select className={cn(inputClass, "cursor-pointer font-normal")} value={form.topic} onChange={set("topic")}>
              {TOPIC_OPTIONS.map((topic) => <option key={topic} value={topic}>{topic}</option>)}
            </select>
          </label>
        </div>
        <label className="grid gap-1.5 text-[11px] font-semibold text-muted">
          企业统一业务公式 *
          <input className={cn(inputClass, "font-mono")} value={form.formula} onChange={set("formula")} placeholder="如：运行时长 / (运行时长 + 停机时长) × 100%" />
        </label>
        <div className="rounded-control border border-line bg-surface-2 p-3">
          <span className="text-[11px] font-semibold text-muted">当前数据源物理绑定</span>
          <p className="mt-0.5 text-[10px] text-faint">物理公式、表和时间字段仅影响当前选中的数据源，不会覆盖其他工厂。</p>
          <label className="mt-3 grid gap-1.5 text-[11px] font-semibold text-muted">
            物理计算表达式 *
            <input className={cn(inputClass, "font-mono")} value={form.physicalFormula} onChange={set("physicalFormula")} placeholder="如：SUM(good_qty) / NULLIF(SUM(input_qty), 0) * 100" />
          </label>
        </div>
        <div className="grid gap-3.5 sm:grid-cols-2">
          <label className="grid gap-1.5 text-[11px] font-semibold text-muted">
            数据来源表 *
            <input className={cn(inputClass, "font-mono")} value={form.source} onChange={set("source")} placeholder="如：eqp_downtime_record" />
          </label>
          <label className="grid gap-1.5 text-[11px] font-semibold text-muted">
            物理时间字段
            <input className={cn(inputClass, "font-mono")} value={form.timeField} onChange={set("timeField")} placeholder="如：stat_date" />
          </label>
        </div>
        <div className="grid gap-3.5 sm:grid-cols-2">
          <label className="grid gap-1.5 text-[11px] font-semibold text-muted">
            负责人
            <input className={inputClass} value={form.owner} onChange={set("owner")} placeholder="如：设备部" />
          </label>
        </div>
        <label className="grid gap-1.5 text-[11px] font-semibold text-muted">
          业务定义
          <textarea
            rows={3}
            className="w-full resize-none rounded-control border border-line bg-surface px-2.5 py-2 text-xs font-normal text-ink outline-none transition placeholder:text-faint focus:border-primary/50 focus:ring-2 focus:ring-primary/25"
            value={form.definition}
            onChange={set("definition")}
            placeholder="说明该指标衡量什么、用于哪些业务判断"
          />
        </label>
        <label className="grid gap-1.5 text-[11px] font-semibold text-muted">
          业务同义词
          <input className={inputClass} value={form.synonyms} onChange={set("synonyms")} placeholder="用顿号分隔，如：开动率、稼动率" />
        </label>
        {!metric && <p className="text-[11px] text-faint">新指标默认进入「待审核」状态，发布前不会参与智能问析。</p>}
        {saveError && <p role="alert" className="text-[11px] text-red">{saveError}</p>}
      </div>
      <div className="flex items-center justify-end gap-2 border-t border-line bg-surface-2 px-5 py-3">
        <Button size="sm" onClick={onClose}>取消</Button>
        <Button size="sm" variant="primary" onClick={save} disabled={!valid || saving}>
          {saving ? "保存中" : metric ? "保存修改" : "创建指标"}
        </Button>
      </div>
    </Modal>
  );
}

function KnowledgeObjects({ objects }) {
  const [expandedId, setExpandedId] = useState(null);
  return (
    <div className="grid gap-3.5 sm:grid-cols-2 xl:grid-cols-3">
      {objects.map((item) => (
        <Card key={item.id} className="grid gap-1.5 p-4 transition-shadow hover:shadow-card-hover">
          <div className="flex items-center justify-between text-muted">
            <Icon name="knowledge" size={18} />
            <span className="font-mono text-[10px] tracking-wide text-faint">{item.id}</span>
          </div>
          <h3 className="mt-1 text-body font-semibold text-ink">{item.name}</h3>
          <p className="text-xs leading-relaxed text-muted">{item.description}</p>
          <small className="text-[11px] text-faint">{item.relationCount} 条语义关系</small>
          <button type="button" aria-expanded={expandedId === item.id} onClick={() => setExpandedId((current) => current === item.id ? null : item.id)} className="mt-1 flex cursor-pointer items-center gap-1 text-xs font-medium text-primary transition-opacity hover:opacity-75">
            查看关系 <Icon name="arrow" size={13} />
          </button>
          {expandedId === item.id && <p className="rounded-control bg-surface-2 px-2.5 py-2 text-[11px] leading-5 text-muted">已登记 {item.relationCount} 条语义关系，所属主题：{item.topic}。</p>}
        </Card>
      ))}
    </div>
  );
}

function KnowledgeRules({ rules }) {
  const [expandedId, setExpandedId] = useState(null);
  return (
    <Card className="grid gap-1 p-4">
      {rules.map((rule) => (
        <div key={rule.id} className="grid grid-cols-[auto_minmax(0,1fr)_auto_auto] items-center gap-3 border-t border-line px-1 py-3 first:border-t-0">
          <span className="rounded-tag bg-surface-3 px-1.5 py-1 font-mono text-[10px] font-semibold text-ink-2">{rule.id}</span>
          <div className="min-w-0">
            <strong className="block truncate text-body font-semibold text-ink">{rule.name}</strong>
            <small className="mt-0.5 block text-xs text-faint">{rule.type} · {rule.scope}</small>
          </div>
          <StatusPill tone="green">已启用</StatusPill>
          <button type="button" aria-label={`查看规则 ${rule.id}`} aria-expanded={expandedId === rule.id} onClick={() => setExpandedId((current) => current === rule.id ? null : rule.id)} className="grid h-7 w-7 cursor-pointer place-items-center rounded-control text-muted transition-colors hover:bg-surface-3 hover:text-ink">
            <Icon name="chevron" size={15} />
          </button>
          {expandedId === rule.id && <p className="col-start-2 col-end-5 -mt-1 text-[11px] leading-5 text-muted">规则范围：{rule.scope} · 类型：{rule.type}</p>}
        </div>
      ))}
    </Card>
  );
}

function KnowledgeTopics({ topics }) {
  return (
    <div className="grid gap-3.5 lg:grid-cols-3">
      {topics.map((topic) => (
        <Card key={topic.code} className="grid gap-2 p-4 transition-shadow hover:shadow-card-hover">
          <div className={cn("w-max rounded-tag px-1.5 py-1 font-mono text-[10px] font-semibold tracking-wide", topicTones[topic.color] ?? topicTones.blue)}>{topic.code}</div>
          <h3 className="text-body font-semibold text-ink">{topic.name}</h3>
          <p className="text-xs leading-relaxed text-muted">{topic.description}</p>
          <div className="flex flex-wrap gap-1.5">
            {topic.metrics.map((metric) => (
              <span key={metric} className="rounded-full border border-line bg-surface-2 px-2 py-0.5 text-[11px] text-ink-2">{metric}</span>
            ))}
          </div>
          <div className="mt-1 flex items-center justify-between border-t border-line pt-2.5">
            <span className="text-[11px] text-faint">{topic.coverage}% 语义覆盖</span>
            <button type="button" className="flex cursor-pointer items-center gap-1 text-xs font-medium text-primary transition-opacity hover:opacity-75">
              进入主题 <Icon name="arrow" size={13} />
            </button>
          </div>
        </Card>
      ))}
    </div>
  );
}
