import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { listSources } from "../../api/datasources";
import {
  createReport,
  deleteReportComposition,
  getReportComposerCatalog,
  listReportCompositions,
  previewReportComposition,
  saveReportComposition,
} from "../../api/reports";
import {
  ComboMetricChart,
  HorizontalRankChart,
  RankBarChart,
  ShareDonutChart,
  TrendLineChart,
} from "../../components/charts";
import { Button, ErrorNotice, Icon, Modal, PageFrame, RouteSkeleton } from "../../components/ui";
import cn from "../../lib/cn";
import {
  addMetricBlock,
  CHART_OPTIONS,
  DEFAULT_COMPOSITION,
  fromStarter,
  moveBlock,
  previewSeries,
  removeBlock,
  updateBlock,
} from "../../features/reports/reportComposerModel";

const ACTIVE_SOURCE_KEY = "forgesight:active-source-id";
const ACCENTS = [
  ["default", "默认", "#2c6576"],
  ["ocean", "深海", "#25677a"],
  ["teal", "青绿", "#17766b"],
  ["violet", "紫灰", "#695b91"],
  ["graphite", "石墨", "#58636d"],
];
const ACCENT_COLOR = Object.fromEntries(ACCENTS.map(([key, , color]) => [key, color]));

function localDate(date) {
  const shifted = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
  return shifted.toISOString().slice(0, 10);
}

function initialRange() {
  const end = new Date();
  const start = new Date(end);
  start.setDate(start.getDate() - 6);
  return { start: localDate(start), end: localDate(end) };
}

export default function ReportComposerPage() {
  const navigate = useNavigate();
  const [sources, setSources] = useState([]);
  const [sourceId, setSourceId] = useState(null);
  const [catalog, setCatalog] = useState(null);
  const [savedTemplates, setSavedTemplates] = useState([]);
  const [saved, setSaved] = useState(null);
  const [composition, setComposition] = useState(DEFAULT_COMPOSITION);
  const [selectedTable, setSelectedTable] = useState("");
  const [selectedBlockId, setSelectedBlockId] = useState("");
  const [range, setRange] = useState(initialRange);
  const [preview, setPreview] = useState(null);
  const [view, setView] = useState("design");
  const [leftMode, setLeftMode] = useState("metrics");
  const [relationshipOpen, setRelationshipOpen] = useState(false);
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState(null);
  const [dragIndex, setDragIndex] = useState(null);

  const loadSource = async (nextSourceId) => {
    setCatalog(null);
    setError(null);
    setPreview(null);
    try {
      const [nextCatalog, templates] = await Promise.all([
        getReportComposerCatalog(nextSourceId),
        listReportCompositions(nextSourceId),
      ]);
      setCatalog(nextCatalog);
      setSavedTemplates(templates);
      setSaved(null);
      setComposition(fromStarter(nextCatalog.starters?.[0] ?? { id: "starter-blank", blocks: [] }));
      setSelectedTable(nextCatalog.tables?.find((table) => table.metricIds?.length)?.qualifiedName ?? nextCatalog.tables?.[0]?.qualifiedName ?? "");
      setSelectedBlockId("");
      setView("design");
      if (nextCatalog.suggestedRange) setRange(nextCatalog.suggestedRange);
    } catch (nextError) {
      setError(nextError);
    }
  };

  useEffect(() => {
    let active = true;
    listSources()
      .then((items) => {
        if (!active) return;
        const stored = Number(window.sessionStorage.getItem(ACTIVE_SOURCE_KEY));
        const source = items.find((item) => item.id === stored) ?? items.find((item) => item.isDefault) ?? items[0];
        setSources(items);
        setSourceId(source?.id ?? null);
        if (source) return loadSource(source.id);
        setCatalog({ metrics: [], tables: [], relations: [], starters: [] });
        return undefined;
      })
      .catch((nextError) => active && setError(nextError));
    return () => { active = false; };
  }, []);

  const metricById = useMemo(
    () => Object.fromEntries((catalog?.metrics ?? []).map((metric) => [metric.id, metric])),
    [catalog],
  );
  const activeTable = catalog?.tables?.find((table) => table.qualifiedName === selectedTable);
  const tableMetrics = (activeTable?.metricIds ?? []).map((id) => metricById[id]).filter(Boolean);
  const selectedBlock = composition.blocks.find((block) => block.id === selectedBlockId);

  const changeSource = (value) => {
    const next = Number(value);
    setSourceId(next);
    window.sessionStorage.setItem(ACTIVE_SOURCE_KEY, String(next));
    loadSource(next);
  };

  const applyStarter = (starter) => {
    const next = fromStarter(starter);
    setComposition(next);
    setSelectedBlockId(next.blocks[0]?.id ?? "");
    setPreview(null);
    setView("design");
    setNotice(starter.id === "starter-blank" ? "已切换为空白周报" : `已载入${starter.name}，仍可自由增删内容`);
  };

  const add = (metric, kind, dimension) => {
    const next = addMetricBlock(composition, metric, kind, dimension);
    setComposition(next);
    setSelectedBlockId(next.blocks.at(-1).id);
    setPreview(null);
    setView("design");
  };

  const save = async () => {
    setBusy("save");
    setNotice("");
    try {
      const result = await saveReportComposition(sourceId, composition, saved);
      setSaved(result);
      setSavedTemplates(await listReportCompositions(sourceId));
      setNotice("个人模板已保存，仅在当前企业、账号和数据源下可见");
    } catch (nextError) {
      setNotice(nextError.message);
    } finally {
      setBusy("");
    }
  };

  const runPreview = async () => {
    if (!composition.blocks.length) {
      setNotice("先从关系图中添加至少一个指标内容");
      return;
    }
    if (!range.start || !range.end || range.end < range.start) {
      setNotice("请选择有效的周报起止日期");
      return;
    }
    setBusy("preview");
    setNotice("");
    try {
      const result = await previewReportComposition({
        sourceId,
        periodStart: `${range.start}T00:00:00`,
        periodEnd: `${range.end}T23:59:59`,
        composition,
      });
      setPreview(result);
      setView("preview");
    } catch (nextError) {
      setNotice(nextError.message);
    } finally {
      setBusy("");
    }
  };

  const generate = async () => {
    if (!composition.blocks.length) {
      setNotice("生成周报至少需要一个指标内容");
      return;
    }
    if (!range.start || !range.end || range.end < range.start) {
      setNotice("请选择有效的周报起止日期");
      return;
    }
    setBusy("generate");
    setNotice("");
    try {
      const report = await createReport({
        templateId: "tpl-operations-weekly",
        sourceId,
        periodStart: `${range.start}T00:00:00`,
        periodEnd: `${range.end}T23:59:59`,
        title: composition.name,
        emailEnabled: true,
        composition,
      });
      navigate(`/workspace/reports/${report.id}`);
    } catch (nextError) {
      setNotice(nextError.message);
    } finally {
      setBusy("");
    }
  };

  const loadSaved = (template) => {
    setSaved(template);
    setComposition(template.composition);
    setSelectedBlockId(template.composition.blocks?.[0]?.id ?? "");
    setPreview(null);
    setNotice(`已打开“${template.composition.name}”`);
  };

  const removeSaved = async () => {
    if (!saved) return;
    setBusy("delete");
    try {
      await deleteReportComposition(sourceId, saved.id);
      setSavedTemplates((items) => items.filter((item) => item.id !== saved.id));
      setSaved(null);
      setNotice("个人模板已删除，历史周报不受影响");
    } catch (nextError) {
      setNotice(nextError.message);
    } finally {
      setBusy("");
    }
  };

  if (error) return <PageFrame><ErrorNotice title="周报工作台加载失败" detail={error.message} onRetry={() => sourceId && loadSource(sourceId)} /></PageFrame>;
  if (!catalog) return <RouteSkeleton />;

  return (
    <PageFrame className="max-w-[1600px] gap-3 pb-8">
      <header className="flex flex-wrap items-end justify-between gap-5 border-b border-line pb-3">
        <div className="flex min-w-0 items-start gap-3">
          <button type="button" onClick={() => navigate("/workspace/reports")} className="mt-1 grid h-8 w-8 shrink-0 place-items-center rounded-control text-muted hover:bg-surface-2 hover:text-ink" aria-label="返回周报中心">
            <Icon name="chevron" size={15} className="rotate-180" />
          </button>
          <div>
            <p className="section-kicker">REPORT DESIGNER</p>
            <h1 className="mt-1 text-[25px] font-semibold tracking-tight text-ink">周报设计器</h1>
            <p className="mt-1 max-w-2xl text-sm text-muted">选数据、排版面、看结果，在一个工作面完成。</p>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button onClick={save} disabled={busy || !composition.name.trim()}>{busy === "save" ? "保存中…" : saved ? "保存更新" : "保存为个人模板"}</Button>
          <Button onClick={runPreview} disabled={Boolean(busy)}>{busy === "preview" ? "查询中…" : "预览数据"}</Button>
          <Button variant="primary" onClick={generate} disabled={Boolean(busy)}>{busy === "generate" ? "正在创建…" : "生成周报"}</Button>
        </div>
      </header>

      <div className="grid gap-3 border-b border-line pb-3 lg:grid-cols-[minmax(240px,1fr)_minmax(260px,1.15fr)_minmax(330px,1.3fr)]">
        <Field label="周报名称">
          <input className="input-control h-9" value={composition.name} onChange={(event) => setComposition({ ...composition, name: event.target.value })} />
        </Field>
        <Field label="数据源">
          <select className="input-control h-9" value={sourceId ?? ""} onChange={(event) => changeSource(event.target.value)}>
            {sources.map((source) => <option key={source.id} value={source.id}>{source.name}</option>)}
          </select>
        </Field>
        <div className="grid grid-cols-2 gap-2">
          <Field label="周期开始"><input type="date" className="input-control h-9" value={range.start} onChange={(event) => setRange({ ...range, start: event.target.value })} /></Field>
          <Field label="周期结束"><input type="date" className="input-control h-9" value={range.end} min={range.start} onChange={(event) => setRange({ ...range, end: event.target.value })} /></Field>
        </div>
      </div>

      {notice && <p className="rounded-control border border-line bg-surface-2 px-3 py-2 text-sm text-muted" role="status">{notice}</p>}

      <section className="grid min-h-[620px] overflow-hidden rounded-section border border-line bg-paper xl:h-[calc(100vh-245px)] xl:grid-cols-[250px_minmax(0,1fr)_290px]">
        <aside className="min-w-0 border-b border-line xl:overflow-y-auto xl:border-b-0 xl:border-r">
          <div className="flex h-12 items-center gap-1 border-b border-line px-3">
            <WorkspaceTab active={leftMode === "metrics"} onClick={() => setLeftMode("metrics")}>数据内容</WorkspaceTab>
            <WorkspaceTab active={leftMode === "templates"} onClick={() => setLeftMode("templates")}>模板</WorkspaceTab>
          </div>
          {leftMode === "metrics" ? (
            <>
              <div className="grid gap-2 border-b border-line p-3">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-[11px] font-semibold text-muted">业务对象</span>
                  <button type="button" onClick={() => setRelationshipOpen(true)} className="inline-flex items-center gap-1 text-[11px] font-semibold text-primary hover:opacity-75"><Icon name="model" size={12} />关系图</button>
                </div>
                <select className="input-control h-9" value={selectedTable} onChange={(event) => setSelectedTable(event.target.value)}>
                  {catalog.tables.map((table) => <option key={table.qualifiedName} value={table.qualifiedName}>{table.cnName || table.name} · {table.metricIds.length} 指标</option>)}
                </select>
              </div>
              <DataInspector table={activeTable} metrics={tableMetrics} onAdd={add} compact />
            </>
          ) : (
            <TemplateLibrary starters={catalog.starters} savedTemplates={savedTemplates} saved={saved} onStarter={applyStarter} onSaved={loadSaved} onDelete={removeSaved} deleting={busy === "delete"} />
          )}
        </aside>

        <div className="flex min-w-0 flex-col border-b border-line xl:border-b-0 xl:border-r">
          <div className="flex h-12 shrink-0 items-center justify-between gap-3 border-b border-line px-4">
            <div className="flex items-center gap-1 rounded-control bg-surface-2 p-0.5">
              <WorkspaceTab active={view === "design"} onClick={() => setView("design")}>版面设计</WorkspaceTab>
              <WorkspaceTab active={view === "preview"} onClick={() => setView("preview")}>数据预览</WorkspaceTab>
            </div>
            <span className="font-mono text-[11px] text-faint">{composition.blocks.length}/24 项内容</span>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto bg-surface-2/35 p-4 sm:p-5">
            {view === "design" ? (
              <CompositionCanvas
                composition={composition}
                metricById={metricById}
                selectedBlockId={selectedBlockId}
                dragIndex={dragIndex}
                onSelect={setSelectedBlockId}
                onDragStart={setDragIndex}
                onDrop={(index) => {
                  if (dragIndex != null) setComposition(moveBlock(composition, dragIndex, index));
                  setDragIndex(null);
                  setPreview(null);
                }}
                onMove={(index, to) => { setComposition(moveBlock(composition, index, to)); setPreview(null); }}
                onRemove={(block) => {
                  setComposition(removeBlock(composition, block.id));
                  if (selectedBlockId === block.id) setSelectedBlockId("");
                  setPreview(null);
                }}
              />
            ) : (
              <PreviewCanvas preview={preview} onRun={runPreview} busy={busy === "preview"} />
            )}
          </div>
        </div>

        <div className="min-w-0 xl:overflow-y-auto">
          <div className="flex h-12 items-center border-b border-line px-4"><h2 className="text-xs font-semibold text-ink">属性</h2></div>
          <BlockSettings block={selectedBlock} metric={selectedBlock ? metricById[selectedBlock.metricId] : null} onChange={(changes) => {
            setComposition(updateBlock(composition, selectedBlock.id, changes));
            setPreview(null);
          }} />
        </div>
      </section>

      <Modal open={relationshipOpen} onClose={() => setRelationshipOpen(false)} maxWidth={1180} label="业务数据关系图">
        <div className="flex items-start justify-between border-b border-line px-5 py-4">
          <div><p className="section-kicker">DATA RELATIONSHIP</p><h2 className="mt-1 text-lg font-semibold text-ink">从业务关系中选择数据</h2><p className="mt-1 text-xs text-muted">关系用于理解来源；只有已发布、已验证的指标可以加入周报。</p></div>
          <button type="button" onClick={() => setRelationshipOpen(false)} className="grid h-8 w-8 place-items-center rounded-control text-muted hover:bg-surface-2" aria-label="关闭关系图"><Icon name="close" size={15} /></button>
        </div>
        <div className="grid max-h-[72vh] min-h-[560px] lg:grid-cols-[minmax(0,1fr)_330px]">
          <div className="min-w-0 overflow-auto border-b border-line lg:border-b-0 lg:border-r"><RelationshipMap tables={catalog.tables} relations={catalog.relations} selected={selectedTable} onSelect={setSelectedTable} /></div>
          <div className="overflow-y-auto"><DataInspector table={activeTable} metrics={tableMetrics} onAdd={add} /></div>
        </div>
      </Modal>
    </PageFrame>
  );
}

function Field({ label, children }) {
  return <label className="grid gap-1 text-[11px] font-semibold text-muted"><span>{label}</span>{children}</label>;
}

function WorkspaceTab({ active, onClick, children }) {
  return <button type="button" aria-pressed={active} onClick={onClick} className={cn("min-h-8 rounded-control px-2.5 text-xs font-medium transition-colors", active ? "bg-paper text-ink shadow-[0_1px_3px_rgb(24_35_41_/10%)]" : "text-muted hover:text-ink")}>{children}</button>;
}

function TemplateLibrary({ starters, savedTemplates, saved, onStarter, onSaved, onDelete, deleting }) {
  return (
    <div>
      <div className="border-b border-line px-3 py-3"><p className="text-[11px] font-semibold text-muted">起始模板</p><p className="mt-1 text-[10px] leading-4 text-faint">只负责快速起步，载入后仍可自由混搭内容。</p></div>
      <div className="divide-y divide-line">
        {starters.map((starter) => <button key={starter.id} type="button" onClick={() => onStarter(starter)} disabled={!starter.blocks.length && starter.id !== "starter-blank"} className="group w-full px-3 py-3 text-left hover:bg-surface-2 disabled:cursor-not-allowed disabled:opacity-40"><span className="flex items-center justify-between gap-2"><strong className="text-xs font-semibold text-ink">{starter.name}</strong><Icon name="arrow" size={12} className="text-faint group-hover:text-primary" /></span><span className="mt-1 block text-[10px] leading-4 text-muted">{starter.description}</span></button>)}
      </div>
      <div className="border-y border-line bg-surface-2 px-3 py-2 text-[11px] font-semibold text-muted">我的模板</div>
      {savedTemplates.length ? <div className="divide-y divide-line">{savedTemplates.map((template) => <button key={template.id} type="button" onClick={() => onSaved(template)} className={cn("w-full px-3 py-3 text-left hover:bg-surface-2", saved?.id === template.id && "bg-primary-soft/50")}><strong className="block truncate text-xs text-ink">{template.composition.name}</strong><span className="mt-1 block text-[10px] text-muted">{template.composition.blocks?.length ?? 0} 项内容 · V{template.version}</span></button>)}</div> : <p className="px-3 py-4 text-xs leading-5 text-muted">还没有个人模板。配置好版面后，可在顶部保存。</p>}
      {saved && <button type="button" onClick={onDelete} disabled={deleting} className="mx-3 my-3 text-[11px] font-semibold text-danger hover:underline">{deleting ? "删除中…" : "删除当前个人模板"}</button>}
    </div>
  );
}

function RelationshipMap({ tables, relations, selected, onSelect }) {
  const width = 720;
  const columns = 3;
  const positions = Object.fromEntries(tables.map((table, index) => [table.qualifiedName, { x: 24 + (index % columns) * 232, y: 24 + Math.floor(index / columns) * 124 }]));
  const resolve = (qualified, plain) => positions[qualified] ?? Object.entries(positions).find(([name]) => name === plain || name.endsWith(`.${plain}`))?.[1];
  const height = Math.max(250, Math.ceil(tables.length / columns) * 124 + 28);
  return (
    <div className="overflow-auto border-t border-line bg-surface-2/35">
      <div className="relative mx-auto" style={{ width, height }}>
        <svg className="pointer-events-none absolute inset-0" width={width} height={height} aria-hidden="true">
          {relations.map((relation, index) => {
            const from = resolve(relation.fromQualifiedTable, relation.fromTable);
            const to = resolve(relation.toQualifiedTable, relation.toTable);
            if (!from || !to) return null;
            return <path key={`${relation.fromTable}-${relation.toTable}-${index}`} d={`M ${from.x + 96} ${from.y + 46} C ${from.x + 150} ${from.y + 46}, ${to.x + 42} ${to.y + 46}, ${to.x + 96} ${to.y + 46}`} fill="none" stroke="var(--color-line-strong)" strokeWidth="1.2" />;
          })}
        </svg>
        {tables.map((table) => {
          const point = positions[table.qualifiedName];
          return (
            <button key={table.qualifiedName} type="button" onClick={() => onSelect(table.qualifiedName)} style={{ left: point.x, top: point.y }} className={cn("absolute h-[92px] w-48 rounded-card border bg-paper p-3 text-left transition-[border-color,box-shadow]", selected === table.qualifiedName ? "border-primary shadow-[0_8px_22px_rgb(33_85_99_/12%)]" : "border-line hover:border-line-strong")}>
              <span className="block truncate text-xs font-semibold text-ink">{table.cnName || table.name}</span>
              <span className="mt-1 block truncate font-mono text-[10px] text-faint">{table.name}</span>
              <span className="mt-3 flex items-center justify-between text-[10px] text-muted"><span>{table.fields.length} 字段</span><strong className={table.metricIds.length ? "text-primary" : "text-faint"}>{table.metricIds.length} 指标</strong></span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

function DataInspector({ table, metrics, onAdd, compact = false }) {
  const [openMetric, setOpenMetric] = useState("");
  if (!table) return <aside className="p-4 text-sm text-muted">选择一个业务对象查看可用内容。</aside>;
  return (
    <aside className="min-w-0">
      <div className={cn("border-b border-line px-4 py-3", compact && "bg-surface-2/35")}>
        <span className="text-[10px] font-semibold text-primary">{table.topic || "业务对象"}</span>
        <h3 className="mt-1 truncate text-sm font-semibold text-ink">{table.cnName || table.name}</h3>
        <p className="mt-1 truncate font-mono text-[10px] text-faint">{table.qualifiedName}</p>
      </div>
      <div className="px-4 py-3">
        <p className="text-[11px] font-semibold text-muted">可用于周报的指标</p>
        {metrics.length ? (
          <div className="mt-2 divide-y divide-line border-y border-line">
            {metrics.map((metric) => (
              <div key={metric.id} className="py-3">
                <button type="button" onClick={() => setOpenMetric(openMetric === metric.id ? "" : metric.id)} className="flex w-full items-start justify-between gap-3 text-left">
                  <span className="min-w-0"><strong className="block text-xs text-ink">{metric.name}</strong><span className="mt-0.5 block line-clamp-2 text-[11px] leading-5 text-muted">{metric.definition || "企业已发布口径"}</span></span>
                  <Icon name="chevron" size={13} className={cn("mt-1 shrink-0 transition-transform", openMetric === metric.id && "rotate-90")} />
                </button>
                {openMetric === metric.id && (
                  <div className="mt-3 grid gap-2">
                    <div className="grid grid-cols-2 gap-2">
                      {metric.kinds.includes("kpi") && <MiniAction onClick={() => onAdd(metric, "kpi")}>指标概览</MiniAction>}
                      {metric.kinds.includes("trend") && <MiniAction onClick={() => onAdd(metric, "trend")}>趋势分析</MiniAction>}
                    </div>
                    {metric.kinds.includes("rank") && metric.dimensions.length > 0 && (
                      <div>
                        <p className="mb-1.5 text-[10px] text-faint">按维度分析</p>
                        <div className="flex flex-wrap gap-1.5">
                          {metric.dimensions.map((dimension) => <MiniAction key={dimension} onClick={() => onAdd(metric, "breakdown", dimension)}>{dimension}</MiniAction>)}
                        </div>
                      </div>
                    )}
                    <p className="text-[10px] text-faint">单位 {metric.unit || "按指标口径"} · 口径 V{metric.version} · 绑定 V{metric.bindingVersion}</p>
                  </div>
                )}
              </div>
            ))}
          </div>
        ) : <p className="mt-4 text-xs leading-5 text-muted">这个对象还没有已发布且完成物理绑定的指标。它仍显示在关系中，便于理解数据来源，但不能直接加入周报。</p>}
        <details className="mt-3 text-xs text-muted">
          <summary className="cursor-pointer py-2 font-medium text-primary">查看字段元数据</summary>
          <div className="max-h-48 overflow-auto divide-y divide-line border-y border-line">
            {table.fields.map((field) => <div key={field.name} className="flex items-center justify-between gap-3 py-2"><span className="truncate">{field.cnName || field.name}</span><code className="shrink-0 text-[10px] text-faint">{field.name}</code></div>)}
          </div>
        </details>
      </div>
    </aside>
  );
}

function MiniAction({ onClick, children }) {
  return <button type="button" onClick={onClick} className="min-h-8 rounded-control border border-line px-2.5 py-1 text-left text-[11px] font-medium text-ink hover:border-primary hover:text-primary">+ {children}</button>;
}

function CompositionCanvas({ composition, metricById, selectedBlockId, dragIndex, onSelect, onDragStart, onDrop, onMove, onRemove }) {
  return (
    <div className="mx-auto max-w-4xl">
      <div className="mb-3 flex items-start justify-between gap-4 px-1">
        <div><p className="text-[11px] font-semibold text-muted">周报版面</p><h2 className="mt-0.5 text-base font-semibold text-ink">{composition.name}</h2></div>
        <span className="text-[10px] text-faint">拖动内容可调整阅读顺序</span>
      </div>
      {composition.blocks.length ? (
        <div className="grid grid-cols-1 gap-3 rounded-section border border-line bg-paper p-3 shadow-[0_10px_30px_rgb(24_35_41_/5%)] sm:grid-cols-2 sm:p-4">
          {composition.blocks.map((block, index) => (
            <CanvasBlock
              key={block.id}
              block={block}
              metric={metricById[block.metricId]}
              index={index}
              count={composition.blocks.length}
              active={block.id === selectedBlockId}
              dragging={dragIndex === index}
              onSelect={() => onSelect(block.id)}
              onMove={(to) => onMove(index, to)}
              onRemove={() => onRemove(block)}
              onDragStart={() => onDragStart(index)}
              onDrop={() => onDrop(index)}
            />
          ))}
        </div>
      ) : (
        <div className="grid min-h-[420px] place-content-center rounded-section border border-dashed border-line-strong bg-paper px-6 text-center">
          <span className="mx-auto grid h-10 w-10 place-items-center rounded-control bg-primary-soft text-primary"><Icon name="report" size={18} /></span>
          <p className="mt-3 text-sm font-semibold text-ink">从左侧加入第一项内容</p>
          <p className="mt-1 max-w-xs text-xs leading-5 text-muted">可以混合生产、质量、设备和库存指标，版面不会改变企业口径。</p>
        </div>
      )}
    </div>
  );
}

function CanvasBlock({ block, metric, index, count, active, dragging, onSelect, onMove, onRemove, onDragStart, onDrop }) {
  const labels = { kpi: "指标概览", trend: "趋势", breakdown: "维度分析" };
  const minHeight = { compact: "min-h-[108px]", standard: "min-h-[148px]", focus: "min-h-[196px]" }[block.height] ?? "min-h-[148px]";
  const chartLabel = CHART_OPTIONS[block.kind]?.find(([value]) => value === block.chart)?.[1];
  return (
    <article role="button" tabIndex={0} aria-label={`编辑${block.title}`} draggable onClick={onSelect} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") onSelect(); }} onDragStart={onDragStart} onDragOver={(event) => event.preventDefault()} onDrop={onDrop} className={cn("group relative flex cursor-pointer flex-col overflow-hidden rounded-card border bg-paper p-3 text-left transition-[border-color,box-shadow,opacity]", minHeight, block.width === "full" && "sm:col-span-2", active ? "border-primary shadow-[0_0_0_2px_rgb(37_103_122_/10%)]" : "border-line hover:border-line-strong", dragging && "opacity-45")}>
      <span className="absolute inset-x-0 top-0 h-0.5" style={{ background: ACCENT_COLOR[block.accent] ?? ACCENT_COLOR.default }} />
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0"><span className="text-[10px] font-semibold text-primary">{labels[block.kind]}</span><h3 className="mt-1 truncate text-sm font-semibold text-ink">{block.title}</h3></div>
        <Icon name="menu" size={14} className="mt-0.5 shrink-0 text-faint" />
      </div>
      <div className="mt-3 flex-1 border-t border-dashed border-line pt-3"><p className="truncate text-xs text-ink-2">{metric?.name ?? block.metricId}{block.dimension ? ` · 按${block.dimension}` : ""}</p><p className="mt-1 text-[10px] text-muted">{block.kind === "trend" ? `${{ day: "按日", week: "按周", month: "按月" }[block.grain]} · ` : ""}{chartLabel} · {metric?.unit || "企业口径"}</p>{block.kind === "kpi" && <p className="mt-3 font-mono text-xl font-semibold text-faint">—</p>}</div>
      <div className="mt-2 flex items-center justify-between border-t border-line pt-2">
        <span className="text-[10px] text-faint">{block.width === "full" ? "通栏" : "半栏"} · {{ compact: "紧凑", standard: "标准", focus: "重点" }[block.height]}</span>
        <span className="flex items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
          <button type="button" onClick={(event) => { event.stopPropagation(); onMove(index - 1); }} disabled={index === 0} className="grid h-7 w-7 place-items-center rounded-control text-muted hover:bg-surface-2 disabled:opacity-25" aria-label="上移内容"><Icon name="chevron" size={12} className="-rotate-90" /></button>
          <button type="button" onClick={(event) => { event.stopPropagation(); onMove(index + 1); }} disabled={index === count - 1} className="grid h-7 w-7 place-items-center rounded-control text-muted hover:bg-surface-2 disabled:opacity-25" aria-label="下移内容"><Icon name="chevron" size={12} className="rotate-90" /></button>
          <button type="button" onClick={(event) => { event.stopPropagation(); onRemove(); }} className="grid h-7 w-7 place-items-center rounded-control text-muted hover:bg-danger-soft hover:text-danger" aria-label="删除内容"><Icon name="trash" size={12} /></button>
        </span>
      </div>
    </article>
  );
}

function BlockSettings({ block, metric, onChange }) {
  if (!block) return <aside className="grid min-h-52 place-content-center px-6 text-center"><p className="text-sm font-semibold text-ink">选择一项内容</p><p className="mt-1 text-xs text-muted">可调整图表、粒度、尺寸和强调色。</p></aside>;
  return (
    <aside className="grid content-start gap-4 p-4">
      <div><p className="text-[11px] font-semibold text-primary">内容设置</p><p className="mt-1 text-xs text-muted">{metric?.name} · {metric?.unit || "企业口径"}</p></div>
      <Field label="显示标题"><input className="input-control h-9" value={block.title} onChange={(event) => onChange({ title: event.target.value })} /></Field>
      {block.kind === "trend" && <Field label="时间粒度"><Segmented value={block.grain} options={[["day", "日"], ["week", "周"], ["month", "月"]]} onChange={(grain) => onChange({ grain })} /></Field>}
      {block.kind === "breakdown" && <Field label="分析维度"><select className="input-control h-9" value={block.dimension ?? ""} onChange={(event) => onChange({ dimension: event.target.value, title: `按${event.target.value}分析${metric.name}` })}>{metric?.dimensions?.map((dimension) => <option key={dimension}>{dimension}</option>)}</select></Field>}
      {block.kind === "breakdown" && <Field label="展示数量"><input type="number" min="2" max="40" className="input-control h-9" value={block.topN} onChange={(event) => onChange({ topN: Math.max(2, Math.min(40, Number(event.target.value) || 12)) })} /></Field>}
      <Field label="展示方式"><Segmented value={block.chart} options={CHART_OPTIONS[block.kind]} onChange={(chart) => onChange({ chart, ...(chart === "donut" && block.topN > 12 ? { topN: 12 } : {}) })} /></Field>
      <Field label="内容宽度"><Segmented value={block.width} options={[["half", "半栏"], ["full", "通栏"]]} onChange={(width) => onChange({ width })} /></Field>
      <Field label="内容高度"><Segmented value={block.height} options={[["compact", "紧凑"], ["standard", "标准"], ["focus", "重点"]]} onChange={(height) => onChange({ height })} /></Field>
      {block.kind === "kpi" && <label className="flex min-h-10 cursor-pointer items-center justify-between gap-3 border-y border-line py-2 text-xs text-ink"><span><strong className="block">与上一周期比较</strong><span className="mt-0.5 block text-[10px] text-muted">使用紧邻的等长周期</span></span><input type="checkbox" checked={block.compare} onChange={(event) => onChange({ compare: event.target.checked })} /></label>}
      <Field label="强调色"><div className="flex flex-wrap gap-2">{ACCENTS.map(([key, label, color]) => <button key={key} type="button" onClick={() => onChange({ accent: key })} className={cn("flex h-8 items-center gap-1.5 rounded-control px-2 text-[10px]", block.accent === key ? "bg-surface-3 text-ink" : "text-muted hover:bg-surface-2")}><span className="h-2.5 w-2.5 rounded-full" style={{ background: color }} />{label}</button>)}</div></Field>
    </aside>
  );
}

function Segmented({ value, options, onChange }) {
  return <div className="flex flex-wrap gap-1 rounded-control bg-surface-2 p-1">{options.map(([key, label]) => <button key={key} type="button" onClick={() => onChange(key)} className={cn("min-h-7 rounded-[6px] px-2 text-[10px] font-medium", value === key ? "bg-paper text-ink shadow-[0_1px_3px_rgb(24_35_41_/10%)]" : "text-muted hover:text-ink")}>{label}</button>)}</div>;
}

function PreviewCanvas({ preview, onRun, busy }) {
  if (!preview) return <div className="grid min-h-[420px] place-content-center rounded-section border border-dashed border-line-strong bg-paper px-6 text-center"><span className="mx-auto grid h-10 w-10 place-items-center rounded-control bg-primary-soft text-primary"><Icon name="chart" size={18} /></span><p className="mt-3 text-sm font-semibold text-ink">查看当前版面的真实数据</p><p className="mt-1 max-w-xs text-xs leading-5 text-muted">预览只执行受治理的只读查询，不会保存结果或启动 Agent。</p><Button className="mx-auto mt-4" onClick={onRun} disabled={busy}>{busy ? "查询中…" : "开始预览"}</Button></div>;
  return (
    <div className="mx-auto max-w-4xl">
      <div className="mb-3 flex items-end justify-between gap-3 px-1"><div><p className="text-[11px] font-semibold text-muted">真实数据预览</p><p className="mt-0.5 text-xs text-ink">{preview.range.start} 至 {preview.range.end}</p></div><span className="text-[10px] text-faint">结果不会保存到个人模板</span></div>
      <div className="grid gap-3 2xl:grid-cols-2">
        {preview.items.map((item) => <PreviewBlock key={item.block.id} item={item} />)}
      </div>
    </div>
  );
}

function PreviewBlock({ item }) {
  const data = previewSeries(item);
  const color = ACCENT_COLOR[item.block.accent] ?? ACCENT_COLOR.default;
  const height = { compact: 220, standard: 270, focus: 350 }[item.block.height] ?? 270;
  if (item.state !== "ready") return <article className="grid min-h-44 place-content-center rounded-card border border-line p-5 text-center"><strong className="text-sm text-ink">{item.block.title}</strong><p className="mt-1 text-xs text-muted">{item.message}</p></article>;
  if (item.block.kind === "kpi") {
    const current = data[0]?.value;
    const previous = item.previousRows?.[0]?.at(-1);
    const change = previous && Number(previous) !== 0 ? (current - Number(previous)) / Math.abs(Number(previous)) * 100 : null;
    return <article className={cn("rounded-card border border-line p-5", item.block.width === "full" && "2xl:col-span-2")}><p className="text-xs font-semibold text-muted">{item.block.title}</p><p className="mt-3 font-mono text-3xl font-semibold tracking-tight text-ink">{Number(current).toLocaleString("zh-CN", { maximumFractionDigits: 2 })}<span className="ml-1 text-sm font-normal text-muted">{item.metric.unit}</span></p>{change != null && <p className={cn("mt-2 text-xs font-medium", change >= 0 ? "text-success" : "text-danger")}>较上一周期 {change >= 0 ? "+" : ""}{change.toFixed(1)}%</p>}</article>;
  }
  let chart;
  if (item.block.chart === "donut") chart = <ShareDonutChart data={data.map((point) => ({ name: point.label, value: point.value }))} unit={item.metric.unit} centerTitle="合计" centerValue={data.reduce((sum, point) => sum + point.value, 0).toLocaleString("zh-CN")} height={height} />;
  else if (item.block.chart === "pareto") {
    const total = data.reduce((sum, point) => sum + Math.max(point.value, 0), 0);
    let cumulative = 0;
    const pareto = data.map((point) => ({ ...point, secondaryValue: total ? (cumulative += Math.max(point.value, 0)) / total * 100 : 0 }));
    chart = <ComboMetricChart data={pareto} xKey="label" bars={[{ key: "value", name: item.metric.name, color }]} lines={[{ key: "secondaryValue", name: "累计贡献率", color: "var(--color-red)" }]} leftUnit={item.metric.unit} rightUnit="%" height={height} />;
  } else if (item.block.chart === "column") chart = <RankBarChart data={data} xKey="label" bars={[{ key: "value", name: item.metric.name, color }]} unit={item.metric.unit} height={height} />;
  else if (item.block.kind === "breakdown") chart = <HorizontalRankChart data={data} color={color} unit={item.metric.unit} valueName={item.metric.name} height={height} />;
  else chart = <TrendLineChart data={data} xKey="label" series={[{ key: "value", name: item.metric.name, color }]} unit={item.metric.unit} area={item.block.chart === "area"} height={height} />;
  return <article className={cn("min-w-0 rounded-card border border-line p-4", item.block.width === "full" && "2xl:col-span-2")}><div className="mb-3"><h3 className="text-sm font-semibold text-ink">{item.block.title}</h3><p className="mt-1 text-[10px] text-muted">{item.metric.name} · {item.metric.unit || "企业口径"} · 查询结果 {item.rows.length} 行</p></div>{chart}</article>;
}
