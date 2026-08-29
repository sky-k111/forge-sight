import { useCallback, useEffect, useMemo, useState } from "react";
import Icon from "../../components/ui/Icon";
import cn from "../../lib/cn";
import { Button, Card, EmptyState, ErrorNotice, Modal, PageFrame, RouteSkeleton, SectionHeader, StatusPill } from "../../components/ui";
import { confirmTableSemantics, getTableFields, getTablePreview, listRelations, listTables, listUnregisteredTables, suggestTableSemantics } from "../../api/resources";
import { createSource, getSemanticIndexStatus, listSources, listSourceTypes, rebuildSemanticIndex, retrySemanticIndexJob, scanSource, testSource, updateSource } from "../../api/datasources";
import { generateMetricCandidates } from "../../api/knowledge";
import { buildSchemaLinkPath, getSchemaNodeStyle, SCHEMA_GRAPH_HEIGHT, SCHEMA_GRAPH_WIDTH } from "./schemaGraphLayout";
import SourceTypeMark from "../../components/ui/SourceTypeMark";

const EMPTY_FORM = {
  name: "", type: "", host: "127.0.0.1", port: "", database: "", username: "", password: "",
  schemas: "", config: {},
};

// 后端类型目录不可用时的可恢复降级值；正常情况下能力与成熟度由 Adapter Registry 返回。
const FALLBACK_SOURCE_TYPES = [
  { type: "mysql", label: "MySQL", default_port: 3306, maturity: "stable" },
  { type: "postgresql", label: "PostgreSQL", default_port: 5432, maturity: "stable" },
  { type: "mariadb", label: "MariaDB", default_port: 3306, maturity: "stable" },
  { type: "sqlserver", label: "SQL Server", default_port: 1433, maturity: "beta" },
  { type: "oracle", label: "Oracle", default_port: 1521, maturity: "beta" },
];

const TYPE_DESCRIPTIONS = {
  mysql: "制造业 ERP、MES 与业务系统常见数据库。",
  postgresql: "支持多 Schema、注释与物化视图的分析型关系数据库。",
  mariadb: "兼容 MySQL 生态的开源关系数据库。",
  sqlserver: "Microsoft 企业数据库；需安装 ODBC Driver 18。",
  oracle: "大型企业核心业务数据库；使用 Service Name 连接。",
};

const inputClass = "h-9 w-full rounded-control border border-line bg-surface px-2.5 text-xs font-normal text-ink outline-none transition placeholder:text-faint focus:border-primary/50 focus:ring-2 focus:ring-primary/25";
const tableIdentity = (table) => table?.qualifiedName ?? (table?.schemaName ? `${table.schemaName}.${table.name}` : table?.name ?? "");

export default function ResourcesPage() {
  const [tables, setTables] = useState(null);
  const [relations, setRelations] = useState([]);
  const [error, setError] = useState(null);
  const [sources, setSources] = useState([]);
  const [sourceTypes, setSourceTypes] = useState(FALLBACK_SOURCE_TYPES);
  const [currentSourceId, setCurrentSourceId] = useState(null);
  const [selectedName, setSelectedName] = useState("");
  const [previewOpen, setPreviewOpen] = useState(false);
  const [connectorOpen, setConnectorOpen] = useState(false);
  const [sourceMenuOpen, setSourceMenuOpen] = useState(false);
  const [form, setForm] = useState(EMPTY_FORM);
  const [editingId, setEditingId] = useState(null);
  const [editNotice, setEditNotice] = useState("");
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState(null);
  const [awaitingSchemaSelection, setAwaitingSchemaSelection] = useState(false);
  const [pendingSourceId, setPendingSourceId] = useState(null);
  const [unregistered, setUnregistered] = useState([]);
  const [search, setSearch] = useState("");
  const [tableFilterOpen, setTableFilterOpen] = useState(false);
  const [tableTopicFilter, setTableTopicFilter] = useState("all");
  const [suggestion, setSuggestion] = useState(null);
  const [suggesting, setSuggesting] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [integrated, setIntegrated] = useState("");
  const [semanticIndex, setSemanticIndex] = useState(null);
  const [indexAction, setIndexAction] = useState(false);

  const load = useCallback(() => {
    setError(null);
    setTables(null);
    Promise.all([
      listTables(currentSourceId ? { sourceId: currentSourceId } : {}),
      listRelations(currentSourceId),
    ])
      .then(([nextTables, nextRelations]) => {
        setTables(nextTables);
        setRelations(nextRelations);
      })
      .catch(setError);
  }, [currentSourceId]);

  useEffect(load, [load]);
  useEffect(() => {
    listSources().then(setSources).catch(() => setSources([]));
    listSourceTypes().then((items) => {
      if (Array.isArray(items) && items.length) setSourceTypes(items);
    }).catch(() => setSourceTypes(FALLBACK_SOURCE_TYPES));
  }, []);

  const sourceTypeMeta = useMemo(
    () => Object.fromEntries(sourceTypes.map((item) => [item.type, item])),
    [sourceTypes],
  );

  const indexSourceId = currentSourceId ?? sources.find((source) => source.isDefault)?.id ?? sources[0]?.id;
  useEffect(() => {
    if (!indexSourceId) return undefined;
    let active = true;
    const refresh = async () => {
      try {
        const next = await getSemanticIndexStatus(indexSourceId);
        if (!active) return;
        setSemanticIndex(next);
      } catch {
        if (active) setSemanticIndex(null);
      }
    };
    refresh();
    const timer = window.setInterval(refresh, 2500);
    return () => { active = false; window.clearInterval(timer); };
  }, [indexSourceId]);

  const refreshSemanticIndex = async () => {
    if (indexSourceId) setSemanticIndex(await getSemanticIndexStatus(indexSourceId));
  };

  const queueFullIndex = async () => {
    if (!indexSourceId) return;
    setIndexAction(true);
    try {
      await rebuildSemanticIndex(indexSourceId);
      await refreshSemanticIndex();
    } finally {
      setIndexAction(false);
    }
  };

  const retryIndex = async () => {
    const jobId = semanticIndex?.latestJob?.id;
    if (!jobId) return;
    setIndexAction(true);
    try {
      await retrySemanticIndexJob(jobId);
      await refreshSemanticIndex();
    } finally {
      setIndexAction(false);
    }
  };

  const visibleTables = useMemo(
    () => (tables ?? []).filter((table) => {
      const matchesSearch = `${table.cn}${table.name}${table.topic}`.includes(search.trim());
      return matchesSearch && (tableTopicFilter === "all" || table.topic === tableTopicFilter);
    }),
    [search, tables, tableTopicFilter],
  );
  const selectedTable = tables?.find((table) => tableIdentity(table) === selectedName) ?? tables?.[0];
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
    setAwaitingSchemaSelection(false);
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
      schemas: (source.schemas ?? []).join(", "),
      config: source.config ?? {},
    });
    setEditingId(source.id);
    setEditNotice("");
    setTestResult(null);
    setAwaitingSchemaSelection(false);
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
        schemas: form.schemas.split(",").map((item) => item.trim()).filter(Boolean),
        config: form.config,
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
      const created = await createSource({
        ...form,
        port: form.port ? Number(form.port) : undefined,
        schemas: form.schemas.split(",").map((item) => item.trim()).filter(Boolean),
      });
      setPendingSourceId(created.id);
      const result = await testSource(created.id);
      setTestResult(result);
      if (result.ok) {
        const requested = form.schemas.split(",").map((item) => item.trim()).filter(Boolean);
        if (requested.length || !(result.schemas?.length)) {
          await scanSource(created.id);
          setUnregistered(await listUnregisteredTables(created.id));
          setSources(await listSources());
        } else {
          setAwaitingSchemaSelection(true);
          setForm((value) => ({ ...value, schemas: (result.schemas ?? []).slice(0, 1).join(", ") }));
        }
      }
    } catch (err) {
      setTestResult({ ok: false, message: err.message });
    } finally {
      setTesting(false);
    }
  };

  const toggleDiscoveredSchema = (schema) => {
    const selected = new Set(form.schemas.split(",").map((item) => item.trim()).filter(Boolean));
    if (selected.has(schema)) selected.delete(schema);
    else selected.add(schema);
    setForm((value) => ({ ...value, schemas: [...selected].join(", ") }));
  };

  const confirmSchemasAndScan = async () => {
    const selected = form.schemas.split(",").map((item) => item.trim()).filter(Boolean);
    if (!pendingSourceId || !selected.length) return;
    setTesting(true);
    try {
      await updateSource(pendingSourceId, { schemas: selected });
      await scanSource(pendingSourceId);
      setUnregistered(await listUnregisteredTables(pendingSourceId));
      setSources(await listSources());
      setAwaitingSchemaSelection(false);
    } catch (err) {
      setTestResult((value) => ({ ...value, ok: false, message: err.message }));
    } finally {
      setTesting(false);
    }
  };

  const runSuggest = async (table) => {
    setSuggesting(true);
    try {
      setSuggestion(await suggestTableSemantics(table.name, pendingSourceId, table.schemaName));
    } finally {
      setSuggesting(false);
    }
  };

  const confirmIntegration = async () => {
    setConfirming(true);
    try {
      const created = await confirmTableSemantics(
        suggestion.name,
        pendingSourceId,
        suggestion.schemaName,
        suggestion,
      );
      const remaining = unregistered.filter((table) => tableIdentity(table) !== tableIdentity(created));
      let notice = created.qualifiedName ?? created.name;
      if (remaining.length === 0) {
        try {
          const candidateResult = await generateMetricCandidates(pendingSourceId);
          notice = `${created.qualifiedName ?? created.name}；已生成 ${candidateResult.generated ?? 0} 项口径候选`;
        } catch {
          notice = `${created.qualifiedName ?? created.name}；口径候选可在业务知识库中重试生成`;
        }
      }
      setIntegrated(notice);
      setSuggestion(null);
      setUnregistered(remaining);
      switchSource(pendingSourceId);
      setSelectedName(tableIdentity(created));
      await refreshSemanticIndex();
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
        <div className="ml-auto flex items-center gap-2 rounded-control border border-line bg-surface-2 px-2.5 py-1.5">
          <span className={cn("h-1.5 w-1.5 rounded-full", semanticIndex?.status === "failed" ? "bg-red" : semanticIndex?.status === "succeeded" ? "bg-green" : "bg-amber")} />
          <span className="text-[11px] font-semibold text-ink-2">{semanticIndex?.statusLabel ?? "正在读取索引状态"}</span>
          {semanticIndex?.documents > 0 && <span className="font-mono text-[10px] text-faint">{semanticIndex.documents} 项语义资产</span>}
          {semanticIndex?.status === "failed" ? (
            <Button size="sm" variant="ghost" onClick={retryIndex} disabled={indexAction}>重试</Button>
          ) : (
            <Button size="sm" variant="ghost" onClick={queueFullIndex} disabled={indexAction} title="重新生成当前数据源的全部语义向量">
              <Icon name="refresh" size={13} />{indexAction ? "已入队" : "重建"}
            </Button>
          )}
        </div>
        <Button variant="primary" onClick={openConnector}>
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
                {sourceTypes.map((meta) => (
                  <button
                    type="button"
                    key={meta.type}
                    onClick={() => setForm((value) => ({
                      ...value,
                      type: meta.type,
                      port: String(meta.default_port ?? ""),
                      config: meta.type === "sqlserver"
                        ? { driver: "ODBC Driver 18 for SQL Server", encrypt: true, trustServerCertificate: false }
                        : meta.type === "oracle" ? { serviceName: "" } : {},
                    }))}
                    className="group flex cursor-pointer items-start gap-3 rounded-card border border-line bg-surface p-3 text-left transition hover:border-primary/40 hover:bg-primary-soft/40 hover:shadow-card-hover"
                  >
                    <SourceTypeMark type={meta.type} className="h-11 w-11 rounded-card" />
                    <span className="min-w-0 flex-1">
                      <strong className="block text-body font-semibold text-ink">{meta.label}</strong>
                      <code className="block font-mono text-[10px] text-faint">{meta.type}</code>
                      <small className="mt-1 block text-[11px] leading-relaxed text-muted">{TYPE_DESCRIPTIONS[meta.type]}</small>
                      <span className={cn("mt-2 inline-flex rounded-tag border px-1.5 py-px text-[9px] font-semibold", meta.maturity === "stable" ? "border-green/20 bg-green-soft text-green" : "border-amber/25 bg-amber-soft text-amber")}>{meta.maturity === "stable" ? "稳定适配" : "Beta 适配"}</span>
                    </span>
                    <Icon name="chevron" size={14} className="mt-1 shrink-0 text-faint transition group-hover:translate-x-0.5 group-hover:text-primary" />
                  </button>
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
                  <Button size="sm" variant="ghost" aria-label="返回选择类型" onClick={() => setForm(() => ({ ...EMPTY_FORM, type: "" }))}>
                    <Icon name="chevron" size={14} className="rotate-180" />
                  </Button>
                )}
                <SourceTypeMark type={form.type} className="h-10 w-10 rounded-card" />
                <div className="min-w-0 flex-1">
                  <span className="text-[11px] font-semibold tracking-wide text-faint">
                    {editingId == null ? "新建数据连接 · 第 2 步" : "数据源配置管理"}
                  </span>
                  <h3 className="mt-0.5 text-base font-semibold tracking-tight text-ink">
                    {editingId == null ? `配置 ${sourceTypeMeta[form.type]?.label ?? form.type} 连接` : `管理「${form.name}」`}
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
                      <input value={form.port} onChange={updateForm("port")} placeholder={String(sourceTypeMeta[form.type]?.default_port ?? "")} className={cn(inputClass, "font-mono")} />
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
                  <label className="grid gap-1.5 text-[11px] font-semibold text-muted">
                    Schema 范围 <span className="font-normal text-faint">（可选，多个用逗号分隔）</span>
                    <input value={form.schemas} onChange={updateForm("schemas")} placeholder={form.type === "postgresql" ? "public, manufacturing" : form.type === "sqlserver" ? "dbo" : "留空自动发现"} className={cn(inputClass, "font-mono")} />
                    <small className="text-[10px] font-normal text-faint">留空时连接成功后自动发现非系统 Schema；扫描和白名单均保留 schema.table 身份。</small>
                  </label>
                  {form.type === "sqlserver" && (
                    <div className="grid gap-3 sm:grid-cols-2">
                      <label className="grid gap-1.5 text-[11px] font-semibold text-muted sm:col-span-2">ODBC 驱动
                        <input value={form.config.driver ?? ""} onChange={(event) => setForm((value) => ({ ...value, config: { ...value.config, driver: event.target.value } }))} className={cn(inputClass, "font-mono")} />
                      </label>
                      <label className="flex items-center gap-2 text-[11px] font-semibold text-muted"><input type="checkbox" checked={form.config.encrypt ?? true} onChange={(event) => setForm((value) => ({ ...value, config: { ...value.config, encrypt: event.target.checked } }))} />加密连接</label>
                      <label className="flex items-center gap-2 text-[11px] font-semibold text-muted"><input type="checkbox" checked={form.config.trustServerCertificate ?? false} onChange={(event) => setForm((value) => ({ ...value, config: { ...value.config, trustServerCertificate: event.target.checked } }))} />信任服务器证书</label>
                    </div>
                  )}
                  {form.type === "oracle" && (
                    <label className="grid gap-1.5 text-[11px] font-semibold text-muted">Service Name
                      <input value={form.config.serviceName ?? ""} onChange={(event) => setForm((value) => ({ ...value, config: { ...value.config, serviceName: event.target.value } }))} placeholder={form.database || "ORCLPDB1"} className={cn(inputClass, "font-mono")} />
                    </label>
                  )}
                </div>
              </div>

              <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-line pt-4">
                <span className="mr-auto flex items-center gap-1.5 text-[11px] text-faint">
                  <Icon name="lock" size={13} />仅使用只读账号，密码仅保存在平台服务端
                </span>
                {editingId == null ? (
                  <>
                    <Button onClick={() => setForm(() => ({ ...EMPTY_FORM, type: "" }))}>上一步</Button>
                    <Button onClick={() => setConnectorOpen(false)}>取消</Button>
                    <Button variant="primary" onClick={saveAndTest} disabled={testing || !form.name || !form.database || !form.username}>
                      {testing ? "正在测试连接" : "保存并测试连接"}
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
                  <StatusPill tone={testResult.ok ? "green" : "red"}>{testResult.ok ? `连接成功${testResult.schemas?.length ? ` · ${testResult.schemas.length} 个 Schema` : ""}` : testResult.message}</StatusPill>
                )}
                {integrated && <StatusPill tone="green">{integrated} 已接入资源目录</StatusPill>}
              </div>
              {testResult?.ok && editingId == null && awaitingSchemaSelection && (
                <div className="mt-4 rounded-card border border-primary/20 bg-primary-soft/30 p-3.5">
                  <div className="flex items-start gap-2.5">
                    <span className="grid h-8 w-8 shrink-0 place-items-center rounded-control bg-surface text-primary"><Icon name="database" size={15} /></span>
                    <div className="min-w-0 flex-1">
                      <strong className="block text-xs font-semibold text-ink">选择允许分析的 Schema</strong>
                      <p className="mt-1 text-[11px] leading-relaxed text-muted">仅所选范围会被扫描、建立语义索引并进入 SQL 白名单。后续可在数据源配置中修改。</p>
                    </div>
                  </div>
                  <div className="mt-3 flex flex-wrap gap-2">
                    {(testResult.schemas ?? []).map((schema) => {
                      const active = form.schemas.split(",").map((item) => item.trim()).includes(schema);
                      return (
                        <button
                          type="button"
                          key={schema}
                          aria-pressed={active}
                          onClick={() => toggleDiscoveredSchema(schema)}
                          className={cn(
                            "flex cursor-pointer items-center gap-2 rounded-control border px-2.5 py-2 font-mono text-[11px] transition",
                            active ? "border-primary/35 bg-surface text-primary ring-1 ring-primary/15" : "border-line bg-surface text-muted hover:border-line-strong",
                          )}
                        >
                          <span className={cn("grid h-4 w-4 place-items-center rounded-[4px] border", active ? "border-primary bg-primary text-white" : "border-line-strong")}>
                            {active && <Icon name="check" size={11} />}
                          </span>
                          {schema}
                        </button>
                      );
                    })}
                  </div>
                  <div className="mt-3 flex items-center justify-end border-t border-primary/15 pt-3">
                    <Button variant="primary" size="sm" disabled={testing || !form.schemas.trim()} onClick={confirmSchemasAndScan}>
                      {testing ? "正在扫描" : "确认范围并扫描"}
                    </Button>
                  </div>
                </div>
              )}
              {testResult?.ok && editingId == null && !awaitingSchemaSelection && (
                <div className="mt-4 grid gap-3 rounded-card border border-line bg-surface-2 p-3.5">
                  {unregistered.length === 0 ? (
                    <span className="text-xs text-muted">结构扫描完成，未发现未登记数据表。</span>
                  ) : (
                    unregistered.map((table) => (
                      <div key={tableIdentity(table)} className="flex flex-wrap items-center gap-2 text-xs text-ink-2">
                        <Icon name="database" size={15} />
                        <span>结构扫描发现未登记数据表：<code className="font-mono">{tableIdentity(table)}</code>（{table.rows} 行）</span>
                        <Button size="sm" variant="primary" className="ml-auto" onClick={() => runSuggest(table)} disabled={suggesting}>
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
          <SchemaGraph tables={tables} relations={relations} selected={tableIdentity(selectedTable)} onSelect={(name) => { setSelectedName(name); setPreviewOpen(false); }} />
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
            <div className="relative">
              <Button size="sm" aria-label="筛选数据表" aria-expanded={tableFilterOpen} aria-haspopup="dialog" onClick={() => setTableFilterOpen((value) => !value)}><Icon name="filter" size={14} /></Button>
              {tableFilterOpen && (
                <div role="dialog" aria-label="筛选数据表" className="absolute right-0 top-[calc(100%+8px)] z-30 grid min-w-[220px] gap-3 rounded-card border border-line bg-surface p-3 shadow-card-hover">
                  <label className="grid gap-1 text-[11px] font-semibold text-muted">
                    业务主题
                    <select aria-label="数据表主题" value={tableTopicFilter} onChange={(event) => setTableTopicFilter(event.target.value)} className="h-9 rounded-control border border-line bg-surface-2 px-2 text-xs font-normal text-ink">
                      <option value="all">全部主题</option>
                      {[...new Set((tables ?? []).map((table) => table.topic).filter(Boolean))].map((topic) => <option key={topic} value={topic}>{topic}</option>)}
                    </select>
                  </label>
                  <Button size="sm" onClick={() => { setTableTopicFilter("all"); setTableFilterOpen(false); }}>清除筛选</Button>
                </div>
              )}
            </div>
          </div>
          <div className="grid gap-1 p-3">
            {visibleTables.map((table) => (
              <button
                type="button"
                key={tableIdentity(table)}
                onClick={() => { setSelectedName(tableIdentity(table)); setPreviewOpen(false); }}
                className={cn(
                  "grid w-full cursor-pointer grid-cols-[32px_minmax(0,1fr)_auto] items-center gap-2.5 rounded-control px-2 py-2 text-left transition-colors",
                  tableIdentity(selectedTable) === tableIdentity(table) ? "bg-surface-2 ring-1 ring-inset ring-line" : "hover:bg-surface-2",
                )}
              >
                <span className="grid h-8 w-8 place-items-center rounded-control border border-line bg-surface text-muted"><Icon name="database" size={16} /></span>
                <span className="min-w-0">
                  <strong className="block truncate text-body font-semibold text-ink">{table.cn}</strong>
                  <small className="block truncate font-mono text-[10px] text-faint">{tableIdentity(table)}</small>
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
                <code className="font-mono text-[11px] text-muted">{tableIdentity(selectedTable)}</code>
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
            {!previewOpen ? <FieldTable tableName={selectedTable.name} schemaName={selectedTable.schemaName} sourceId={currentSourceId} /> : <DataPreview table={selectedTable} sourceId={currentSourceId} />}
          </Card>
        )}
      </div>
    </PageFrame>
  );
}

function FieldTable({ tableName, schemaName, sourceId }) {
  const [fields, setFields] = useState(null);

  useEffect(() => {
    let active = true;
    setFields(null);
    getTableFields(tableName, sourceId, schemaName)
      .then((data) => { if (active) setFields(data); })
      .catch(() => { if (active) setFields([]); });
    return () => { active = false; };
  }, [tableName, schemaName, sourceId]);

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
    getTablePreview(table.name, sourceId, table.schemaName)
      .then((data) => { if (active) setPreview(data); })
      .catch(() => { if (active) setPreview({ columns: [], rows: [] }); });
    return () => { active = false; };
  }, [table.name, table.schemaName, sourceId]);

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
        <code className="ml-auto font-mono text-[10px]">{tableIdentity(table)}</code>
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

function SchemaGraph({ tables, relations, selected, onSelect }) {
  const positions = [
    [18, 26], [246, 26], [486, 26], [724, 26],
    [18, 138], [246, 138], [486, 138], [724, 138],
  ];
  const toneForTopic = (topic) => ({ 生产: "blue", 质量: "teal", 设备: "amber", 库存: "violet" }[topic] ?? "blue");
  const nodes = (tables ?? []).slice(0, positions.length).map((table, index) => ({
    id: tableIdentity(table),
    label: table.cn || table.name,
    x: positions[index][0],
    y: positions[index][1],
    tone: toneForTopic(table.topic),
  }));
  const visibleIds = new Set(nodes.map((node) => node.id));
  const relationIdentity = (schema, name) => (schema ? `${schema}.${name}` : name);
  const links = (relations ?? [])
    .map((relation) => [
      relationIdentity(relation.fromSchema, relation.fromTable),
      relationIdentity(relation.toSchema, relation.toTable),
    ])
    .filter(([fromId, toId], index, items) => (
      visibleIds.has(fromId)
      && visibleIds.has(toId)
      && items.findIndex(([left, right]) => left === fromId && right === toId) === index
    ));
  const nodeMap = Object.fromEntries(nodes.map((node) => [node.id, node]));
  if (!nodes.length) {
    return (
      <div data-testid="schema-graph-canvas" className="rounded-card border border-line bg-surface-2 p-5">
        <EmptyState title="尚无可展示的数据关系" description="完成元数据扫描并登记数据表后，关系图会自动生成。" />
      </div>
    );
  }
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
              <small title={node.id} className="block truncate font-mono text-[9px] text-faint">{node.id}</small>
            </span>
          </button>
        ))}
        {(tables?.length ?? 0) > positions.length && (
          <span className="absolute bottom-1 right-2 text-[9px] text-faint">当前展示前 {positions.length} 张表</span>
        )}
      </div>
    </div>
  );
}
