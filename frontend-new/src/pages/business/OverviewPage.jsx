import { useEffect, useMemo, useState } from "react";
import { Responsive, WidthProvider } from "react-grid-layout/legacy";
import "react-grid-layout/css/styles.css";
import { useOutletContext } from "react-router-dom";

import { listSources } from "../../api/datasources";
import { getDashboard, queryDashboardWidget, saveDashboard } from "../../api/dashboard";
import { getBusinessOverview } from "../../api/overview";
import { CategoryTreemapChart, HorizontalRankChart, RankBarChart, ShareDonutChart, TrendLineChart } from "../../components/charts";
import Icon from "../../components/ui/Icon";
import { Button, ErrorNotice, Modal, PageFrame, RouteSkeleton, StatusPill } from "../../components/ui";
import { addWidget, applyWidgetSizePreset, moveWidget, removeWidget, updateWidgetAppearance, updateWidgetLayouts } from "../../features/dashboard/dashboardModel";
import { currentUserId } from "../../lib/auth";
import cn from "../../lib/cn";

const Grid = WidthProvider(Responsive);
const ACTIVE_SOURCE_KEY = "forgesight:active-source-id";
const ANALYSIS_DRAFT_KEY = "forgesight:analysis-draft";
const ANALYSIS_SESSION_KEY = "forgesight:analysis-session-id";
const USER_ID = currentUserId;
const PERIODS = [
  { id: "last7", label: "近 7 天" },
  { id: "last30", label: "近 30 天" },
  { id: "all", label: "全部" },
];
const RUN_LABELS = {
  queued: "排队中", running: "分析中", waiting_user: "待继续", succeeded: "已完成",
  failed: "失败", blocked: "未执行", cancelled: "已取消", "未开始": "未开始",
};
const WORKSPACE_WIDGETS = [
  { kind: "context", title: "调查与提醒", description: "集中显示待处理、失败或需要继续的分析任务。", icon: "analysis" },
  { kind: "recent", title: "近期调查", description: "查看最近的分析记录，并从看板继续调查。", icon: "history" },
  { kind: "catalog", title: "可用指标", description: "浏览当前数据源已发布的企业指标。", icon: "knowledge" },
];
const ACCENT_OPTIONS = [
  { id: "default", label: "默认", color: "var(--color-primary)" },
  { id: "ocean", label: "深海蓝", color: "var(--dashboard-accent-ocean)" },
  { id: "teal", label: "青黛", color: "var(--color-teal)" },
  { id: "violet", label: "紫灰", color: "var(--color-violet)" },
  { id: "graphite", label: "石墨", color: "var(--dashboard-accent-graphite)" },
];
const SIZE_OPTIONS = [
  { id: "compact", label: "紧凑", description: "适合辅助信息" },
  { id: "standard", label: "标准", description: "日常查看尺寸" },
  { id: "emphasis", label: "重点", description: "扩大为主要内容" },
];
const CHART_OPTIONS = {
  kpi: [
    { id: "number", label: "指标值", description: "突出显示一个汇总值" },
  ],
  trend: [
    { id: "line", label: "折线图", description: "观察连续变化与拐点" },
    { id: "area", label: "面积图", description: "强调趋势与数量规模" },
    { id: "bar", label: "柱状图", description: "比较各时间点数值" },
  ],
  rank: [
    { id: "horizontal_bar", label: "横向条形图", description: "适合名称较长的排行" },
    { id: "bar", label: "柱状图", description: "比较少量分类的高低" },
    { id: "donut", label: "环形图", description: "查看分类占比与总量" },
    { id: "pie", label: "饼图", description: "查看少量分类构成" },
    { id: "treemap", label: "矩形树图", description: "比较较多分类的构成" },
    { id: "table", label: "数据表", description: "精确读取并浏览全部结果" },
  ],
};
const DEFAULT_CHART_TYPE = { kpi: "number", trend: "line", rank: "horizontal_bar" };
const CATEGORY_COLORS = ["var(--color-primary)", "var(--color-teal)", "var(--color-violet)", "var(--color-amber)", "var(--color-red)", "var(--color-green)"];

function accentColor(accent) {
  return ACCENT_OPTIONS.find((item) => item.id === accent)?.color || ACCENT_OPTIONS[0].color;
}

function effectiveChartType(widget) {
  const configured = widget.appearance?.chartType;
  return !configured || configured === "auto" ? DEFAULT_CHART_TYPE[widget.kind] : configured;
}

function chartHeight(widget) {
  return Math.max(170, Math.min(390, widget.layout.h * 62 - 104));
}

function deepCopy(value) {
  return JSON.parse(JSON.stringify(value));
}

function sourceTypeLabel(type) {
  return { mysql: "MySQL", postgresql: "PostgreSQL", mariadb: "MariaDB", sqlserver: "SQL Server", oracle: "Oracle" }[type]
    ?? String(type || "数据源").toUpperCase();
}

function formatTime(value) {
  if (!value) return "暂无记录";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function formatValue(value, unit = "") {
  if (value === null || value === undefined || value === "") return "—";
  const number = Number(value);
  if (!Number.isFinite(number)) return String(value);
  return new Intl.NumberFormat("zh-CN", { maximumFractionDigits: unit === "%" ? 2 : 1 }).format(number);
}

function queryKey(widget, period) {
  return `${widget.id}:${widget.metricId}:${widget.dimension || ""}:${period}`;
}

export default function OverviewPage() {
  const { navigatePage } = useOutletContext();
  const [sources, setSources] = useState([]);
  const [sourceId, setSourceId] = useState(() => Number(window.sessionStorage.getItem(ACTIVE_SOURCE_KEY)) || null);
  const [overview, setOverview] = useState(null);
  const [catalog, setCatalog] = useState([]);
  const [defaultConfig, setDefaultConfig] = useState(null);
  const [savedConfig, setSavedConfig] = useState(null);
  const [config, setConfig] = useState(null);
  const [results, setResults] = useState({});
  const [loadError, setLoadError] = useState(null);
  const [notice, setNotice] = useState("");
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [addOpen, setAddOpen] = useState(false);
  const [appearanceWidgetId, setAppearanceWidgetId] = useState(null);
  const [refreshToken, setRefreshToken] = useState(0);
  const querySignature = useMemo(() => JSON.stringify((config?.widgets ?? [])
    .filter((item) => item.metricId && ["kpi", "trend", "rank"].includes(item.kind))
    .map(({ id, kind, metricId, dimension }) => ({ id, kind, metricId, dimension: dimension || "" }))), [config?.widgets]);

  const load = async (requestedSourceId = sourceId) => {
    setLoadError(null);
    setOverview(null);
    try {
      const available = await listSources();
      const source = available.find((item) => item.id === requestedSourceId)
        ?? available.find((item) => item.isDefault) ?? available[0];
      if (!source) throw new Error("尚未配置可用数据源");
      const [overviewData, dashboardData] = await Promise.all([
        getBusinessOverview(source.id, USER_ID()), getDashboard(source.id),
      ]);
      setSources(available);
      setSourceId(source.id);
      setOverview(overviewData);
      setCatalog(dashboardData.catalog);
      setDefaultConfig(deepCopy(dashboardData.defaultConfig || dashboardData.config));
      setSavedConfig(deepCopy(dashboardData.config));
      setConfig(deepCopy(dashboardData.config));
      setEditing(false);
      window.sessionStorage.setItem(ACTIVE_SOURCE_KEY, String(source.id));
    } catch (error) {
      setLoadError(error);
    }
  };

  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!config || !sourceId) return undefined;
    let cancelled = false;
    const queryWidgets = JSON.parse(querySignature);
    setResults(Object.fromEntries(queryWidgets.map((item) => [queryKey(item, config.period), { state: "loading" }])));
    Promise.all(queryWidgets.map(async (widget) => {
      const key = queryKey(widget, config.period);
      try {
        const result = await queryDashboardWidget({
          sourceId, metricId: widget.metricId, kind: widget.kind, period: config.period,
          ...(widget.dimension ? { dimension: widget.dimension } : {}),
        });
        return [key, result];
      } catch (error) {
        return [key, { state: "error", message: error.message || "查询暂时失败" }];
      }
    })).then((entries) => { if (!cancelled) setResults(Object.fromEntries(entries)); });
    return () => { cancelled = true; };
  }, [config?.period, querySignature, refreshToken, sourceId]);

  const switchSource = (event) => {
    const nextId = Number(event.target.value);
    window.sessionStorage.removeItem(ANALYSIS_SESSION_KEY);
    load(nextId);
  };

  const startAnalysis = (prompt = "", sessionId = "") => {
    if (!overview?.capability?.canAnalyze) return;
    window.sessionStorage.setItem(ACTIVE_SOURCE_KEY, String(sourceId));
    if (prompt) window.sessionStorage.setItem(ANALYSIS_DRAFT_KEY, prompt);
    else window.sessionStorage.removeItem(ANALYSIS_DRAFT_KEY);
    if (sessionId) window.sessionStorage.setItem(ANALYSIS_SESSION_KEY, sessionId);
    else window.sessionStorage.removeItem(ANALYSIS_SESSION_KEY);
    navigatePage("analysis");
  };

  const changePeriod = async (period) => {
    if (editing) { setConfig((current) => ({ ...current, period })); return; }
    if (period === config.period) return;
    const previous = config;
    const next = { ...config, period };
    setConfig(next);
    try {
      const saved = await saveDashboard(sourceId, next);
      setSavedConfig(deepCopy(saved));
      setConfig(deepCopy(saved));
    } catch (error) {
      setConfig(previous);
      setNotice(error.message || "时间范围保存失败");
    }
  };

  const beginEdit = () => { setConfig(deepCopy(savedConfig)); setEditing(true); setNotice(""); };
  const cancelEdit = () => { setConfig(deepCopy(savedConfig)); setEditing(false); setAddOpen(false); setAppearanceWidgetId(null); };
  const persist = async () => {
    setSaving(true);
    setNotice("");
    try {
      const saved = await saveDashboard(sourceId, config);
      setSavedConfig(deepCopy(saved));
      setConfig(deepCopy(saved));
      setEditing(false);
      setAppearanceWidgetId(null);
      setNotice("布局已保存");
    } catch (error) {
      setNotice(error.message || "布局保存失败");
    } finally {
      setSaving(false);
    }
  };

  const restoreDefault = () => {
    if (!defaultConfig) return;
    setConfig({ ...deepCopy(defaultConfig), version: savedConfig.version });
    setNotice("已恢复默认布局，保存后生效");
  };

  if (loadError) return <PageFrame><ErrorNotice title="业务看板加载失败" detail={loadError.message || "请检查数据源连接后重试。"} onRetry={() => load()} /></PageFrame>;
  if (!overview || !config) return <RouteSkeleton />;

  const source = overview.source;
  const largeLayout = config.widgets.map((item) => ({ i: item.id, ...item.layout }));
  let mobileY = 0;
  const mobileLayout = config.widgets
    .slice()
    .sort((left, right) => left.layout.y - right.layout.y || left.layout.x - right.layout.x)
    .map((item) => {
      const layout = { i: item.id, x: 0, y: mobileY, w: 1, h: item.layout.h, minW: 1, maxW: 1, minH: item.layout.minH };
      mobileY += item.layout.h;
      return layout;
    });
  const layouts = { lg: largeLayout, sm: mobileLayout };
  const appearanceWidget = config.widgets.find((item) => item.id === appearanceWidgetId) || null;

  return (
    <PageFrame className="dashboard-page">
      <span hidden dangerouslySetInnerHTML={{ __html: "<!-- THESIS: A governed analysis workspace centered on one dominant chart, refusing the greeting-and-equal-card dashboard. OWN-WORLD: Cool white operational canvas, graphite type, teal-blue selection, hairline dividers, compact square controls. STORY: Choose scope, read evidence-backed measures, compare a dimension, then continue or start an investigation. FIRST VIEWPORT: A slim toolbar and KPI band lead to a 9/3 trend-and-context split; edit action stays top-right. FORM: Analysis workspace, ranked second but selected by the user; B staging; user-approved direction. -->" }} />
      <section className="dashboard-toolbar" aria-label="看板工具栏">
        <div className="dashboard-toolbar__main">
          <div className="dashboard-title">业务看板</div>
          <label className="dashboard-source"><span className="sr-only">当前数据源</span><select value={sourceId} onChange={switchSource}>{sources.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
          <div className="dashboard-segment" aria-label="统计周期">{PERIODS.map((item) => <button key={item.id} type="button" aria-pressed={config.period === item.id} onClick={() => changePeriod(item.id)}>{item.label}</button>)}</div>
        </div>
        <div className="dashboard-toolbar__actions">
          <Button size="sm" onClick={() => setRefreshToken((value) => value + 1)}><Icon name="refresh" size={14} />刷新</Button>
          <Button size="sm" variant={editing ? "primary" : "secondary"} onClick={editing ? persist : beginEdit} disabled={saving}><Icon name={editing ? "check" : "edit"} size={14} />{editing ? "保存布局" : "编辑布局"}</Button>
        </div>
        <div className="dashboard-provenance">
          <span>{sourceTypeLabel(source.type)} · {source.database}</span><span>企业已发布口径</span><span>只读查询</span><span>数据截至 {overview.snapshot.latestData || "待识别"}</span>
        </div>
      </section>
      {notice && <div className="dashboard-notice" role="status">{notice}<button type="button" onClick={() => setNotice("")} aria-label="关闭提示"><Icon name="close" size={13} /></button></div>}
      {!catalog.some((item) => item.kinds.length) && <section className="dashboard-empty-scope"><Icon name="knowledge" size={18} /><div><strong>当前数据源还没有可用于看板的聚合指标</strong><p>可继续使用调查探索数据，或由管理员发布并验证指标绑定。</p></div><Button size="sm" onClick={() => startAnalysis()}>发起调查</Button></section>}
      <Grid
        className={cn("dashboard-grid", editing && "is-editing")}
        layouts={layouts} breakpoints={{ lg: 720, sm: 0 }} cols={{ lg: 12, sm: 1 }} rowHeight={50} margin={[12, 12]} containerPadding={[0, 0]}
        isDraggable={editing} isResizable={editing} draggableHandle=".dashboard-drag-handle" resizeHandles={["se"]}
        onLayoutChange={(layout, allLayouts) => { if (editing) setConfig((current) => updateWidgetLayouts(current, allLayouts?.lg || layout)); }}
      >
        {config.widgets.map((widget) => <div key={widget.id} className="dashboard-widget-wrap"><DashboardWidget widget={widget} result={results[queryKey(widget, config.period)]} catalog={catalog} overview={overview} editing={editing} onConfigure={() => setAppearanceWidgetId(widget.id)} onRemove={() => { setConfig((current) => removeWidget(current, widget.id)); setAppearanceWidgetId((current) => current === widget.id ? null : current); }} onMove={(delta, resize) => setConfig((current) => moveWidget(current, widget.id, delta, resize))} onStartAnalysis={startAnalysis} /></div>)}
      </Grid>
      {editing && <div className="dashboard-editbar" role="region" aria-label="布局编辑操作"><span><i />布局尚未保存</span><button type="button" onClick={restoreDefault} disabled={saving}>恢复默认</button><Button size="sm" onClick={() => setAddOpen(true)} disabled={config.widgets.length >= 20}><Icon name="plus" size={14} />添加组件</Button><Button size="sm" onClick={cancelEdit}>取消</Button><Button size="sm" variant="primary" onClick={persist} disabled={saving}>{saving ? "保存中…" : "保存布局"}</Button></div>}
      <AddWidgetDialog open={addOpen} catalog={catalog} widgets={config.widgets} onClose={() => setAddOpen(false)} onAdd={(definition) => { setConfig((current) => addWidget(current, definition)); setAddOpen(false); }} />
      <WidgetAppearanceDialog widget={appearanceWidget} onClose={() => setAppearanceWidgetId(null)} onAccent={(accent) => setConfig((current) => updateWidgetAppearance(current, appearanceWidget.id, { accent }))} onChartType={(chartType) => setConfig((current) => updateWidgetAppearance(current, appearanceWidget.id, { chartType }))} onSize={(preset) => setConfig((current) => applyWidgetSizePreset(current, appearanceWidget.id, preset))} />
    </PageFrame>
  );
}

function DashboardWidget({ widget, result, catalog, overview, editing, onConfigure, onRemove, onMove, onStartAnalysis }) {
  const metric = catalog.find((item) => item.id === widget.metricId);
  const configuredTitle = widget.title || metric?.name || ({ recent: "近期调查", context: "调查与提醒", catalog: "可用指标" }[widget.kind] ?? "看板组件");
  const title = widget.kind === "rank" && metric?.name && !configuredTitle.includes(metric.name)
    ? `${metric.name} · ${configuredTitle}`
    : configuredTitle;
  const onHandleKey = (event) => {
    const delta = { ArrowLeft: { x: -1, y: 0 }, ArrowRight: { x: 1, y: 0 }, ArrowUp: { x: 0, y: -1 }, ArrowDown: { x: 0, y: 1 } }[event.key];
    if (!delta) return;
    event.preventDefault();
    onMove(delta, event.shiftKey);
  };
  const accent = widget.appearance?.accent || "default";
  const color = accentColor(accent);
  return <article className={cn("dashboard-widget", `dashboard-widget--${widget.kind}`, accent !== "default" && "has-custom-accent")} data-accent={accent} style={{ "--dashboard-widget-accent": color }}>
    <header className="dashboard-widget__header"><div className="dashboard-widget__heading">{editing && <button type="button" className="dashboard-drag-handle" onKeyDown={onHandleKey} aria-label={`移动或调整${title}组件`} title="拖动移动；方向键移动；Shift + 方向键调整大小"><Icon name="more" size={16} /></button>}<h2>{title}</h2>{metric?.definition && <span className="dashboard-help" title={metric.definition}><Icon name="help" size={13} /></span>}</div>{editing ? <div className="dashboard-widget__actions"><button type="button" className="dashboard-widget__configure" onClick={onConfigure} aria-label={`设置${title}组件外观`} title="外观与尺寸"><Icon name="settings" size={14} /></button><button type="button" className="dashboard-widget__remove" onClick={onRemove} aria-label={`移除${title}组件`}><Icon name="trash" size={14} /></button></div> : <Icon name="more" size={15} className="text-faint" />}</header>
    <div className="dashboard-widget__body">{widget.kind === "kpi" && <KpiBody result={result} metric={metric} />}{widget.kind === "trend" && <TrendBody result={result} metric={metric} widget={widget} color={color} />}{widget.kind === "rank" && <RankBody result={result} metric={metric} widget={widget} color={color} />}{widget.kind === "context" && <ContextBody overview={overview} onStartAnalysis={onStartAnalysis} />}{widget.kind === "recent" && <RecentBody overview={overview} onStartAnalysis={onStartAnalysis} />}{widget.kind === "catalog" && <CatalogBody catalog={catalog} onStartAnalysis={onStartAnalysis} />}</div>
  </article>;
}

function QueryState({ result, compact = false }) {
  if (!result || result.state === "loading") return <div className={cn("dashboard-query-state", compact && "is-compact")}><span className="dashboard-spinner" />正在查询…</div>;
  if (result.state === "ready") return null;
  return <div className={cn("dashboard-query-state", `is-${result.state}`, compact && "is-compact")}><Icon name={result.state === "error" ? "alert" : "database"} size={16} /><span>{result.message || "暂无可展示数据"}</span></div>;
}

function KpiBody({ result, metric }) {
  if (result?.state !== "ready") return <QueryState result={result} compact />;
  const value = result.rows?.[0]?.[result.rows[0].length - 1];
  return <><div className="dashboard-kpi-value">{formatValue(value, metric?.unit)}<span>{metric?.unit}</span></div><div className="dashboard-kpi-meta">数据截至 {result.range?.anchor || "待识别"}</div></>;
}

function TrendBody({ result, metric, widget, color }) {
  if (result?.state !== "ready") return <QueryState result={result} />;
  const [xKey, valueKey] = result.columns;
  const data = result.rows.map((row) => ({ [xKey]: row[0], [valueKey]: row[row.length - 1] }));
  const type = effectiveChartType(widget);
  const height = chartHeight(widget);
  return <><div className="dashboard-chart-meta"><span>指标：{metric?.name}</span><span>按日</span><span>{result.range?.start || "全部"} — {result.range?.anchor}</span></div>{type === "bar" ? <RankBarChart data={data} xKey={xKey} bars={[{ key: valueKey, name: metric?.name, color }]} unit={metric?.unit} height={height} /> : <TrendLineChart data={data} xKey={xKey} series={[{ key: valueKey, name: metric?.name, color }]} unit={metric?.unit} height={height} area={type === "area"} />}</>;
}

function RankBody({ result, metric, widget, color }) {
  if (result?.state !== "ready") return <QueryState result={result} />;
  const rows = result.rows.map((row, index) => ({ label: row[0] ?? "未标注", name: row[0] ?? "未标注", value: Number(row[row.length - 1]), color: index === 0 ? color : CATEGORY_COLORS[index % CATEGORY_COLORS.length] }));
  const requestedType = effectiveChartType(widget);
  const compositionType = ["donut", "pie", "treemap"].includes(requestedType);
  const canShowComposition = rows.length > 0 && rows.every((item) => Number.isFinite(item.value) && item.value >= 0) && rows.some((item) => item.value > 0);
  const type = compositionType && !canShowComposition ? "table" : requestedType;
  const height = chartHeight(widget);
  const total = rows.reduce((sum, item) => sum + (Number.isFinite(item.value) ? item.value : 0), 0);
  let visualization;
  if (type === "bar") visualization = <RankBarChart data={rows} xKey="label" bars={[{ key: "value", name: metric?.name, color }]} height={height} unit={metric?.unit} />;
  else if (type === "donut" || type === "pie") visualization = <div className="dashboard-share-visual"><ShareDonutChart data={rows} height={height} unit={metric?.unit} variant={type} centerTitle="合计" centerValue={formatValue(total, metric?.unit)} /><CategoryLegend data={rows} unit={metric?.unit} /></div>;
  else if (type === "treemap") visualization = <CategoryTreemapChart data={rows} height={height} unit={metric?.unit} />;
  else if (type === "table") visualization = <CategoryResultTable data={rows} dimension={widget.dimension || result.columns?.[0]} metric={metric} />;
  else visualization = <HorizontalRankChart data={rows} height={height} unit={metric?.unit} color={color} valueName={metric?.name} />;
  return <><div className="dashboard-chart-meta"><span>指标：{metric?.name || "未命名指标"}</span><span>对比维度：{widget.dimension || result.columns?.[0] || "未指定"}</span>{metric?.unit && <span>单位：{metric.unit}</span>}<span>{result.range?.start || "全部"} — {result.range?.anchor || "最新"}</span></div>{compositionType && !canShowComposition && <div className="dashboard-chart-advice"><Icon name="info" size={13} />结果包含负数或全部为零，已改用数据表，避免产生错误的占比。</div>}{visualization}</>;
}

function CategoryLegend({ data, unit }) {
  return <div className="dashboard-chart-legend" aria-label="分类图例">{data.map((item) => <div key={item.name}><i style={{ background: item.color }} /><span title={item.name}>{item.name}</span><strong>{formatValue(item.value, unit)}{unit}</strong></div>)}</div>;
}

function CategoryResultTable({ data, dimension = "分类", metric }) {
  return <div className="dashboard-result-table" role="table" aria-label={`${metric?.name || "指标"}分类数据`}><div role="row" className="is-header"><span>{dimension}</span><span>{metric?.name || "数值"}{metric?.unit ? `（${metric.unit}）` : ""}</span></div>{data.map((item) => <div role="row" key={item.name}><span title={item.name}>{item.name}</span><strong>{formatValue(item.value, metric?.unit)}</strong></div>)}</div>;
}

function ContextBody({ overview, onStartAnalysis }) {
  const pending = overview.recent.items.filter((item) => item.status !== "succeeded").slice(0, 2);
  return <div className="dashboard-context">{pending.length ? pending.map((item) => <button type="button" key={item.id} className="dashboard-context__item" onClick={() => onStartAnalysis("", item.id)}><span><strong>{item.title}</strong><small>{formatTime(item.updatedAt)} · {item.runCount} 次分析</small></span><StatusPill tone={item.status === "failed" || item.status === "blocked" ? "red" : "amber"}>{RUN_LABELS[item.status] || item.status}</StatusPill></button>) : <div className="dashboard-context__empty">当前没有待继续的调查</div>}<Button variant="primary" className="w-full" onClick={() => onStartAnalysis()} disabled={!overview.capability.canAnalyze}><Icon name="analysis" size={14} />发起分析</Button></div>;
}

function RecentBody({ overview, onStartAnalysis }) {
  if (!overview.recent.items.length) return <div className="dashboard-context__empty">发起分析后，问题和证据会保存在这里。</div>;
  return <div className="dashboard-table" role="table" aria-label="近期调查"><div className="dashboard-table__row is-header" role="row"><span>调查主题</span><span>状态</span><span>更新时间</span><span>操作</span></div>{overview.recent.items.map((item) => <div className="dashboard-table__row" role="row" key={item.id}><strong>{item.title}</strong><span>{RUN_LABELS[item.status] || item.status}</span><span>{formatTime(item.updatedAt)}</span><button type="button" onClick={() => onStartAnalysis("", item.id)}>继续调查</button></div>)}</div>;
}

function CatalogBody({ catalog, onStartAnalysis }) {
  return <div className="dashboard-catalog"><div className="dashboard-catalog__summary"><strong>{catalog.length}</strong><span>个已发布指标</span></div><div className="dashboard-catalog__list" role="list">{catalog.map((item) => <button type="button" role="listitem" key={item.id} onClick={() => onStartAnalysis(`请分析${item.name}的最近趋势，并按可用业务维度对比。`)}><span><strong>{item.name}</strong><small>{item.topic} · v{item.version}</small></span><Icon name="arrow" size={12} /></button>)}</div>{catalog.length > 5 && <div className="dashboard-catalog__footer"><span>已显示全部 {catalog.length} 项</span><span>滚动查看</span></div>}</div>;
}

function ChartTypePreview({ type }) {
  if (type === "number") return <svg viewBox="0 0 48 28" aria-hidden="true"><text x="24" y="20" textAnchor="middle">42</text></svg>;
  if (type === "line" || type === "area") return <svg viewBox="0 0 48 28" aria-hidden="true"><path className="chart-preview-area" d="M3 23 13 17 23 19 33 8 45 12V25H3Z" opacity={type === "area" ? 1 : 0} /><path d="M3 23 13 17 23 19 33 8 45 12" /></svg>;
  if (type === "bar") return <svg viewBox="0 0 48 28" aria-hidden="true"><rect x="4" y="14" width="7" height="11" /><rect x="15" y="8" width="7" height="17" /><rect x="26" y="11" width="7" height="14" /><rect x="37" y="4" width="7" height="21" /></svg>;
  if (type === "horizontal_bar") return <svg viewBox="0 0 48 28" aria-hidden="true"><rect x="3" y="3" width="38" height="5" /><rect x="3" y="11" width="30" height="5" /><rect x="3" y="19" width="22" height="5" /></svg>;
  if (type === "donut" || type === "pie") return <svg viewBox="0 0 48 28" aria-hidden="true"><circle cx="24" cy="14" r="10" className="chart-preview-pie" /><path d="M24 14V4A10 10 0 0 1 33.5 17Z" />{type === "donut" && <circle cx="24" cy="14" r="4.5" className="chart-preview-hole" />}</svg>;
  if (type === "treemap") return <svg viewBox="0 0 48 28" aria-hidden="true"><rect x="3" y="3" width="25" height="22" /><rect x="30" y="3" width="15" height="10" /><rect x="30" y="15" width="15" height="10" /></svg>;
  return <svg viewBox="0 0 48 28" aria-hidden="true"><path d="M3 5h42M3 13h42M3 21h42M17 3v22M34 3v22" /></svg>;
}

function ChartTypeChoices({ kind, value, onChange }) {
  const options = CHART_OPTIONS[kind] || [];
  if (options.length <= 1) return null;
  return <div className="dashboard-chart-options" role="group" aria-label="图表类型">{options.map((item) => <button type="button" key={item.id} aria-label={`图表类型：${item.label}，${item.description}`} aria-pressed={value === item.id} onClick={() => onChange(item.id)}><ChartTypePreview type={item.id} /><span><strong>{item.label}</strong><small>{item.description}</small></span><Icon name="check" size={13} /></button>)}</div>;
}

function WidgetAppearanceDialog({ widget, onClose, onAccent, onChartType, onSize }) {
  if (!widget) return null;
  const currentAccent = widget.appearance?.accent || "default";
  const currentChartType = effectiveChartType(widget);
  return <Modal open onClose={onClose} maxWidth={560} label="组件外观与尺寸"><div className="dashboard-dialog dashboard-appearance-dialog"><div className="dashboard-dialog__header"><div><h2>图表、外观与尺寸</h2><p>调整“{widget.title || "看板组件"}”，修改将在保存布局后生效。</p></div><button type="button" onClick={onClose} aria-label="关闭"><Icon name="close" size={16} /></button></div><div className="dashboard-dialog__body">{CHART_OPTIONS[widget.kind]?.length > 1 && <fieldset><legend>图表类型</legend><ChartTypeChoices kind={widget.kind} value={currentChartType} onChange={onChartType} /><small>只显示与当前数据形态兼容的图表，避免无意义的可视化。</small></fieldset>}<fieldset><legend>强调色</legend><div className="dashboard-accent-options">{ACCENT_OPTIONS.map((item) => <button type="button" key={item.id} aria-label={`强调色：${item.label}`} aria-pressed={currentAccent === item.id} onClick={() => onAccent(item.id)}><i style={{ background: item.color }} /><span>{item.label}</span><Icon name="check" size={13} /></button>)}</div><small>颜色只用于标题强调和图表主色，不改变成功、告警等业务状态色。</small></fieldset><fieldset><legend>快捷尺寸</legend><div className="dashboard-size-options">{SIZE_OPTIONS.map((item) => <button type="button" key={item.id} onClick={() => onSize(item.id)}><strong>{item.label}</strong><small>{item.description}</small></button>)}</div><small>当前为 {widget.layout.w} × {widget.layout.h} 网格；应用快捷尺寸后仍可拖动右下角微调。</small></fieldset></div><div className="dashboard-dialog__footer"><span>随看板布局统一保存</span><Button variant="primary" onClick={onClose}>完成</Button></div></div></Modal>;
}

function AddWidgetDialog({ open, catalog, widgets, onClose, onAdd }) {
  const initialMetric = catalog.find((item) => item.kinds.length);
  const [metricId, setMetricId] = useState(initialMetric?.id || "");
  const metric = useMemo(() => catalog.find((item) => item.id === metricId) ?? initialMetric, [catalog, initialMetric, metricId]);
  const [category, setCategory] = useState("metric");
  const [kind, setKind] = useState("kpi");
  const [chartType, setChartType] = useState("number");
  const [dimension, setDimension] = useState("");
  const existingSystemKinds = useMemo(() => new Set(widgets.map((item) => item.kind)), [widgets]);
  const firstAvailableSystem = WORKSPACE_WIDGETS.find((item) => !existingSystemKinds.has(item.kind));
  const [systemKind, setSystemKind] = useState(firstAvailableSystem?.kind || "context");
  const systemWidget = WORKSPACE_WIDGETS.find((item) => item.kind === systemKind);

  useEffect(() => {
    if (!open) return;
    setCategory("metric");
    setSystemKind(firstAvailableSystem?.kind || "context");
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (open && metric) { const nextKind = metric.kinds.includes(kind) ? kind : (metric.kinds[0] || "kpi"); setKind(nextKind); setChartType(DEFAULT_CHART_TYPE[nextKind]); setDimension(metric.dimensions?.[0] || ""); } }, [open, metricId]); // eslint-disable-line react-hooks/exhaustive-deps

  const canAddMetric = Boolean(metric) && (kind !== "rank" || Boolean(dimension));
  const canAddSystem = Boolean(systemWidget) && !existingSystemKinds.has(systemWidget.kind);
  const submit = () => {
    if (category === "workspace") {
      if (canAddSystem) onAdd({ kind: systemWidget.kind, title: systemWidget.title });
      return;
    }
    if (!canAddMetric) return;
    onAdd({ kind, metricId: metric.id, ...(kind === "rank" ? { dimension } : {}), appearance: { accent: "default", chartType }, title: kind === "trend" ? `${metric.name}趋势` : kind === "rank" ? `${metric.name} · 按${dimension}对比` : metric.name });
  };

  const selectKind = (nextKind) => { setKind(nextKind); setChartType(DEFAULT_CHART_TYPE[nextKind]); };
  return <Modal open={open} onClose={onClose} maxWidth={620} label="添加看板组件"><div className="dashboard-dialog"><div className="dashboard-dialog__header"><div><h2>添加组件</h2><p>添加业务指标，或恢复被移除的工作区组件。</p></div><button type="button" onClick={onClose} aria-label="关闭"><Icon name="close" size={16} /></button></div><div className="dashboard-dialog__body"><div className="dashboard-choice dashboard-dialog__category" role="group" aria-label="组件类别"><button type="button" aria-pressed={category === "metric"} onClick={() => setCategory("metric")}>数据指标</button><button type="button" aria-pressed={category === "workspace"} onClick={() => setCategory("workspace")}>工作区组件</button></div>{category === "metric" ? <><label><span>指标 <i title="由企业管理员发布，并绑定到当前数据源。"><Icon name="help" size={12} /></i></span><select value={metric?.id || ""} onChange={(event) => setMetricId(event.target.value)}>{catalog.filter((item) => item.kinds.length).map((item) => <option key={item.id} value={item.id}>{item.name} · {item.topic}</option>)}</select><small>{metric?.definition || "当前没有适合看板的聚合指标。"}</small></label><fieldset><legend>数据形态</legend><div className="dashboard-choice">{[{ id: "kpi", label: "汇总值" }, { id: "trend", label: "时间趋势" }, { id: "rank", label: "分类对比" }].map((item) => <button type="button" key={item.id} aria-pressed={kind === item.id} disabled={!metric?.kinds.includes(item.id)} onClick={() => selectKind(item.id)}>{item.label}</button>)}</div></fieldset>{kind === "rank" && <label><span>对比维度</span><select value={dimension} onChange={(event) => setDimension(event.target.value)}>{metric?.dimensions.map((item) => <option key={item}>{item}</option>)}</select><small>选择一个已登记的业务维度拆分指标。</small></label>}<fieldset><legend>图表类型</legend><ChartTypeChoices kind={kind} value={chartType} onChange={setChartType} />{kind === "kpi" && <small>汇总值使用大数字卡片展示。</small>}</fieldset></> : <div className="dashboard-system-options" role="group" aria-label="工作区组件">{WORKSPACE_WIDGETS.map((item) => { const added = existingSystemKinds.has(item.kind); return <button type="button" key={item.kind} className={cn(systemKind === item.kind && "is-selected")} aria-pressed={systemKind === item.kind} disabled={added} onClick={() => setSystemKind(item.kind)}><Icon name={item.icon} size={17} /><span><strong>{item.title}</strong><small>{item.description}</small></span><em>{added ? "已添加" : "可添加"}</em></button>; })}</div>}</div><div className="dashboard-dialog__footer"><span>{category === "workspace" && !firstAvailableSystem ? "工作区组件已全部添加" : "尚未保存到个人看板"}</span><Button onClick={onClose}>取消</Button><Button variant="primary" disabled={category === "metric" ? !canAddMetric : !canAddSystem} onClick={submit}>添加组件</Button></div></div></Modal>;
}
