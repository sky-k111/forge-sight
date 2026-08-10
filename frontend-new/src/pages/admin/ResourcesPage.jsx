import { useCallback, useEffect, useMemo, useState } from "react";
import Icon from "../../components/ui/Icon";
import cn from "../../lib/cn";
import { Button, Card, EmptyState, ErrorNotice, Modal, PageFrame, RouteSkeleton, SectionHeader, StatusPill } from "../../components/ui";
import { confirmTableSemantics, getTableFields, getTablePreview, listTables, listUnregisteredTables, suggestTableSemantics } from "../../api/resources";
import { createSource, listSources, scanSource, testSource, updateSource } from "../../api/datasources";
import { buildSchemaLinkPath, getSchemaNodeStyle, SCHEMA_GRAPH_HEIGHT, SCHEMA_GRAPH_WIDTH } from "./schemaGraphLayout";
import SourceTypeMark from "../../components/ui/SourceTypeMark";

const EMPTY_FORM = { name: "", type: "", host: "127.0.0.1", port: "", database: "", username: "", password: "" };

// 已支持的数据源类型（对齐后端 SUPPORTED_TYPES：mysql / postgresql），图标为真实数据库 logo
const SOURCE_TYPE_META = {
  mysql: { label: "MySQL", description: "关系型数据库，制造业务库常用。默认端口 3306。" },
  postgresql: { label: "PostgreSQL", description: "对象关系型数据库。默认端口 5432。" },
};

// 规划中类型：仅展示扩展能力，不可选择
const UPCOMING_TYPE_META = {
  sqlserver: { label: "SQL Server", description: "Microsoft 企业级数据库，适配器规划中。" },
  oracle: { label: "Oracle", description: "Oracle 企业级数据库，适配器规划中。" },
};

const inputClass = "h-9 w-full rounded-control border border-line bg-surface px-2.5 text-xs font-normal text-ink outline-none transition placeholder:text-faint focus:border-primary/50 focus:ring-2 focus:ring-primary/25";

export default function ResourcesPage() {
  const [tables, setTables] = useState(null);
  const [error, setError] = useState(null);
  const [sources, setSources] = useState([]);
  const [currentSourceId, setCurrentSourceId] = useState(null);
  const [selectedName, setSelectedName] = useState("mes_process_output");
  const [previewOpen, setPreviewOpen] = useState(false);
  const [connectorOpen, setConnectorOpen] = useState(false);
  const [sourceMenuOpen, setSourceMenuOpen] = useState(false);
  const [form, setForm] = useState(EMPTY_FORM);
  const [editingId, setEditingId] = useState(null);
  const [editNotice, setEditNotice] = useState("");
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState(null);
  const [pendingSourceId, setPendingSourceId] = useState(null);
  const [unregistered, setUnregistered] = useState([]);
  const [search, setSearch] = useState("");
  const [suggestion, setSuggestion] = useState(null);
  const [suggesting, setSuggesting] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [integrated, setIntegrated] = useState("");

  const load = useCallback(() => {
    setError(null);
    setTables(null);
    listTables(currentSourceId ? { sourceId: currentSourceId } : {})
      .then(setTables)
      .catch(setError);
  }, [currentSourceId]);

  useEffect(load, [load]);
  useEffect(() => {
    listSources().then(setSources).catch(() => setSources([]));
  }, []);

  const visibleTables = useMemo(
    () => (tables ?? []).filter((table) => `${table.cn}${table.name}${table.topic}`.includes(search.trim())),
    [search, tables],
  );
  const selectedTable = tables?.find((table) => table.name === selectedName) ?? tables?.[0];
  const currentSource = sources.find((source) => source.id === currentSourceId) ?? sources.find((source) => source.isDefault);

  const switchSource = (id) => {
    setCurrentSourceId(id ? Number(id) : null);
    setSelectedName("");
    setPreviewOpen(false);
  };

  const updateForm = (key) => (event) => setForm((value) => ({ ...value, [key]: event.target.value }));

  // 打开接入面板时重置到「选择类型」第一步
  const openConnector = () => {
    setForm(EMPTY_FORM);
    setEditingId(null);
    setEditNotice("");
    setTestResult(null);
    setSuggestion(null);
    setUnregistered([]);
    setConnectorOpen(true);
  };

  // 打开已有数据源的配置管理（跳过类型选择，类型不可改）
  const openEditor = (source) => {
    setForm({
      name: source.name,
      type: source.type,
      host: source.host,
      port: source.port ? String(source.port) : "",
      database: source.database,
      username: source.username,
      password: "",
    });
    setEditingId(source.id);
    setEditNotice("");
    setTestResult(null);
    setSuggestion(null);
    setUnregistered([]);
    setConnectorOpen(true);
  };

  // 编辑模式：保存修改（密码留空 = 不变）
  const saveEdit = async () => {
    setTesting(true);
    setEditNotice("");
    try {
      await updateSource(editingId, {
        name: form.name,
        host: form.host,
        port: form.port ? Number(form.port) : undefined,
        database: form.database,
        username: form.username,
        password: form.password || undefined,
      });
      setSources(await listSources());
      setEditNotice("配置已保存");
    } catch (err) {
      setTestResult({ ok: false, message: err.message });
    } finally {
      setTesting(false);
    }
  };

  // 编辑模式：测试已有数据源连接
  const runTestExisting = async () => {
    setTesting(true);
    setEditNotice("");
    try {
      setTestResult(await testSource(editingId));
      setSources(await listSources());
    } catch (err) {
      setTestResult({ ok: false, message: err.message });
    } finally {
      setTesting(false);
    }
  };

  // 编辑模式：重新扫描表结构；若扫的是当前源则刷新资源目录
  const runRescan = async () => {
    setTesting(true);
    setEditNotice("");
    try {
      const result = await scanSource(editingId);
      setEditNotice(`重新扫描完成：${result.tables ?? "?"} 张表 / ${result.fields ?? "?"} 个字段`);
      setSources(await listSources());
      if (editingId === currentSource?.id) load();
    } catch (err) {
      setTestResult({ ok: false, message: err.message });
    } finally {
      setTesting(false);
    }
  };

  const saveAndTest = async () => {
    setTesting(true);
    setTestResult(null);
    setUnregistered([]);
    setSuggestion(null);
    try {
      const created = await createSource({ ...form, port: form.port ? Number(form.port) : undefined });
      setPendingSourceId(created.id);
      const result = await testSource(created.id);
      setTestResult(result);
      if (result.ok) {
        await scanSource(created.id);
        setUnregistered(await listUnregisteredTables(created.id));
        setSources(await listSources());
      }
    } catch (err) {
      setTestResult({ ok: false, message: err.message });
    } finally {
      setTesting(false);
    }
  };

  const runSuggest = async (tableName) => {
    setSuggesting(true);
    try {
      setSuggestion(await suggestTableSemantics(tableName, pendingSourceId));
    } finally {
      setSuggesting(false);
    }
  };

  const confirmIntegration = async () => {
    setConfirming(true);
    try {
      const created = await confirmTableSemantics(suggestion.name, pendingSourceId, suggestion);
      setIntegrated(created.name);
      setSuggestion(null);
      setUnregistered((list) => list.filter((table) => table.name !== created.name));
      switchSource(pendingSourceId);
      setSelectedName(created.name);
    } finally {
      setConfirming(false);
    }
  };

  if (error) {
    return (
      <PageFrame>
        <ErrorNotice title="数据资源加载失败" detail="请检查数据连接后重试。" onRetry={load} />
      </PageFrame>
    );
  }
  if (!tables) return <RouteSkeleton />;

  return (
    <PageFrame>
      <Card className="flex flex-wrap items-center gap-5 px-5 py-4">
        <div className="relative">
          <button
            type="button"
            onClick={() => setSourceMenuOpen((value) => !value)}
            aria-haspopup="listbox"
            aria-expanded={sourceMenuOpen}
            className="flex cursor-pointer items-center gap-3 rounded-control border border-line bg-surface px-3 py-2 text-left transition hover:border-line-strong hover:bg-surface-2"
          >
            <SourceTypeMark type={currentSource?.type} />
            <span className="min-w-0">
              <strong className="block max-w-52 truncate text-body font-semibold text-ink">{currentSource?.name ?? "数据源"}</strong>
              <small className="block text-[11px] text-faint">
                {currentSource ? `${currentSource.typeLabel} · 只读连接 · ${currentSource.status}` : "加载中"}
              </small>
            </span>
            <Icon name="chevron" size={13} className={cn("shrink-0 text-faint transition-transform duration-150", sourceMenuOpen ? "" : "rotate-90")} />
          </button>
          {sourceMenuOpen && (
            <>
              <button type="button" aria-hidden tabIndex={-1} className="fixed inset-0 z-10 cursor-default" onClick={() => setSourceMenuOpen(false)} />
              <div role="listbox" aria-label="切换数据源" className="motion-soft-rise absolute left-0 top-full z-20 mt-2 w-72 rounded-panel border border-line bg-surface p-1.5 shadow-card-hover">
                <div className="px-2 pb-1 pt-1.5 text-[10px] font-semibold uppercase tracking-wider text-faint">切换数据源</div>
                {sources.map((source) => {
                  const active = source.id === currentSource?.id;
                  return (
                    <div
                      key={source.id}
                      className={cn(
                        "flex items-center gap-1 rounded-control pr-1 transition-colors",
                        active ? "bg-primary-soft" : "hover:bg-surface-2",
                      )}
                    >
                      <button
                        type="button"
                        role="option"
                        aria-selected={active}
                        onClick={() => { switchSource(source.id); setSourceMenuOpen(false); }}
                        className="flex min-w-0 flex-1 cursor-pointer items-center gap-2.5 rounded-control px-2 py-2 text-left"
                      >
                        <SourceTypeMark type={source.type} className="h-8 w-8 text-[10px]" />
                        <span className="min-w-0 flex-1">
                          <strong className="block truncate text-xs font-semibold text-ink">{source.name}</strong>
                          <small className="block truncate font-mono text-[10px] text-faint">{source.typeLabel} · {source.host}:{source.port}</small>
                        </span>
                        {active
                          ? <Icon name="check" size={14} className="shrink-0 text-primary" />
                          : <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", source.status === "连接正常" ? "bg-green" : "bg-line-strong")} />}
                      </button>
                      <button
                        type="button"
                        aria-label={`管理 ${source.name}`}
                        title="配置管理"
                        onClick={() => { setSourceMenuOpen(false); openEditor(source); }}
                        className="grid h-7 w-7 shrink-0 cursor-pointer place-items-center rounded-control text-faint transition-colors hover:bg-surface hover:text-ink"
                      >
                        <Icon name="settings" size={14} />
                      </button>
                    </div>
                  );
                })}
                <button
                  type="button"
                  onClick={() => { setSourceMenuOpen(false); openConnector(); }}
                  className="mt-1 flex w-full cursor-pointer items-center gap-2.5 rounded-control border-t border-line px-2 pb-1.5 pt-2.5 text-left text-xs font-semibold text-muted transition-colors hover:bg-surface-2 hover:text-ink"
                >
                  <span className="grid h-8 w-8 shrink-0 place-items-center rounded-control border border-dashed border-line-strong text-faint"><Icon name="plus" size={13} /></span>
                  接入新数据源
                </button>
              </div>
            </>
          )}
        </div>
        <div className="flex items-center gap-5 text-xs text-muted">
          <span><b className="mr-1 font-mono text-base font-semibold text-ink">{tables.length}</b>数据表</span>
          <span><b className="mr-1 font-mono text-base font-semibold text-ink">{tables.reduce((sum, table) => sum + table.fields, 0)}</b>字段</span>
        </div>
        <Button variant="primary" className="ml-auto" onClick={openConnector}>
          <Icon name="plus" size={15} />接入数据源
        </Button>
      </Card>

      <Modal open={connectorOpen} onClose={() => setConnectorOpen(false)} label={editingId == null ? "接入数据源" : "数据源配置管理"} maxWidth={720}>
        <div className="p-5">
          {!form.type && editingId == null ? (
            <>
              <div className="flex items-start justify-between gap-4">
                <div className="min-w-0">
                  <span className="text-[11px] font-semibold tracking-wide text-faint">新建数据连接 · 第 1 步</span>
                  <h3 className="mt-1 text-base font-semibold tracking-tight text-ink">选择数据源类型</h3>
                  <p className="mt-1 max-w-xl text-xs leading-relaxed text-muted">选择一个已启用的数据库适配器，下一步填写连接配置。全程只读，不会写入业务数据库。</p>
                </div>
                <Button size="sm" variant="ghost" aria-label="关闭接入面板" onClick={() => setConnectorOpen(false)}>
                  <Icon name="plus" size={14} className="rotate-45" />
                </Button>
              </div>
              <div className="mt-4 grid gap-2 sm:grid-cols-2">
                {Object.entries(SOURCE_TYPE_META).map(([type, meta]) => (
                  <button
                    type="button"
                    key={type}
                    onClick={() => setForm((value) => ({ ...value, type }))}
                    className="group flex cursor-pointer items-start gap-3 rounded-card border border-line bg-surface p-3 text-left transition hover:border-primary/40 hover:bg-primary-soft/40 hover:shadow-card-hover"
                  >
                    <SourceTypeMark type={type} className="h-11 w-11 rounded-card" />
                    <span className="min-w-0 flex-1">
                      <strong className="block text-body font-semibold text-ink">{meta.label}</strong>
                      <code className="block font-mono text-[10px] text-faint">{type}</code>
                      <small className="mt-1 block text-[11px] leading-relaxed text-muted">{meta.description}</small>
                    </span>
                    <Icon name="chevron" size={14} className="mt-1 shrink-0 text-faint transition group-hover:translate-x-0.5 group-hover:text-primary" />
                  </button>
                ))}
                {Object.entries(UPCOMING_TYPE_META).map(([type, meta]) => (
                  <div
                    key={type}
                    aria-disabled="true"
                    className="flex cursor-not-allowed items-start gap-3 rounded-card border border-dashed border-line bg-surface-2 p-3 opacity-70"
                  >
                    <SourceTypeMark type={type} className="h-11 w-11 rounded-card" />
                    <span className="min-w-0 flex-1">
                      <span className="flex items-center gap-2">
                        <strong className="block text-body font-semibold text-muted">{meta.label}</strong>
                        <span className="rounded-tag border border-line bg-surface px-1.5 py-px text-[10px] font-semibold text-faint">待支持</span>
                      </span>
                      <code className="block font-mono text-[10px] text-faint">{type}</code>
                      <small className="mt-1 block text-[11px] leading-relaxed text-faint">{meta.description}</small>
                    </span>
                  </div>
                ))}
              </div>
              <p className="mt-3 flex items-center gap-1.5 text-[11px] text-faint">
                <Icon name="lock" size={13} />仅使用只读账号接入，SQL 经平台安全网关校验后才允许执行
              </p>
            </>
          ) : (
            <>
              <div className="flex items-start gap-3">
                {editingId == null && (
                  <Button size="sm" variant="ghost" aria-label="返回选择类型" onClick={() => setForm((value) => ({ ...EMPTY_FORM, type: "" }))}>
                    <Icon name="chevron" size={14} className="rotate-180" />
                  </Button>
                )}
                <SourceTypeMark type={form.type} className="h-10 w-10 rounded-card" />
                <div className="min-w-0 flex-1">
                  <span className="text-[11px] font-semibold tracking-wide text-faint">
                    {editingId == null ? "新建数据连接 · 第 2 步" : "数据源配置管理"}
                  </span>
                  <h3 className="mt-0.5 text-base font-semibold tracking-tight text-ink">
                    {editingId == null ? `配置 ${SOURCE_TYPE_META[form.type].label} 连接` : `管理「${form.name}」`}
                  </h3>
                  <p className="mt-0.5 text-xs text-muted">
                    {editingId == null ? "连接成功后自动扫描表、字段、主外键和少量样例值。" : "修改连接参数后请重新测试连接；密码留空表示不修改。"}
                  </p>
                </div>
                <Button size="sm" variant="ghost" aria-label="关闭接入面板" onClick={() => setConnectorOpen(false)}>
                  <Icon name="plus" size={14} className="rotate-45" />
                </Button>
              </div>

              <div className="mt-4 grid gap-3">
                <div className="grid gap-3 rounded-card border border-line bg-surface-2 p-3.5">
                  <span className="text-[11px] font-semibold tracking-wide text-faint">基本信息</span>
                  <label className="grid gap-1.5 text-[11px] font-semibold text-muted">
                    数据源名称
                    <input value={form.name} onChange={updateForm("name")} placeholder="如 华南工厂数据底座" className={inputClass} />
                  </label>
                </div>
                <div className="grid gap-3 rounded-card border border-line bg-surface-2 p-3.5">
                  <span className="text-[11px] font-semibold tracking-wide text-faint">连接配置</span>
                  <div className="grid gap-3 sm:grid-cols-2">
                    <label className="grid gap-1.5 text-[11px] font-semibold text-muted">
                      连接地址
                      <input value={form.host} onChange={updateForm("host")} placeholder="127.0.0.1" className={cn(inputClass, "font-mono")} />
                    </label>
                    <label className="grid gap-1.5 text-[11px] font-semibold text-muted">
                      端口
                      <input value={form.port} onChange={updateForm("port")} placeholder={form.type === "postgresql" ? "5432" : "3306"} className={cn(inputClass, "font-mono")} />
                    </label>
                    <label className="grid gap-1.5 text-[11px] font-semibold text-muted">
                      数据库名
                      <input value={form.database} onChange={updateForm("database")} placeholder="manufacturing" className={cn(inputClass, "font-mono")} />
                    </label>
                    <label className="grid gap-1.5 text-[11px] font-semibold text-muted">
                      只读账号
                      <input value={form.username} onChange={updateForm("username")} placeholder="forgesight_reader" className={cn(inputClass, "font-mono")} />
                    </label>
                    <label className="grid gap-1.5 text-[11px] font-semibold text-muted sm:col-span-2">
                      密码
                      <input type="password" value={form.password} onChange={updateForm("password")} placeholder={editingId != null ? "留空表示不修改" : ""} className={cn(inputClass, "font-mono")} />
                      <small className="text-[10px] font-normal text-faint">密码仅保存在平台服务端，接口永不回传明文。</small>
                    </label>
                  </div>
                </div>
              </div>

              <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-line pt-4">
                <span className="mr-auto flex items-center gap-1.5 text-[11px] text-faint">
                  <Icon name="lock" size={13} />仅使用只读账号，密码仅保存在平台服务端
                </span>
                {editingId == null ? (
                  <>
                    <Button onClick={() => setForm((value) => ({ ...EMPTY_FORM, type: "" }))}>上一步</Button>
                    <Button onClick={() => setConnectorOpen(false)}>取消</Button>
                    <Button variant="primary" onClick={saveAndTest} disabled={testing || !form.name || !form.database || !form.username}>
                      {testing ? "正在连接并扫描" : "保存并测试连接"}
                    </Button>
                  </>
                ) : (
                  <>
                    <Button onClick={runRescan} disabled={testing}>
                      <Icon name="refresh" size={14} />重新扫描
                    </Button>
                    <Button onClick={runTestExisting} disabled={testing}>测试连接</Button>
                    <Button onClick={() => setConnectorOpen(false)}>取消</Button>
                    <Button variant="primary" onClick={saveEdit} disabled={testing || !form.name || !form.database || !form.username}>
                      {testing ? "正在保存" : "保存修改"}
                    </Button>
                  </>
                )}
                {editNotice && <StatusPill tone="green">{editNotice}</StatusPill>}
                {testResult && (
                  <StatusPill tone={testResult.ok ? "green" : "red"}>{testResult.ok ? "连接成功" : testResult.message}</StatusPill>
                )}
                {integrated && <StatusPill tone="green">{integrated} 已接入资源目录</StatusPill>}
              </div>
              {testResult?.ok && editingId == null && (
                <div className="mt-4 grid gap-3 rounded-card border border-line bg-surface-2 p-3.5">
                  {unregistered.length === 0 ? (
                    <span className="text-xs text-muted">结构扫描完成，未发现未登记数据表。</span>
                  ) : (
                    unregistered.map((table) => (
                      <div key={table.name} className="flex flex-wrap items-center gap-2 text-xs text-ink-2">
                        <Icon name="database" size={15} />
                        <span>结构扫描发现未登记数据表：<code className="font-mono">{table.name}</code>（{table.rows} 行）</span>
                        <Button size="sm" variant="primary" className="ml-auto" onClick={() => runSuggest(table.name)} disabled={suggesting}>
                          <Icon name="scan" size={14} />{suggesting ? "正在分析" : "AI 辅助理解"}
                        </Button>
                      </div>
                    ))
                  )}
                  {suggestion && (
                    <div className="rounded-card border border-line bg-surface p-3.5">
                      <div className="flex flex-wrap items-center gap-2">
                        <strong className="text-body font-semibold text-ink">{suggestion.cn}</strong>
                        <StatusPill tone="blue">{suggestion.topic}主题</StatusPill>
                        <span className="ml-auto font-mono text-[11px] text-faint">置信度 {(suggestion.confidence * 100).toFixed(0)}%</span>
                      </div>
                      <p className="mt-1.5 text-xs leading-relaxed text-muted">{suggestion.reason}</p>
                      <div className="mt-3 grid gap-1">
                        {suggestion.fieldSuggestions.map((field) => (
                          <div key={field.name} className="grid grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)_minmax(0,1.3fr)] items-center gap-3 border-t border-line py-2 text-xs first:border-t-0">
                            <code className="truncate font-mono text-[11px] text-ink-2">{field.name}</code>
                            <span className="truncate font-medium text-ink">{field.cn}</span>
                            <span className="truncate text-[11px] text-faint">{field.note}</span>
                          </div>
                        ))}
                      </div>
                      <div className="mt-3 flex items-center gap-2 border-t border-line pt-3">
                        <span className="text-[11px] text-faint">以上为 AI 推荐结果，确认后才会进入资源目录并对问析可用</span>
                        <Button size="sm" className="ml-auto" onClick={() => setSuggestion(null)}>暂不接入</Button>
                        <Button size="sm" variant="primary" onClick={confirmIntegration} disabled={confirming}>
                          {confirming ? "正在接入" : "确认接入"}
                        </Button>
                      </div>
                    </div>
                  )}
                </div>
              )}
            </>
          )}
        </div>
      </Modal>

      <Card>
        <SectionHeader eyebrow="业务数据关系" title="生产—质量—设备关联图" action="展开关系图" />
        <div className="px-5 pb-5">
          <SchemaGraph selected={selectedTable?.name} onSelect={(name) => { setSelectedName(name); setPreviewOpen(false); }} />
        </div>
      </Card>

      <div className="grid items-start gap-3.5 xl:grid-cols-[minmax(320px,0.85fr)_minmax(0,1.5fr)]">
        <Card>
          <div className="flex items-center gap-2 border-b border-line px-4 py-3">
            <label className="relative block flex-1">
              <span className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-faint"><Icon name="search" size={15} /></span>
              <input
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="搜索数据表或业务含义"
                className="h-9 w-full rounded-control border border-line bg-surface pl-8 pr-2.5 text-xs text-ink outline-none transition placeholder:text-faint focus:border-primary/50 focus:ring-2 focus:ring-primary/25"
              />
            </label>
            <Button size="sm" aria-label="筛选数据表"><Icon name="filter" size={14} /></Button>
          </div>
          <div className="grid gap-1 p-3">
            {visibleTables.map((table) => (
              <button
                type="button"
                key={table.name}
                onClick={() => { setSelectedName(table.name); setPreviewOpen(false); }}
                className={cn(
                  "grid w-full cursor-pointer grid-cols-[32px_minmax(0,1fr)_auto] items-center gap-2.5 rounded-control px-2 py-2 text-left transition-colors",
                  selectedTable?.name === table.name ? "bg-surface-2 ring-1 ring-inset ring-line" : "hover:bg-surface-2",
                )}
              >
                <span className="grid h-8 w-8 place-items-center rounded-control border border-line bg-surface text-muted"><Icon name="database" size={16} /></span>
                <span className="min-w-0">
                  <strong className="block truncate text-body font-semibold text-ink">{table.cn}</strong>
                  <small className="block truncate font-mono text-[10px] text-faint">{table.name}</small>
                </span>
                <span className="text-right">
                  <b className="block font-mono text-xs font-semibold text-ink">{table.quality}</b>
                  <small className="text-[10px] text-faint">质量分</small>
                </span>
              </button>
            ))}
            {!visibleTables.length && <EmptyState title="没有匹配的数据表" description="调整搜索关键词或切换数据源后重试。" />}
          </div>
        </Card>

        {selectedTable && (
          <Card>
            <div className="flex items-start justify-between gap-4 px-5 pb-3.5 pt-4.5">
              <div className="min-w-0">
                <span className="text-[11px] font-semibold tracking-wide text-faint">{selectedTable.topic}主题 · {selectedTable.rows} 行</span>
                <h3 className="mt-1 truncate text-base font-semibold tracking-tight text-ink">{selectedTable.cn}</h3>
                <code className="font-mono text-[11px] text-muted">{selectedTable.name}</code>
              </div>
              <Button
                size="sm"
                className={cn(previewOpen && "border-line-strong bg-surface-2 text-ink")}
                onClick={() => setPreviewOpen((value) => !value)}
              >
                {previewOpen ? "查看字段" : "预览数据"}
              </Button>
            </div>
            <p className="px-5 text-xs leading-relaxed text-muted">该数据表已完成结构扫描和业务含义映射，可用于自然语言查询、指标计算和建模数据准备。</p>
            <div className="mt-3 flex flex-wrap items-center gap-5 border-y border-line bg-surface-2 px-5 py-2.5 text-xs text-muted">
              <span><b className="mr-1 font-mono font-semibold text-ink">{selectedTable.fields}</b>字段</span>
              <span><b className="mr-1 font-mono font-semibold text-ink">{selectedTable.key}</b>主键</span>
              <span><b className="mr-1 font-mono font-semibold text-ink">{selectedTable.freshness}</b>更新</span>
              <span><b className="mr-1 font-mono font-semibold text-ink">{selectedTable.quality}</b>质量分</span>
            </div>
            {!previewOpen ? <FieldTable tableName={selectedTable.name} sourceId={currentSourceId} /> : <DataPreview table={selectedTable} sourceId={currentSourceId} />}
          </Card>
        )}
      </div>
    </PageFrame>
  );
}

function FieldTable({ tableName, sourceId }) {
  const [fields, setFields] = useState(null);

  useEffect(() => {
    let active = true;
    setFields(null);
    getTableFields(tableName, sourceId)
      .then((data) => { if (active) setFields(data); })
      .catch(() => { if (active) setFields([]); });
    return () => { active = false; };
  }, [tableName, sourceId]);

  if (!fields) {
    return (
      <div className="grid animate-pulse gap-2 p-5">
        {[1, 2, 3, 4].map((item) => <i key={item} className="h-8 rounded-control bg-surface-3" />)}
      </div>
    );
  }
  if (!fields.length) {
    return <div className="p-5"><EmptyState title="该表尚未登记字段信息" description="接入扫描完成后字段会显示在这里。" /></div>;
  }
  return (
    <div className="px-5 pb-5">
      <div className="grid grid-cols-[minmax(0,1.4fr)_0.8fr_1fr_1fr] gap-3 py-2.5 text-[11px] font-semibold text-muted">
        <span>字段</span><span>类型</span><span>规则 / 关系</span><span>样例值</span>
      </div>
      {fields.map((field) => (
        <div key={field.name} className="grid grid-cols-[minmax(0,1.4fr)_0.8fr_1fr_1fr] items-center gap-3 border-t border-line py-2.5 text-xs text-ink-2">
          <span className="min-w-0">
            <b className="block truncate font-semibold text-ink">{field.cn}</b>
            <code className="block truncate font-mono text-[10px] text-faint">{field.name}</code>
          </span>
          <span className="font-mono text-[11px]">{field.type}</span>
          <span>{field.rule}</span>
          <span className="truncate font-mono text-[11px]">{field.sample}</span>
        </div>
      ))}
    </div>
  );
}

function DataPreview({ table, sourceId }) {
  const [preview, setPreview] = useState(null);

  useEffect(() => {
    let active = true;
    setPreview(null);
    getTablePreview(table.name, sourceId)
      .then((data) => { if (active) setPreview(data); })
      .catch(() => { if (active) setPreview({ columns: [], rows: [] }); });
    return () => { active = false; };
  }, [table.name, sourceId]);

  if (!preview) {
    return (
      <div className="grid animate-pulse gap-2 p-5">
        {[1, 2, 3].map((item) => <i key={item} className="h-8 rounded-control bg-surface-3" />)}
      </div>
    );
  }
  return (
    <div className="grid gap-3 px-5 pb-5 pt-4">
      <div className="flex items-center gap-2 rounded-control border border-teal/30 bg-teal-soft px-2.5 py-2 text-[11px] text-teal">
        <Icon name="shield" size={14} />
        <span>只读预览 · 最多返回 20 行</span>
        <code className="ml-auto font-mono text-[10px]">{table.name}</code>
      </div>
      <div className="overflow-x-auto rounded-card border border-line">
        <table className="w-full border-collapse text-left">
          <thead>
            <tr className="border-b border-line bg-surface-2">
              {preview.columns.map((column) => (
                <th key={column} className="whitespace-nowrap px-3 py-2 font-mono text-[10px] font-semibold text-muted">{column}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {preview.rows.map((row, index) => (
              <tr key={index} className="border-b border-line last:border-b-0">
                {row.map((value, cell) => (
                  <td key={`${index}-${cell}`} className="whitespace-nowrap px-3 py-2 font-mono text-[11px] text-ink-2">{value}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <footer className="text-[11px] text-faint">显示 {preview.rows.length} / 20 行 · 数据更新至 {table.freshness}</footer>
    </div>
  );
}

const nodeTones = {
  blue: "text-primary",
  teal: "text-teal",
  amber: "text-amber",
  violet: "text-violet",
};

function SchemaGraph({ selected, onSelect }) {
  const nodes = [
    { id: "mes_work_order", label: "生产工单", x: 18, y: 82, tone: "blue" },
    { id: "mes_process_output", label: "工序产量", x: 246, y: 26, tone: "blue" },
    { id: "qms_inspection", label: "质量检验", x: 246, y: 138, tone: "teal" },
    { id: "eqp_downtime_record", label: "设备停机", x: 486, y: 26, tone: "amber" },
    { id: "qms_defect_detail", label: "缺陷明细", x: 486, y: 138, tone: "teal" },
    { id: "inv_inventory_snapshot", label: "库存快照", x: 724, y: 82, tone: "violet" },
  ];
  const links = [
    ["mes_work_order", "mes_process_output"],
    ["mes_work_order", "qms_inspection"],
    ["mes_process_output", "eqp_downtime_record"],
    ["qms_inspection", "qms_defect_detail"],
    ["eqp_downtime_record", "inv_inventory_snapshot"],
    ["qms_defect_detail", "inv_inventory_snapshot"],
  ];
  const nodeMap = Object.fromEntries(nodes.map((node) => [node.id, node]));
  return (
    <div className="overflow-hidden rounded-card border border-line bg-surface-2">
      <div
        data-testid="schema-graph-canvas"
        className="relative mx-auto w-full max-w-[920px]"
        style={{ aspectRatio: `${SCHEMA_GRAPH_WIDTH} / ${SCHEMA_GRAPH_HEIGHT}` }}
      >
        <svg
          viewBox={`0 0 ${SCHEMA_GRAPH_WIDTH} ${SCHEMA_GRAPH_HEIGHT}`}
          preserveAspectRatio="none"
          className="absolute inset-0 h-full w-full fill-none stroke-line-strong"
          aria-hidden="true"
        >
          {links.map(([fromId, toId]) => (
            <path key={`${fromId}-${toId}`} d={buildSchemaLinkPath(nodeMap[fromId], nodeMap[toId])} />
          ))}
        </svg>
        {nodes.map((node) => (
          <button
            type="button"
            key={node.id}
            onClick={() => onSelect(node.id)}
            style={getSchemaNodeStyle(node)}
            className={cn(
              "absolute flex cursor-pointer items-center gap-2 rounded-control border bg-surface px-[1.05%] py-[0.82%] text-left transition-shadow",
              selected === node.id ? "border-ink/20 shadow-card-hover ring-1 ring-inset ring-line" : "border-line shadow-card hover:shadow-card-hover",
            )}
          >
            <span className={cn("shrink-0", nodeTones[node.tone] ?? nodeTones.blue)}><Icon name="database" size={14} /></span>
            <span className="min-w-0">
              <strong className="block truncate text-xs font-semibold text-ink">{node.label}</strong>
              <small className="block truncate font-mono text-[9px] text-faint">{node.id}</small>
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}
