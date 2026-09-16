import { useEffect, useMemo, useRef, useState } from "react";
import { useBlocker, useNavigate } from "react-router-dom";
import { clearReportDraft, readReportDraft, writeReportDraft } from "../../features/reports/reportDraft";

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
  CalendarHeatmap,
  ComboMetricChart,
  DataTableBars,
  HorizontalRankChart,
  RankBarChart,
  ShareDonutChart,
  TrendLineChart,
} from "../../components/charts";
import { Button, ErrorNotice, Icon, Modal, PageFrame, RouteSkeleton, StatusPill } from "../../components/ui";
import cn from "../../lib/cn";
import {
  addMetricBlock,
  BLOCK_LIMIT,
  CHART_OPTIONS,
  chartOptionsFor,
  DEFAULT_COMPOSITION,
  filterMetrics,
  fromStarter,
  groupMetricsByTopic,
  KIND_ICONS,
  KIND_LABELS,
  moveBlock,
  previewSeries,
  removeBlock,
  structureChecklist,
  TOPIC_COLORS,
  TOPIC_ORDER,
  topicOf,
  updateBlock,
  usageCounts,
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
const GRAIN_LABELS = { day: "按日", week: "按周", month: "按月" };
const HEIGHT_LABELS = { compact: "紧凑", standard: "标准", focus: "重点" };

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

function weekRange(offset) {
  const today = new Date();
  const day = (today.getDay() + 6) % 7;
  const monday = new Date(today);
  monday.setDate(today.getDate() - day + offset * 7);
  const sunday = new Date(monday);
  sunday.setDate(monday.getDate() + 6);
  return { start: localDate(monday), end: localDate(sunday) };
}

function presetRanges(suggested) {
  const presets = [
    { id: "this-week", label: "本周", range: weekRange(0) },
    { id: "last-week", label: "上周", range: weekRange(-1) },
    { id: "last-7", label: "近 7 天", range: initialRange() },
  ];
  if (suggested?.start && suggested?.end) {
    presets.push({ id: "data", label: "数据可覆盖期", range: { start: suggested.start, end: suggested.end } });
  }
  return presets;
}

function monthCells(year, month) {
  const first = new Date(year, month, 1);
  const offset = (first.getDay() + 6) % 7;
  const cells = [];
  for (let index = 0; index < 42; index += 1) {
    const day = new Date(year, month, 1 - offset + index);
    cells.push({ iso: localDate(day), inMonth: day.getMonth() === month });
  }
  return cells;
}

function rangeDays(range) {
  if (!range.start || !range.end || range.end < range.start) return 0;
  const from = new Date(`${range.start}T00:00:00`);
  const to = new Date(`${range.end}T00:00:00`);
  return Math.round((to - from) / 86400000) + 1;
}

export default function ReportComposerPage() {
  const navigate = useNavigate();
  const [sources, setSources] = useState([]);
  const [sourceId, setSourceId] = useState(null);
  const [catalog, setCatalog] = useState(null);
  const [savedTemplates, setSavedTemplates] = useState([]);
  const [saved, setSaved] = useState(null);
  const [dirty, setDirty] = useState(false);
  const [composition, setComposition] = useState(DEFAULT_COMPOSITION);
  const [selectedBlockId, setSelectedBlockId] = useState("");
  const [range, setRange] = useState(initialRange);
  const [periodOpen, setPeriodOpen] = useState(false);
  const [preview, setPreview] = useState(null);
  const [view, setView] = useState("design");
  const [leftMode, setLeftMode] = useState("metrics");
  const [metricQuery, setMetricQuery] = useState("");
  const [relationshipOpen, setRelationshipOpen] = useState(false);
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState(null);
  const [dragIndex, setDragIndex] = useState(null);
  const [overIndex, setOverIndex] = useState(null);
  const [mobilePanel, setMobilePanel] = useState("canvas");
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [pendingAction, setPendingAction] = useState(null);
  // 1280–1536px 区间属性面板是覆盖式抽屉：选中内容块时自动打开
  const selectBlock = (id) => {
    setSelectedBlockId(id);
    if (id) setInspectorOpen(true);
  };
  const loadSequence = useRef(0);
  const loadedSource = useRef(null);
  const latestComposition = useRef(composition);
  latestComposition.current = composition;
  const allowNavigation = useRef(false);
  const blocker = useBlocker(() => dirty && !allowNavigation.current);
  const canvasRef = useRef(null);

  useEffect(() => {
    if (!dirty) return undefined;
    const onUnload = (event) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", onUnload);
    return () => window.removeEventListener("beforeunload", onUnload);
  }, [dirty]);

  useEffect(() => {
    if (dirty && catalog && loadedSource.current === sourceId) {
      if (!writeReportDraft(sourceId, { composition, range, saved })) setNotice("浏览器无法保存草稿，请存为模板后再离开");
    }
  }, [dirty, composition, range, saved, sourceId, catalog]);

  const confirmAction = (title, action, always = false) => {
    if (busy) return;
    if (dirty || always) setPendingAction({ title, action });
    else action();
  };

  const loadSource = async (nextSourceId) => {
    const sequence = ++loadSequence.current;
    setCatalog(null);
    setError(null);
    setPreview(null);
    try {
      const [nextCatalog, templates] = await Promise.all([
        getReportComposerCatalog(nextSourceId),
        listReportCompositions(nextSourceId),
      ]);
      if (sequence !== loadSequence.current) return;
      loadedSource.current = nextSourceId;
      const draft = readReportDraft(nextSourceId);
      setCatalog(nextCatalog);
      setSavedTemplates(templates);
      setSaved(draft?.saved ?? null);
      setDirty(Boolean(draft));
      setComposition(draft?.composition ?? fromStarter(nextCatalog.starters?.find((item) => item.id === "starter-综合") ?? nextCatalog.starters?.[0] ?? { id: "starter-blank", blocks: [] }));
      setSelectedBlockId("");
      setView("design");
      setMetricQuery("");
      if (draft?.range || nextCatalog.suggestedRange) setRange(draft?.range ?? nextCatalog.suggestedRange);
      if (draft) setNotice("已恢复当前企业、账号及数据源下的未保存草稿；生成前会重新校验指标口径");
    } catch (nextError) {
      if (sequence === loadSequence.current) setError(nextError);
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
    return () => { active = false; loadSequence.current += 1; };
  }, []);

  const metricById = useMemo(
    () => Object.fromEntries((catalog?.metrics ?? []).map((metric) => [metric.id, metric])),
    [catalog],
  );
  const usedMetrics = useMemo(() => usageCounts(composition), [composition]);
  const selectedBlock = composition.blocks.find((block) => block.id === selectedBlockId);
  const activeSource = sources.find((source) => source.id === sourceId);

  const patch = (next) => {
    if (busy) return;
    setComposition(next);
    setDirty(true);
    setPreview(null);
  };

  const changeSource = (value) => confirmAction("切换数据源？当前修改会保留为该数据源的个人草稿。", () => {
    const next = Number(value);
    setSourceId(next);
    window.sessionStorage.setItem(ACTIVE_SOURCE_KEY, String(next));
    loadSource(next);
  });

  const applyStarter = (starter) => confirmAction("使用起始模板替换当前内容？这会覆盖当前编辑中的草稿。", () => {
    const next = fromStarter(starter);
    setComposition(next);
    setDirty(true);
    setSelectedBlockId(next.blocks[0]?.id ?? "");
    setPreview(null);
    setView("design");
    setNotice(starter.id === "starter-blank" ? "已切换为空白周报" : `已载入${starter.name.replace("起始模板", "")}，仍可自由增删内容`);
  });

  const add = (metric, kind, dimension) => {
    if (busy) return;
    if (composition.blocks.length >= BLOCK_LIMIT) {
      setNotice(`一份周报最多 ${BLOCK_LIMIT} 项内容，请先精简版面`);
      return;
    }
    const next = addMetricBlock(composition, metric, kind, dimension);
    if (next === composition) return;
    setComposition(next);
    setDirty(true);
    selectBlock(next.blocks.at(-1).id);
    setPreview(null);
    setView("design");
    setMobilePanel("canvas");
  };

  const focusMetric = (metricId) => {
    const block = composition.blocks.find((item) => item.metricId === metricId);
    if (!block) return;
    setView("design");
    selectBlock(block.id);
    canvasRef.current?.querySelector(`[data-block="${block.id}"]`)?.scrollIntoView({ block: "nearest" });
  };

  const save = async () => {
    const snapshot = composition;
    setBusy("save");
    setNotice("");
    try {
      const result = await saveReportComposition(sourceId, composition, saved);
      setSaved(result);
      if (latestComposition.current === snapshot) { setDirty(false); clearReportDraft(sourceId); }
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
      setNotice("先从左侧指标库添加至少一个内容");
      return;
    }
    if (!range.start || !range.end || range.end < range.start) {
      setNotice("请选择有效的统计起止日期");
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
      setNotice("请选择有效的统计起止日期");
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
      allowNavigation.current = true;
      navigate(`/workspace/reports/${report.id}`);
    } catch (nextError) {
      setNotice(nextError.message);
    } finally {
      setBusy("");
    }
  };

  const loadSaved = (template) => confirmAction("打开个人模板并替换当前内容？未保存的编辑会被覆盖。", () => {
    clearReportDraft(sourceId);
    setSaved(template);
    setComposition(template.composition);
    setDirty(false);
    setSelectedBlockId(template.composition.blocks?.[0]?.id ?? "");
    setPreview(null);
    setNotice(`已打开“${template.composition.name}”`);
  });

  const removeSaved = () => confirmAction("删除这个个人模板？历史周报不受影响，此操作无法撤销。", async () => {
    if (!saved) return;
    setBusy("delete");
    try {
      await deleteReportComposition(sourceId, saved.id);
      setSavedTemplates((items) => items.filter((item) => item.id !== saved.id));
      setSaved(null);
      setDirty(true);
      setNotice("个人模板已删除，当前内容已保留为本地草稿");
    } catch (nextError) {
      setNotice(nextError.message);
    } finally {
      setBusy("");
    }
  }, true);

  if (error) return <PageFrame><ErrorNotice title="周报工作台加载失败" detail={error.message} onRetry={() => sourceId && loadSource(sourceId)} /></PageFrame>;
  if (!catalog) return <RouteSkeleton />;

  const topicCounts = Object.fromEntries(TOPIC_ORDER.map((topic) => [topic, 0]));
  for (const block of composition.blocks) topicCounts[topicOf(metricById[block.metricId])] += 1;

  return (
    <div className="flex h-full min-h-0 flex-col bg-canvas">
      <header className="flex min-h-12 shrink-0 flex-wrap items-center gap-2 border-b border-line bg-paper px-3 py-2">
        <button type="button" onClick={() => navigate("/workspace/reports")} className="grid h-7 w-7 shrink-0 place-items-center rounded-control text-muted hover:bg-surface-2 hover:text-ink" aria-label="返回本周期报告">
          <Icon name="chevron" size={15} className="rotate-180" />
        </button>
        <span className="h-4 w-px shrink-0 bg-line" />
        <input
          className="min-w-0 max-w-[240px] flex-1 truncate rounded-control border border-transparent bg-transparent px-1.5 py-1 text-sm font-semibold text-ink hover:border-line focus:border-primary focus:bg-surface-2"
          value={composition.name}
          maxLength={100}
          aria-label="周报名称"
          onChange={(event) => patch({ ...composition, name: event.target.value })}
        />
        <SaveStatePill dirty={dirty} saved={saved} />
        <div className="ml-auto flex min-w-0 flex-wrap items-center gap-1.5">
          <label className="relative flex items-center">
            <Icon name="database" size={13} className="pointer-events-none absolute left-2 text-faint" />
            <select disabled={Boolean(busy)} className="input-control h-7 max-w-[150px] appearance-none pl-7 pr-6 text-xs" value={sourceId ?? ""} onChange={(event) => changeSource(event.target.value)} aria-label="数据源">
              {sources.map((source) => <option key={source.id} value={source.id}>{source.name}</option>)}
            </select>
          </label>
          <PeriodButton range={range} suggested={catalog.suggestedRange} open={periodOpen} onOpen={(open) => !busy && setPeriodOpen(open)} onApply={(next) => { if (busy) return; setRange(next); setDirty(true); setPreview(null); setPeriodOpen(false); }} />
          <span className="hidden h-4 w-px bg-line sm:block" />
          <Button size="sm" variant="ghost" onClick={save} disabled={Boolean(busy) || !composition.name.trim()}>{busy === "save" ? "保存中…" : "存为模板"}</Button>
          <Button size="sm" onClick={runPreview} disabled={Boolean(busy)}>{busy === "preview" ? "查询中…" : "预览"}</Button>
          <Button size="sm" variant="primary" onClick={generate} disabled={Boolean(busy)}>{busy === "generate" ? "正在创建…" : "生成周报"}</Button>
        </div>
      </header>

      {notice && (
        <div className="flex shrink-0 items-center justify-between gap-3 border-b border-line bg-primary-soft/70 px-4 py-1.5 text-xs text-ink">
          <span role="status">{notice}</span>
          <button type="button" onClick={() => setNotice("")} className="grid h-5 w-5 place-items-center rounded-control text-muted hover:bg-paper" aria-label="关闭提示"><Icon name="close" size={12} /></button>
        </div>
      )}

      <div className="flex shrink-0 gap-1 border-b border-line bg-paper px-3 py-1 xl:hidden" role="tablist" aria-label="周报工作区面板">
        {[["metrics", "指标与模板"], ["canvas", "报告画布"], ["properties", selectedBlock ? "内容属性" : "报告概览"]].map(([panel, label]) => <button type="button" role="tab" aria-selected={mobilePanel === panel} key={panel} onClick={() => setMobilePanel(panel)} className={cn("min-h-11 rounded-control px-3 text-xs", mobilePanel === panel ? "bg-primary-soft font-semibold text-primary" : "text-muted")}>{label}</button>)}
      </div>
      <div className="relative flex min-h-0 flex-1">
        <aside className={cn("min-h-0 w-full shrink-0 flex-col overflow-auto border-line bg-paper xl:flex xl:w-[268px] xl:border-r", mobilePanel === "metrics" ? "flex" : "hidden")}>
          <div className="flex h-9 shrink-0 items-center gap-1 border-b border-line px-2">
            <SideTab active={leftMode === "metrics"} onClick={() => setLeftMode("metrics")}>指标库</SideTab>
            <SideTab active={leftMode === "templates"} onClick={() => setLeftMode("templates")}>模板</SideTab>
          </div>
          {leftMode === "metrics" ? (
            <MetricLibrary
              metrics={catalog.metrics}
              query={metricQuery}
              onQuery={setMetricQuery}
              usedMetrics={usedMetrics}
              metricById={metricById}
              onAdd={add}
              onJump={focusMetric}
              onOpenRelationships={() => setRelationshipOpen(true)}
            />
          ) : (
            <TemplateLibrary starters={catalog.starters} savedTemplates={savedTemplates} saved={saved} onStarter={applyStarter} onSaved={loadSaved} onDelete={removeSaved} deleting={busy === "delete"} />
          )}
        </aside>

        <section className={cn("min-h-0 min-w-0 flex-1 flex-col xl:flex", mobilePanel === "canvas" ? "flex" : "hidden")}>
          <div className="flex h-9 shrink-0 items-center justify-between gap-3 border-b border-line bg-paper px-3">
            <div className="flex items-center gap-0.5 rounded-control bg-surface-2 p-0.5">
              <SideTab active={view === "design"} onClick={() => setView("design")}>版面设计</SideTab>
              <SideTab active={view === "preview"} onClick={() => setView("preview")}>数据预览</SideTab>
            </div>
            <div className="flex items-center gap-2">
              <span className="font-mono text-[10px] text-faint">{composition.blocks.length}/{BLOCK_LIMIT} 项内容</span>
              {/* 1280–1536px：属性面板收进抽屉，手动开关 */}
              <button
                type="button"
                onClick={() => setInspectorOpen((open) => !open)}
                className="hidden h-6 cursor-pointer items-center gap-1 rounded-tag border border-line bg-surface px-2 text-[11px] text-muted transition-colors hover:border-line-strong hover:text-ink xl:flex 2xl:hidden"
                aria-expanded={inspectorOpen}
              >
                <Icon name="panel" size={12} />
                {selectedBlock ? "内容属性" : "报告概览"}
              </button>
            </div>
          </div>
          <div ref={canvasRef} className="min-h-0 flex-1 overflow-auto bg-surface-2/40 p-4">
            {view === "design" ? (
              <DesignCanvas
                composition={composition}
                metricById={metricById}
                selectedBlockId={selectedBlockId}
                starters={catalog.starters}
                dragIndex={dragIndex}
                overIndex={overIndex}
                onSelect={selectBlock}
                onDragStart={setDragIndex}
                onDragOver={setOverIndex}
                onDrop={(index) => {
                  if (dragIndex != null && dragIndex !== index) { patch(moveBlock(composition, dragIndex, index)); }
                  setDragIndex(null);
                  setOverIndex(null);
                }}
                onMove={(index, to) => patch(moveBlock(composition, index, to))}
                onRemove={(block) => {
                  patch(removeBlock(composition, block.id));
                  if (selectedBlockId === block.id) setSelectedBlockId("");
                }}
                onToggleWidth={(block) => {
                  const width = block.width === "full" ? "half" : "full";
                  patch(updateBlock(composition, block.id, { width }));
                  selectBlock(block.id);
                }}
                onApplyStarter={applyStarter}
              />
            ) : (
              <PreviewCanvas preview={preview} composition={composition} sourceName={activeSource?.name} onRun={runPreview} busy={busy === "preview"} />
            )}
          </div>
        </section>

        {/* ≥1536px 固定第三栏；1280–1536px 为覆盖式抽屉；<1280px 走面板切换标签 */}
        <aside
          className={cn(
            "min-h-0 shrink-0 flex-col border-line bg-paper",
            mobilePanel === "properties" ? "flex w-full" : "hidden",
            "xl:absolute xl:inset-y-0 xl:right-0 xl:z-20 xl:w-[300px] xl:border-l xl:shadow-card-hover",
            inspectorOpen ? "xl:flex" : "xl:hidden",
            "2xl:static 2xl:z-auto 2xl:flex 2xl:w-[280px] 2xl:shadow-none",
          )}
        >
          <div className="flex h-9 shrink-0 items-center justify-between gap-2 border-b border-line px-3">
            <h2 className="text-xs font-semibold text-ink">{selectedBlock ? "内容属性" : "报告概览"}</h2>
            <div className="flex items-center gap-2">
              {selectedBlock && <button type="button" onClick={() => setSelectedBlockId("")} className="text-[11px] font-semibold text-primary hover:underline">返回概览</button>}
              <button
                type="button"
                aria-label="关闭属性面板"
                onClick={() => setInspectorOpen(false)}
                className="hidden h-5 w-5 cursor-pointer place-items-center rounded-tag text-muted transition-colors hover:bg-surface-2 hover:text-ink xl:grid 2xl:hidden"
              >
                <Icon name="close" size={12} />
              </button>
            </div>
          </div>
          <div className="min-h-0 flex-1 overflow-auto">
            {selectedBlock ? (
              <BlockSettings
                block={selectedBlock}
                metric={metricById[selectedBlock.metricId]}
                onChange={(changes) => patch(updateBlock(composition, selectedBlock.id, changes))}
                onRemove={() => { patch(removeBlock(composition, selectedBlock.id)); setSelectedBlockId(""); }}
              />
            ) : (
              <ReportOverview
                composition={composition}
                metricById={metricById}
                topicCounts={topicCounts}
                sourceName={activeSource?.name}
                range={range}
                usedCount={usedMetrics.size}
                onDescription={(description) => patch({ ...composition, description })}
                onOpenRelationships={() => setRelationshipOpen(true)}
              />
            )}
          </div>
        </aside>
      </div>

      <Modal open={Boolean(pendingAction) || blocker.state === "blocked"} onClose={() => { setPendingAction(null); if (blocker.state === "blocked") blocker.reset(); }} label="确认未保存的修改">
        <div className="p-5">
          <h2 className="text-sm font-semibold text-ink">{pendingAction?.title ?? "离开周报工作台？"}</h2>
          <p className="mt-2 text-xs leading-relaxed text-muted">{pendingAction ? "确认后执行；继续编辑则不改变当前内容。" : "未保存的内容会保留在当前浏览器的个人草稿中，不等同于已保存模板。"}</p>
          <div className="mt-5 flex justify-end gap-2">
            <Button onClick={() => { setPendingAction(null); if (blocker.state === "blocked") blocker.reset(); }}>继续编辑</Button>
            <Button variant="primary" onClick={() => { if (pendingAction) { const action = pendingAction.action; setPendingAction(null); action(); } else if (blocker.state === "blocked") blocker.proceed(); }}>{pendingAction ? "确认操作" : "保留草稿并离开"}</Button>
          </div>
        </div>
      </Modal>
      <footer className="hidden h-7 shrink-0 items-center gap-3 border-t border-line bg-paper px-3 font-mono text-[10px] text-faint xl:flex">
        <span>{composition.blocks.length} 项内容 · {usedMetrics.size} 个指标</span>
        <span className="h-3 w-px bg-line" />
        <span>数据源 {activeSource?.name ?? "未选择"}</span>
        <span className="h-3 w-px bg-line" />
        <span>周期 {range.start} ~ {range.end}</span>
        <span className="ml-auto">{dirty ? "有未保存更改" : saved ? `模板 V${saved.version}` : "未保存草稿"}</span>
      </footer>

      <Modal open={relationshipOpen} onClose={() => setRelationshipOpen(false)} maxWidth={1180} label="业务数据关系图">
        <div className="flex items-start justify-between border-b border-line px-5 py-4">
          <div><p className="section-kicker">DATA RELATIONSHIP</p><h2 className="mt-1 text-lg font-semibold text-ink">从业务关系中选择数据</h2><p className="mt-1 text-xs text-muted">关系用于理解数据来源；只有已发布、已验证的指标可以加入周报。</p></div>
          <button type="button" onClick={() => setRelationshipOpen(false)} className="grid h-8 w-8 place-items-center rounded-control text-muted hover:bg-surface-2" aria-label="关闭关系图"><Icon name="close" size={15} /></button>
        </div>
        <RelationshipBrowser catalog={catalog} onAdd={add} onClose={() => setRelationshipOpen(false)} />
      </Modal>
    </div>
  );
}

function SaveStatePill({ dirty, saved }) {
  if (dirty) return <StatusPill tone="amber">未保存更改</StatusPill>;
  if (saved) return <StatusPill tone="green">模板 V{saved.version}</StatusPill>;
  return <StatusPill tone="gray">草稿</StatusPill>;
}

function SideTab({ active, onClick, children }) {
  return <button type="button" aria-pressed={active} onClick={onClick} className={cn("min-h-7 rounded-control px-2.5 text-xs font-medium transition-colors", active ? "bg-paper text-ink shadow-[0_1px_3px_rgb(24_35_41_/10%)]" : "text-muted hover:text-ink")}>{children}</button>;
}

const WEEKDAYS = ["一", "二", "三", "四", "五", "六", "日"];

function PeriodButton({ range, suggested, open, onOpen, onApply }) {
  const presets = presetRanges(suggested);
  const [cursor, setCursor] = useState(() => {
    const anchor = new Date(`${(range.start || localDate(new Date())).slice(0, 7)}-01T00:00:00`);
    return { year: anchor.getFullYear(), month: anchor.getMonth() };
  });
  const [picking, setPicking] = useState(null);

  const shiftMonth = (delta) => setCursor((current) => {
    const next = new Date(current.year, current.month + delta, 1);
    return { year: next.getFullYear(), month: next.getMonth() };
  });

  const pickDay = (iso) => {
    if (!picking) {
      setPicking(iso);
      return;
    }
    const start = picking <= iso ? picking : iso;
    const end = picking <= iso ? iso : picking;
    setPicking(null);
    onApply({ start, end });
  };

  const cells = monthCells(cursor.year, cursor.month);
  const monthLabel = `${cursor.year} 年 ${cursor.month + 1} 月`;
  const activePreset = presets.find((preset) => preset.range.start === range.start && preset.range.end === range.end);

  return (
    <div className="relative">
      <button type="button" aria-expanded={open} aria-label="统计周期" onClick={() => { setPicking(null); onOpen(!open); }} className={cn("inline-flex h-7 items-center gap-1.5 rounded-control border bg-surface px-2 text-xs font-medium text-ink hover:border-line-strong", open ? "border-primary" : "border-line")}>
        <Icon name="clock" size={12} className="text-faint" />
        <span className="font-mono">{range.start.slice(5)} ~ {range.end.slice(5)}</span>
        <Icon name="chevron" size={11} className={cn("text-faint transition-transform", open && "rotate-90")} />
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-30" onClick={() => onOpen(false)} aria-hidden="true" />
          <div className="absolute right-0 top-9 z-40 w-[300px] rounded-card border border-line bg-paper p-3 shadow-card-hover">
            <div className="flex flex-wrap gap-1.5 border-b border-line pb-2.5">
              {presets.map((preset) => (
                <button key={preset.id} type="button" onClick={() => { setPicking(null); onApply(preset.range); }} className={cn("min-h-7 rounded-control border px-2 text-[11px] font-medium", activePreset?.id === preset.id ? "border-primary bg-primary-soft text-primary" : "border-line text-ink hover:border-line-strong")}>
                  {preset.label}
                </button>
              ))}
            </div>
            <div className="mt-2.5 flex items-center justify-between">
              <button type="button" onClick={() => shiftMonth(-1)} className="grid h-7 w-7 place-items-center rounded-control text-muted hover:bg-surface-2 hover:text-ink" aria-label="上一月"><Icon name="chevron" size={13} className="rotate-180" /></button>
              <span className="text-xs font-semibold text-ink">{monthLabel}</span>
              <button type="button" onClick={() => shiftMonth(1)} className="grid h-7 w-7 place-items-center rounded-control text-muted hover:bg-surface-2 hover:text-ink" aria-label="下一月"><Icon name="chevron" size={13} /></button>
            </div>
            <div className="mt-1.5 grid grid-cols-7 gap-0.5 text-center">
              {WEEKDAYS.map((day) => <span key={day} className="py-1 text-[10px] font-semibold text-faint">{day}</span>)}
              {cells.map((cell) => {
                const inRange = range.start && range.end && cell.iso >= range.start && cell.iso <= range.end;
                const isEdge = cell.iso === range.start || cell.iso === range.end;
                const isPickStart = picking && cell.iso === picking;
                const isToday = cell.iso === localDate(new Date());
                return (
                  <button
                    key={cell.iso}
                    type="button"
                    disabled={!cell.inMonth}
                    onClick={() => pickDay(cell.iso)}
                    aria-label={cell.iso}
                    aria-pressed={isEdge}
                    className={cn(
                      "relative grid h-8 place-items-center rounded-control font-mono text-[11px] transition-colors",
                      !cell.inMonth && "cursor-default text-transparent",
                      cell.inMonth && !inRange && !isPickStart && "text-ink hover:bg-surface-2",
                      inRange && !isEdge && "bg-primary-soft text-primary",
                      isPickStart && "bg-primary-soft text-primary",
                      isEdge && "bg-primary text-white",
                      isToday && !isEdge && "ring-1 ring-inset ring-line-strong",
                    )}
                  >
                    {Number(cell.iso.slice(8))}
                  </button>
                );
              })}
            </div>
            <p className="mt-2 text-center text-[10px] leading-4 text-faint" role="status">
              {picking
                ? `已选开始 ${picking}，请点击结束日期`
                : `当前周期 ${rangeDays(range)} 天${suggested?.start ? ` · 数据覆盖 ${suggested.start} 至 ${suggested.end}` : " · 点选起止两天"}`}
            </p>
          </div>
        </>
      )}
    </div>
  );
}

function MetricLibrary({ metrics, query, onQuery, usedMetrics, metricById, onAdd, onJump, onOpenRelationships }) {
  const [openMetric, setOpenMetric] = useState("");
  const [collapsedTopics, setCollapsedTopics] = useState(() => new Set());
  const visible = filterMetrics(metrics, query);
  const groups = groupMetricsByTopic(visible);
  const usedRows = [...usedMetrics.entries()].filter(([id]) => metricById[id]);
  const toggleTopic = (topic) => setCollapsedTopics((current) => {
    const next = new Set(current);
    if (next.has(topic)) next.delete(topic); else next.add(topic);
    return next;
  });

  return (
    <>
      <div className="shrink-0 border-b border-line p-2">
        <label className="relative flex items-center">
          <Icon name="search" size={13} className="pointer-events-none absolute left-2.5 text-faint" />
          <input className="input-control h-8 pl-8 pr-2 text-xs" placeholder="搜索指标、口径、维度…" value={query} onChange={(event) => onQuery(event.target.value)} aria-label="搜索指标" />
        </label>
      </div>
      <div className="min-h-0 flex-1 overflow-auto">
        {usedRows.length > 0 && (
          <section className="border-b border-line bg-surface-2/50">
            <p className="px-3 pb-1 pt-2 text-[10px] font-semibold text-muted">已加入 · {usedRows.reduce((sum, [, count]) => sum + count, 0)} 项</p>
            {usedRows.map(([id, count]) => (
              <button key={id} type="button" onClick={() => onJump(id)} className="flex w-full items-center gap-2 px-3 py-1.5 text-left hover:bg-surface-3">
                <span className="h-1.5 w-1.5 shrink-0 rounded-full" style={{ background: TOPIC_COLORS[topicOf(metricById[id])] }} />
                <span className="min-w-0 flex-1 truncate text-[13px] font-semibold text-ink">{metricById[id].name}</span>
                <span className="shrink-0 font-mono text-[10px] text-faint">×{count}</span>
              </button>
            ))}
          </section>
        )}
        {groups.length ? groups.map((group) => {
          const collapsed = collapsedTopics.has(group.topic) && !query;
          return (
            <section key={group.topic} className="border-b border-line/70">
              <button type="button" onClick={() => toggleTopic(group.topic)} aria-expanded={!collapsed} className="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-surface-2">
                <Icon name="chevron" size={11} className={cn("shrink-0 text-faint transition-transform", !collapsed && "rotate-90")} />
                <span className="h-2 w-2 shrink-0 rounded-[3px]" style={{ background: group.color }} />
                <span className="min-w-0 flex-1 truncate text-xs font-semibold text-ink">{group.topic}</span>
                <span className="shrink-0 font-mono text-[10px] text-faint">{group.metrics.length}</span>
              </button>
              {!collapsed && group.metrics.map((metric) => (
                <MetricRow key={metric.id} metric={metric} open={openMetric === metric.id || Boolean(query)} usage={usedMetrics.get(metric.id) ?? 0} onToggle={() => setOpenMetric(openMetric === metric.id ? "" : metric.id)} onAdd={onAdd} />
              ))}
            </section>
          );
        }) : (
          <p className="px-4 py-6 text-center text-xs leading-5 text-muted">没有匹配“{query}”的已发布指标。可换个关键词，或到指标管理中确认口径是否已发布。</p>
        )}
      </div>
      <div className="flex h-9 shrink-0 items-center justify-between gap-2 border-t border-line px-3">
        <button type="button" onClick={onOpenRelationships} className="inline-flex items-center gap-1 text-[11px] font-semibold text-primary hover:underline"><Icon name="model" size={12} />数据关系图</button>
        <span className="font-mono text-[10px] text-faint">{metrics.length} 个可用指标</span>
      </div>
    </>
  );
}

function MetricRow({ metric, open, usage, onToggle, onAdd }) {
  const dimensions = metric.dimensions ?? [];
  return (
    <div className="border-t border-line/50 first:border-t-0">
      <div className="flex items-center gap-1.5 px-3 py-1.5">
        <button type="button" onClick={onToggle} aria-expanded={open} className="min-w-0 flex-1 text-left">
          <span className="flex items-center gap-1.5">
            <strong className="truncate text-[13px] font-semibold leading-5 text-ink">{metric.name}</strong>
            {metric.unit && <span className="shrink-0 rounded-tag bg-surface-2 px-1 text-[10px] text-muted">{metric.unit}</span>}
            {usage > 0 && <span className="shrink-0 rounded-tag bg-primary-soft px-1 font-mono text-[10px] text-primary">×{usage}</span>}
          </span>
          <span className="mt-0.5 block truncate text-[11px] leading-4 text-muted/85">{metric.definition || "企业已发布口径"}</span>
        </button>
        <button type="button" onClick={onToggle} aria-label={`添加${metric.name}到周报`} className={cn("grid h-6 w-6 shrink-0 place-items-center rounded-control border border-line text-muted hover:border-primary hover:text-primary", open && "border-primary bg-primary text-paper")}>
          <Icon name="plus" size={12} />
        </button>
      </div>
      {open && (
        <div className="flex flex-wrap gap-1 px-3 pb-2">
          {metric.kinds.includes("kpi") && <AddChip onClick={() => onAdd(metric, "kpi")}>指标概览</AddChip>}
          {metric.kinds.includes("trend") && <AddChip onClick={() => onAdd(metric, "trend")}>趋势</AddChip>}
          {metric.kinds.includes("rank") && dimensions.map((dimension) => <AddChip key={dimension} onClick={() => onAdd(metric, "breakdown", dimension)}>按{dimension}</AddChip>)}
          {!metric.kinds.length && <span className="text-[11px] leading-5 text-faint">该指标暂无可用展示类型，请先在指标管理中完善物理绑定。</span>}
        </div>
      )}
    </div>
  );
}

function AddChip({ onClick, children }) {
  return <button type="button" onClick={onClick} className="min-h-6 rounded-tag border border-line px-1.5 text-[10px] font-medium text-ink hover:border-primary hover:bg-primary-soft hover:text-primary">+ {children}</button>;
}

function TemplateLibrary({ starters, savedTemplates, saved, onStarter, onSaved, onDelete, deleting }) {
  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-auto">
      <p className="px-3 pb-1 pt-2.5 text-[11px] font-semibold text-muted">起始模板</p>
      <p className="px-3 pb-2 text-[11px] leading-4 text-faint">只负责快速起步，载入后仍可自由混搭内容。</p>
      <div className="divide-y divide-line/70 border-y border-line">
        {starters.map((starter) => (
          <button key={starter.id} type="button" onClick={() => onStarter(starter)} disabled={!starter.blocks.length && starter.id !== "starter-blank"} className="group w-full px-3 py-2.5 text-left hover:bg-surface-2 disabled:cursor-not-allowed disabled:opacity-40">
            <span className="flex items-center justify-between gap-2">
              <strong className="text-xs font-semibold text-ink">{starter.name.replace("起始模板", "周报")}</strong>
              <Icon name="arrow" size={12} className="text-faint group-hover:text-primary" />
            </span>
            <span className="mt-0.5 block text-[11px] leading-4 text-muted">{starter.description}</span>
            {starter.blocks.length > 0 && <span className="mt-1 block font-mono text-[10px] text-faint">{starter.blocks.length} 项内容</span>}
          </button>
        ))}
      </div>
      <p className="px-3 pb-1 pt-3 text-[11px] font-semibold text-muted">我的模板</p>
      {savedTemplates.length ? (
        <div className="divide-y divide-line/70 border-y border-line">
          {savedTemplates.map((template) => (
            <button key={template.id} type="button" onClick={() => onSaved(template)} className={cn("w-full px-3 py-2.5 text-left hover:bg-surface-2", saved?.id === template.id && "bg-primary-soft/50")}>
              <strong className="block truncate text-xs text-ink">{template.composition.name}</strong>
              <span className="mt-0.5 block font-mono text-[10px] text-muted">{template.composition.blocks?.length ?? 0} 项内容 · V{template.version}</span>
            </button>
          ))}
        </div>
      ) : <p className="px-3 py-3 text-[11px] leading-5 text-muted">还没有个人模板。配置好版面后，用顶部“存为模板”保存。</p>}
      {saved && <button type="button" onClick={onDelete} disabled={deleting} className="mx-3 my-2 text-[11px] font-semibold text-red hover:underline">{deleting ? "删除中…" : "删除当前个人模板"}</button>}
    </div>
  );
}

function DesignCanvas({ composition, metricById, selectedBlockId, starters, dragIndex, overIndex, onSelect, onDragStart, onDragOver, onDrop, onMove, onRemove, onToggleWidth, onApplyStarter }) {
  if (!composition.blocks.length) {
    return (
      <div className="mx-auto flex h-full min-h-[360px] max-w-3xl flex-col items-center justify-center rounded-section border border-dashed border-line-strong bg-paper px-6 text-center">
        <span className="grid h-10 w-10 place-items-center rounded-control bg-primary-soft text-primary"><Icon name="report" size={18} /></span>
        <p className="mt-3 text-sm font-semibold text-ink">从左侧指标库加入第一项内容</p>
        <p className="mt-1 max-w-sm text-xs leading-5 text-muted">生产、质量、设备、库存指标可自由混搭，版面不会改变企业口径。</p>
        <div className="mt-4 flex flex-wrap justify-center gap-1.5">
          {starters.filter((starter) => starter.blocks.length).map((starter) => (
            <button key={starter.id} type="button" onClick={() => onApplyStarter(starter)} className="min-h-7 rounded-control border border-line px-2.5 text-[11px] font-medium text-ink hover:border-primary hover:text-primary">{starter.name.replace("起始模板", "起步")}</button>
          ))}
        </div>
      </div>
    );
  }
  return (
    <div className="mx-auto max-w-[860px]">
      <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
        {composition.blocks.map((block, index) => (
          <CanvasCard
            key={block.id}
            block={block}
            metric={metricById[block.metricId]}
            index={index}
            count={composition.blocks.length}
            active={block.id === selectedBlockId}
            dragging={dragIndex === index}
            dropTarget={dragIndex != null && overIndex === index && dragIndex !== index}
            onSelect={() => onSelect(block.id)}
            onMove={(to) => onMove(index, to)}
            onRemove={() => onRemove(block)}
            onToggleWidth={() => onToggleWidth(block)}
            onDragStart={() => onDragStart(index)}
            onDragOver={() => onDragOver(index)}
            onDrop={() => onDrop(index)}
          />
        ))}
      </div>
      <p className="mt-3 px-1 text-[11px] text-faint">卡片尺寸即报告版面：半栏并排、通栏整行；拖动卡片调整阅读顺序，悬停卡片可切换宽度或删除。</p>
    </div>
  );
}

function BlockGlyph({ kind, chart, color }) {
  const stroke = { stroke: color, fill: "none", strokeWidth: 1.6, strokeLinecap: "round" };
  if (kind === "kpi") {
    return (
      <span className="flex items-baseline gap-1.5">
        <span className="font-mono text-[26px] font-semibold leading-none tracking-tight" style={{ color }}>—</span>
        <span className="h-2.5 w-8 rounded-full opacity-30" style={{ background: color }} />
      </span>
    );
  }
  if (kind === "trend") {
    return (
      <svg viewBox="0 0 72 26" width="72" height="26" aria-hidden="true">
        <path d="M2 22 L14 14 L26 18 L38 7 L50 12 L62 3 L70 6" {...stroke} opacity="0.75" />
        {chart === "area" && <path d="M2 22 L14 14 L26 18 L38 7 L50 12 L62 3 L70 6 L70 25 L2 25 Z" fill={color} opacity="0.12" stroke="none" />}
      </svg>
    );
  }
  if (chart === "calendar") {
    return (
      <svg viewBox="0 0 72 26" width="72" height="26" aria-hidden="true">
        {Array.from({ length: 12 }, (_, cellIndex) => {
          const cx = 4 + (cellIndex % 6) * 11;
          const cy = 3 + Math.floor(cellIndex / 6) * 11;
          const shade = [0.2, 0.6, 0.35, 0.85, 0.15, 0.5][cellIndex % 6];
          return <rect key={cellIndex} x={cx} y={cy} width="9" height="9" rx="1.5" fill={color} opacity={shade} />;
        })}
      </svg>
    );
  }
  if (chart === "table") {
    return (
      <svg viewBox="0 0 72 26" width="72" height="26" aria-hidden="true">
        {[3, 11, 19].map((rowY) => <line key={rowY} x1="3" y1={rowY} x2="69" y2={rowY} stroke={color} strokeWidth="0.8" opacity="0.28" />)}
        {[16, 11, 6].map((barW, rowIndex) => <rect key={rowIndex} x="6" y={rowIndex * 8 + 4} width={barW} height="3.5" rx="1" fill={color} opacity={0.75 - rowIndex * 0.18} />)}
      </svg>
    );
  }
  if (chart === "donut") {
    return (
      <svg viewBox="0 0 26 26" width="26" height="26" aria-hidden="true">
        <circle cx="13" cy="13" r="9" {...stroke} opacity="0.25" />
        <path d="M13 4 a9 9 0 0 1 8.2 12.6" stroke={color} strokeWidth="3" fill="none" strokeLinecap="round" opacity="0.8" />
      </svg>
    );
  }
  const bars = chart === "pareto" ? [16, 12, 9, 6, 4, 3] : [10, 16, 7, 13, 5];
  return (
    <svg viewBox="0 0 72 26" width="72" height="26" aria-hidden="true">
      {bars.map((height, barIndex) => (
        <rect key={barIndex} x={4 + barIndex * 12} y={24 - height} width="8" height={height} rx="1.5" fill={color} opacity={0.2 + barIndex * 0.1} />
      ))}
      {chart === "pareto" && <path d="M8 18 L20 13 L32 9 L44 6 L56 4 L68 3" {...stroke} opacity="0.7" />}
    </svg>
  );
}

function CanvasCard({ block, metric, index, count, active, dragging, dropTarget, onSelect, onMove, onRemove, onToggleWidth, onDragStart, onDragOver, onDrop }) {
  const topic = topicOf(metric);
  const color = TOPIC_COLORS[topic];
  const chartLabel = CHART_OPTIONS[block.kind]?.find(([value]) => value === block.chart)?.[1];
  const meta = [
    metric?.name ?? block.metricId,
    block.kind === "breakdown" && block.dimension ? `按${block.dimension}` : null,
    block.kind === "trend" ? GRAIN_LABELS[block.grain] : null,
    chartLabel,
  ].filter(Boolean).join(" · ");
  const minHeight = { compact: "min-h-[104px]", standard: "min-h-[136px]", focus: "min-h-[176px]" }[block.height] ?? "min-h-[136px]";
  return (
    <article
      data-block={block.id}
      role="button"
      tabIndex={0}
      aria-label={`编辑${block.title}`}
      draggable
      onClick={onSelect}
      onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") onSelect(); }}
      onDragStart={onDragStart}
      onDragOver={(event) => { event.preventDefault(); onDragOver(); }}
      onDrop={onDrop}
      className={cn(
        "group relative flex cursor-pointer flex-col overflow-hidden rounded-card border bg-paper text-left transition-[border-color,box-shadow,opacity,transform]",
        minHeight,
        block.width === "full" && "sm:col-span-2",
        active ? "border-primary shadow-[0_0_0_2px_rgb(40_93_114_/14%)]" : "border-line hover:border-line-strong hover:shadow-card-hover",
        dragging && "opacity-40",
        dropTarget && "border-primary ring-2 ring-primary/25",
      )}
    >
      <span className="absolute inset-x-0 top-0 h-[3px]" style={{ background: color }} />
      <div className="flex items-start gap-1.5 px-3 pb-1 pt-2.5">
        <Icon name="menu" size={13} className="mt-0.5 shrink-0 cursor-grab text-faint" />
        <span className="mt-px shrink-0 font-mono text-[10px] text-faint">{index + 1}</span>
        <span className="min-w-0 flex-1 truncate text-xs font-semibold text-ink">{block.title}</span>
        <span className="flex shrink-0 items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
          <button type="button" onClick={(event) => { event.stopPropagation(); onToggleWidth(); }} className="grid h-6 w-6 place-items-center rounded-control text-muted hover:bg-surface-2" aria-label={block.width === "full" ? "改为半栏" : "改为通栏"} title={block.width === "full" ? "改为半栏" : "改为通栏"}><Icon name="expand" size={12} /></button>
          <button type="button" onClick={(event) => { event.stopPropagation(); onMove(index - 1); }} disabled={index === 0} className="grid h-6 w-6 place-items-center rounded-control text-muted hover:bg-surface-2 disabled:opacity-25" aria-label="前移内容"><Icon name="chevron" size={12} className="-rotate-90" /></button>
          <button type="button" onClick={(event) => { event.stopPropagation(); onMove(index + 1); }} disabled={index === count - 1} className="grid h-6 w-6 place-items-center rounded-control text-muted hover:bg-surface-2 disabled:opacity-25" aria-label="后移内容"><Icon name="chevron" size={12} className="rotate-90" /></button>
          <button type="button" onClick={(event) => { event.stopPropagation(); onRemove(); }} className="grid h-6 w-6 place-items-center rounded-control text-muted hover:bg-red-soft hover:text-red" aria-label="删除内容"><Icon name="trash" size={12} /></button>
        </span>
      </div>
      <div className="flex flex-1 items-center justify-center px-3 py-1">
        <BlockGlyph kind={block.kind} chart={block.chart} color={color} />
      </div>
      <div className="flex items-center justify-between gap-2 border-t border-line/70 px-3 py-1.5">
        <span className="min-w-0 truncate text-[10px] text-muted">{meta}</span>
        <span className="flex shrink-0 items-center gap-1 text-[10px] text-faint">
          <span className="rounded-tag px-1" style={{ background: `${color}14`, color }}>{topic}</span>
          {block.width === "full" ? "通栏" : "半栏"} · {HEIGHT_LABELS[block.height]}
        </span>
      </div>
    </article>
  );
}

function InspectorField({ label, children }) {
  return <label className="grid gap-1 text-[10px] font-semibold text-muted"><span>{label}</span>{children}</label>;
}

function BlockSettings({ block, metric, onChange, onRemove }) {
  if (!block) return null;
  return (
    <div className="grid content-start gap-3.5 p-3">
      <div className="rounded-card border border-line bg-surface-2/60 p-2.5">
        <p className="text-[10px] font-semibold text-primary">{KIND_LABELS[block.kind]}</p>
        <p className="mt-1 truncate text-xs font-semibold text-ink">{metric?.name ?? block.metricId}</p>
        <p className="mt-0.5 font-mono text-[10px] text-faint">口径 V{metric?.version} · 绑定 V{metric?.bindingVersion} · {metric?.unit || "企业口径"}</p>
      </div>
      <InspectorField label="显示标题"><input className="input-control h-8 text-xs" value={block.title} maxLength={100} onChange={(event) => onChange({ title: event.target.value })} /></InspectorField>
      {block.kind === "trend" && <InspectorField label="时间粒度"><Segmented value={block.grain} options={block.chart === "calendar" ? [["day", "日"]] : [["day", "日"], ["week", "周"], ["month", "月"]]} onChange={(grain) => onChange({ grain })} /></InspectorField>}
      {block.kind === "breakdown" && <InspectorField label="分析维度"><select className="input-control h-8 text-xs" value={block.dimension ?? ""} onChange={(event) => onChange({ dimension: event.target.value, title: `按${event.target.value}分析${metric?.name ?? ""}` })}>{metric?.dimensions?.map((dimension) => <option key={dimension}>{dimension}</option>)}</select></InspectorField>}
      {block.kind === "breakdown" && <InspectorField label="展示数量"><input type="number" min="2" max="40" className="input-control h-8 text-xs" value={block.topN} onChange={(event) => onChange({ topN: Math.max(2, Math.min(40, Number(event.target.value) || 12)) })} /></InspectorField>}
      <InspectorField label="展示方式"><Segmented value={block.chart} options={chartOptionsFor(metric, block.kind)} onChange={(chart) => onChange({ chart, ...(chart === "donut" && block.topN > 12 ? { topN: 12 } : {}), ...(chart === "calendar" ? { grain: "day", compare: false } : {}) })} /></InspectorField>
      {block.kind === "breakdown" && !metric?.semantics?.compositionAllowed && <p className="text-[11px] leading-relaxed text-muted">此指标不可证明可加总，保留数值比较与表格，不提供占比和累计贡献图。</p>}
      <InspectorField label="内容宽度"><Segmented value={block.width} options={[["half", "半栏"], ["full", "通栏"]]} onChange={(width) => onChange({ width })} /></InspectorField>
      <InspectorField label="内容高度"><Segmented value={block.height} options={[["compact", "紧凑"], ["standard", "标准"], ["focus", "重点"]]} onChange={(height) => onChange({ height })} /></InspectorField>
      {(block.kind === "kpi" || (block.kind === "trend" && block.chart !== "calendar")) && <label className="flex min-h-9 cursor-pointer items-center justify-between gap-3 border-y border-line py-2 text-xs text-ink"><span><strong className="block">与上一周期比较</strong><span className="mt-0.5 block text-[11px] text-muted">{block.kind === "kpi" ? "使用紧邻的等长周期" : "上期按日历粒度相对位置对应；缺失不补零"}</span></span><input type="checkbox" checked={block.compare} onChange={(event) => onChange({ compare: event.target.checked })} /></label>}
      <InspectorField label="强调色"><div className="flex flex-wrap gap-1.5">{ACCENTS.map(([key, label, color]) => <button key={key} type="button" onClick={() => onChange({ accent: key })} className={cn("flex h-7 items-center gap-1.5 rounded-control px-2 text-[10px]", block.accent === key ? "bg-surface-3 text-ink" : "text-muted hover:bg-surface-2")}><span className="h-2.5 w-2.5 rounded-full" style={{ background: color }} />{label}</button>)}</div></InspectorField>
      <button type="button" onClick={onRemove} className="mt-1 inline-flex min-h-8 items-center justify-center gap-1.5 rounded-control border border-line text-[11px] font-semibold text-red hover:border-red hover:bg-red-soft"><Icon name="trash" size={12} />删除该内容</button>
    </div>
  );
}

function Segmented({ value, options, onChange }) {
  return <div className="flex flex-wrap gap-1 rounded-control bg-surface-2 p-1">{options.map(([key, label]) => <button key={key} type="button" onClick={() => onChange(key)} className={cn("min-h-6 rounded-[6px] px-2 text-[10px] font-medium", value === key ? "bg-paper text-ink shadow-[0_1px_3px_rgb(24_35_41_/10%)]" : "text-muted hover:text-ink")}>{label}</button>)}</div>;
}

function ReportOverview({ composition, metricById, topicCounts, sourceName, range, usedCount, onDescription, onOpenRelationships }) {
  const checklist = structureChecklist(composition, metricById);
  const doneCount = checklist.filter((item) => item.done).length;
  return (
    <div className="grid content-start gap-4 p-3">
      <section>
        <p className="text-[11px] font-semibold text-muted">主题覆盖</p>
        <div className="mt-1.5 flex flex-wrap gap-1.5">
          {TOPIC_ORDER.filter((topic) => topic !== "其他" || topicCounts["其他"]).map((topic) => (
            <span key={topic} className={cn("inline-flex items-center gap-1.5 rounded-tag border px-1.5 py-0.5 text-[10px]", topicCounts[topic] ? "border-line bg-surface-2 text-ink" : "border-dashed border-line text-faint")}>
              <span className="h-1.5 w-1.5 rounded-full" style={{ background: topicCounts[topic] ? TOPIC_COLORS[topic] : "var(--color-line-strong)" }} />
              {topic} {topicCounts[topic] || 0}
            </span>
          ))}
        </div>
      </section>
      <section>
        <div className="flex items-center justify-between">
          <p className="text-[11px] font-semibold text-muted">结构检查</p>
          <span className="font-mono text-[10px] text-faint">{doneCount}/{checklist.length}</span>
        </div>
        <ul className="mt-1.5 divide-y divide-line/70 rounded-card border border-line bg-surface-2/40">
          {checklist.map((item) => (
            <li key={item.id} className="flex items-center gap-2 px-2.5 py-1.5 text-[11px]">
              <span className={cn("grid h-4 w-4 shrink-0 place-items-center rounded-full border", item.done ? "border-success bg-success text-white" : "border-line-strong text-transparent")}><Icon name="check" size={9} /></span>
              <span className={cn(item.done ? "text-ink" : "text-muted")}>{item.label}</span>
            </li>
          ))}
        </ul>
        <p className="mt-1 text-[11px] leading-4 text-faint">检查只是建议结构，不限制发布；报告以实际业务问题为准。</p>
      </section>
      <InspectorField label="报告说明（随模板保存）">
        <textarea className="input-control min-h-16 resize-y py-1.5 text-xs" maxLength={300} placeholder="这份周报给谁看、关注什么，写一句备忘…" value={composition.description} onChange={(event) => onDescription(event.target.value)} />
      </InspectorField>
      <section className="rounded-card border border-line bg-surface-2/40 p-2.5 text-[11px] leading-5 text-muted">
        <p className="flex justify-between gap-2"><span>数据源</span><strong className="min-w-0 truncate text-right font-medium text-ink">{sourceName ?? "未选择"}</strong></p>
        <p className="mt-1 flex justify-between gap-2"><span>统计周期</span><strong className="shrink-0 font-mono text-[10px] text-ink">{range.start} ~ {range.end}</strong></p>
        <p className="mt-1 flex justify-between gap-2"><span>内容 / 指标</span><strong className="shrink-0 font-mono text-[10px] text-ink">{composition.blocks.length} / {usedCount}</strong></p>
        <button type="button" onClick={onOpenRelationships} className="mt-2 inline-flex items-center gap-1 text-[11px] font-semibold text-primary hover:underline"><Icon name="model" size={12} />查看数据关系与字段</button>
      </section>
      <p className="text-[11px] leading-4 text-faint">点击中间版面的一项内容，可在此调整图表、粒度、尺寸和强调色。</p>
    </div>
  );
}

function RelationshipBrowser({ catalog, onAdd, onClose }) {
  const [selectedTable, setSelectedTable] = useState(() => catalog.tables?.find((table) => table.metricIds?.length)?.qualifiedName ?? catalog.tables?.[0]?.qualifiedName ?? "");
  const metricById = Object.fromEntries((catalog.metrics ?? []).map((metric) => [metric.id, metric]));
  const activeTable = catalog.tables?.find((table) => table.qualifiedName === selectedTable);
  const tableMetrics = (activeTable?.metricIds ?? []).map((id) => metricById[id]).filter(Boolean);
  return (
    <div className="grid max-h-[72vh] min-h-[560px] lg:grid-cols-[minmax(0,1fr)_330px]">
      <div className="min-w-0 overflow-auto border-b border-line lg:border-b-0 lg:border-r"><RelationshipMap tables={catalog.tables ?? []} relations={catalog.relations ?? []} selected={selectedTable} onSelect={setSelectedTable} /></div>
      <div className="flex flex-col">
        <div className="border-b border-line px-4 py-3">
          <span className="text-[11px] font-semibold text-primary">{activeTable?.topic || "业务对象"}</span>
          <h3 className="mt-1 truncate text-sm font-semibold text-ink">{activeTable?.cnName || activeTable?.name || "选择对象"}</h3>
          <p className="mt-1 truncate font-mono text-[10px] text-faint">{activeTable?.qualifiedName}</p>
        </div>
        <div className="min-h-0 flex-1 overflow-auto">
          {tableMetrics.length ? tableMetrics.map((metric) => (
            <MetricRow key={metric.id} metric={metric} open usage={0} onToggle={() => {}} onAdd={(...args) => { onAdd(...args); onClose(); }} />
          )) : <p className="px-4 py-4 text-xs leading-5 text-muted">这个对象还没有已发布且完成物理绑定的指标。它仍显示在关系中，便于理解数据来源，但不能直接加入周报。</p>}
          {activeTable?.fields?.length > 0 && (
            <details className="border-t border-line px-4 text-xs text-muted">
              <summary className="cursor-pointer py-2 font-medium text-primary">查看字段元数据（{activeTable.fields.length}）</summary>
              <div className="max-h-48 overflow-auto divide-y divide-line border-y border-line pb-2">
                {activeTable.fields.map((field) => <div key={field.name} className="flex items-center justify-between gap-3 py-1.5"><span className="truncate">{field.cnName || field.name}</span><code className="shrink-0 text-[10px] text-faint">{field.name}</code></div>)}
              </div>
            </details>
          )}
        </div>
      </div>
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

function PreviewCanvas({ preview, composition, sourceName, onRun, busy }) {
  if (!preview) {
    return (
      <div className="mx-auto flex h-full min-h-[360px] max-w-3xl flex-col items-center justify-center rounded-section border border-dashed border-line-strong bg-paper px-6 text-center">
        <span className="grid h-10 w-10 place-items-center rounded-control bg-primary-soft text-primary"><Icon name="chart" size={18} /></span>
        <p className="mt-3 text-sm font-semibold text-ink">查看当前版面的真实数据</p>
        <p className="mt-1 max-w-xs text-xs leading-5 text-muted">预览只执行受治理的只读查询，不会保存结果、冻结版本或启动 Agent。</p>
        <Button className="mx-auto mt-4" onClick={onRun} disabled={busy}>{busy ? "查询中…" : "开始预览"}</Button>
      </div>
    );
  }
  return (
    <div className="mx-auto w-full max-w-[860px]">
      <div className="overflow-hidden rounded-section border border-line bg-paper shadow-card">
        <header className="border-b border-line px-6 py-5">
          <p className="section-kicker">制造经营分析 · 周期报告（预览）</p>
          <h2 className="mt-1.5 text-xl font-semibold tracking-tight text-ink">{composition.name}</h2>
          <p className="mt-1.5 text-xs text-muted">数据源 {sourceName ?? "—"} · 统计周期 {preview.range.start} 至 {preview.range.end} · {composition.blocks.length} 项内容 · 预览结果不保存到个人模板</p>
        </header>
        <div className="grid gap-3 p-5 sm:grid-cols-2">
          {preview.items.map((item) => <PreviewBlock key={item.block.id} item={item} />)}
        </div>
        <footer className="border-t border-line bg-surface-2/50 px-6 py-3 text-[10px] leading-4 text-faint">
          每个数字来自已发布指标的只读查询；正式生成周报时会冻结指标版本、查询口径与证据快照，页面、PDF、Excel 使用同一版本。
        </footer>
      </div>
    </div>
  );
}

function PreviewBlock({ item }) {
  const data = previewSeries(item);
  const color = ACCENT_COLOR[item.block.accent] ?? ACCENT_COLOR.default;
  const height = { compact: 220, standard: 270, focus: 350 }[item.block.height] ?? 270;
  const fullClass = item.block.width === "full" ? "sm:col-span-2" : "";
  if (item.state !== "ready") {
    return <article className={cn("grid min-h-44 place-content-center rounded-card border border-dashed border-line bg-surface-2/30 p-5 text-center", fullClass)}><strong className="text-sm text-ink">{item.block.title}</strong><p className="mt-1 text-xs text-muted">{item.message}</p></article>;
  }
  if (item.block.kind === "kpi") {
    const current = data[0]?.value;
    const previous = item.previousRows?.[0]?.at(-1);
    const change = previous && Number(previous) !== 0 ? (current - Number(previous)) / Math.abs(Number(previous)) * 100 : null;
    return (
      <article className={cn("rounded-card border border-line p-4", fullClass)}>
        <p className="text-xs font-semibold text-muted">{item.block.title}</p>
        <p className="mt-2.5 font-mono text-[26px] font-semibold leading-none tracking-tight text-ink">{Number(current).toLocaleString("zh-CN", { maximumFractionDigits: 2 })}<span className="ml-1 text-xs font-normal text-muted">{item.metric.unit}</span></p>
        {change != null && <p className="mt-2 text-xs font-medium text-muted">较上一周期 {change >= 0 ? "+" : ""}{change.toFixed(1)}% · 变化不代表优劣</p>}
        <p className="mt-2 font-mono text-[10px] text-faint">{item.metric.name} · 口径 V{item.metric.version} · 绑定 V{item.metric.bindingVersion}</p>
      </article>
    );
  }
  let chart;
  if (item.block.kind === "breakdown" && item.block.chart === "table") {
    chart = <DataTableBars data={data} compositionAllowed={item.metric.semantics?.compositionAllowed} unit={item.metric.unit} valueName={item.metric.name} categoryName={item.block.dimension || "项目"} color={color} height={height} topN={item.block.topN} />;
  } else if (item.block.chart === "donut") chart = <ShareDonutChart data={data.map((point) => ({ name: point.label, value: point.value }))} unit={item.metric.unit} centerTitle="合计" centerValue={data.reduce((sum, point) => sum + point.value, 0).toLocaleString("zh-CN")} height={height} />;
  else if (item.block.chart === "pareto") {
    const total = data.reduce((sum, point) => sum + Math.max(point.value, 0), 0);
    let cumulative = 0;
    const pareto = data.map((point) => ({ ...point, secondaryValue: total ? (cumulative += Math.max(point.value, 0)) / total * 100 : 0 }));
    chart = <ComboMetricChart data={pareto} xKey="label" bars={[{ key: "value", name: item.metric.name, color }]} lines={[{ key: "secondaryValue", name: "累计贡献率", color: "var(--color-red)" }]} leftUnit={item.metric.unit} rightUnit="%" height={height} />;
  } else if (item.block.chart === "column") {
    const bars = [{ key: "value", name: item.metric.name, color }];
    if (data.some((point) => point.previousValue != null)) bars.push({ key: "previousValue", name: item.comparison?.label ?? "上一周期", color: "var(--color-faint)" });
    chart = <RankBarChart data={data} xKey="label" bars={bars} unit={item.metric.unit} height={height} />;
  }
  else if (item.block.kind === "breakdown") chart = <HorizontalRankChart data={data} color={color} unit={item.metric.unit} valueName={item.metric.name} height={height} />;
  else if (item.block.chart === "calendar") chart = <CalendarHeatmap data={data} unit={item.metric.unit} valueName={item.metric.name} color={color} />;
  else {
    const hasPrevious = item.block.compare && data.some((point) => point.previousValue != null);
    const series = [{ key: "value", name: item.metric.name, color, width: 2 }];
    if (hasPrevious) series.push({ key: "previousValue", name: item.comparison?.label ?? "上一周期", color: "var(--color-faint)", width: 1.5, dash: true });
    chart = <TrendLineChart data={data} xKey="label" series={series} unit={item.metric.unit} area={item.block.chart === "area"} height={height} />;
  }
  return (
    <article className={cn("min-w-0 rounded-card border border-line p-4", fullClass)}>
      <div className="mb-3 flex items-start justify-between gap-3">
        <div className="min-w-0"><h3 className="text-sm font-semibold text-ink">{item.block.title}</h3><p className="mt-1 font-mono text-[10px] text-muted">{item.metric.name} · {item.metric.unit || "企业口径"} · 查询结果 {item.rows.length} 行</p></div>
        {item.block.kind === "trend" && item.block.compare && item.previousRows?.length > 0 && (
          <span className="flex shrink-0 items-center gap-1 text-[11px] text-faint"><i className="inline-block h-0 w-4 border-t-2 border-dashed border-faint" />上一周期</span>
        )}
      </div>
      {chart}
      {item.block.compare && item.comparison?.state !== "ready" && <p className="mt-2 text-[11px] text-muted">上期对比数据不可用或无法对应，缺失值未补零。</p>}
    </article>
  );
}
