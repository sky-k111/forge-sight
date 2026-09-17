import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { listSources } from "../../api/datasources";
import { discoverRemoteTools, listRemoteCalls, listRemoteModels, remoteModelAction, saveRemoteModel } from "../../api/remoteModels";
import { Button, PageFrame, StatusPill } from "../../components/ui";
import Icon from "../../components/ui/Icon";
import cn from "../../lib/cn";

const blank = { name: "", sourceId: 0, endpoint: "", credentialRef: "", toolName: "", version: "1", taskType: "regression", description: "", datasetSql: "", recordsArgument: "records", outputKey: "predictions" };
const statuses = { draft: "待验证", validated: "接口已验证", published: "已发布", retired: "已停用" };
const statusTones = { draft: "amber", validated: "blue", published: "green", retired: "gray" };
const inputClass = "input-control mt-1.5 h-9 w-full px-3 text-xs text-ink disabled:cursor-not-allowed disabled:bg-surface-2 disabled:opacity-60";

function WorkflowStep({ index, label, caption, complete, active }) {
  return (
    <div className={cn("min-w-[150px] flex-1 border-r border-line px-4 py-3 last:border-r-0", active && "bg-primary-soft/70")}>
      <div className="flex items-center gap-2">
        <span className={cn(
          "grid h-5 w-5 shrink-0 place-items-center rounded-full border font-mono text-[10px] font-semibold",
          complete ? "border-teal/30 bg-teal-soft text-teal" : active ? "border-primary/30 bg-surface text-primary" : "border-line bg-surface text-faint",
        )}>
          {complete ? <Icon name="check" size={11} strokeWidth={2.2} /> : index}
        </span>
        <strong className="text-[11px] font-semibold text-ink">{label}</strong>
      </div>
      <p className="mt-1 pl-7 text-[10px] text-muted">{caption}</p>
    </div>
  );
}

export default function RemoteModelsPanel({ onBack, backLabel = "本地建模", embedded = false, onClose }) {
  const [params, setParams] = useSearchParams();
  const sourceId = Number(params.get("sourceId") || 0);
  const [sources, setSources] = useState([]);
  const [assets, setAssets] = useState([]);
  const [permissions, setPermissions] = useState({ canConfigure: false, canPublish: false });
  const [loadingAssets, setLoadingAssets] = useState(false);
  const [selected, setSelected] = useState(null);
  const [form, setForm] = useState(blank);
  const [mapping, setMapping] = useState('{"feature": "source_column"}');
  const [samples, setSamples] = useState("");
  const [tools, setTools] = useState([]);
  const [result, setResult] = useState(null);
  const [calls, setCalls] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const canEdit = permissions.canConfigure && (!selected || selected.canEdit !== false);
  let dirty = Boolean(selected && JSON.stringify(form) !== JSON.stringify(selected.config));
  try { dirty ||= Boolean(selected && JSON.stringify(JSON.parse(mapping)) !== JSON.stringify(selected.config.fieldMapping)); } catch { dirty = true; }

  useEffect(() => { listSources().then((data) => setSources(Array.isArray(data) ? data : data.items ?? [])).catch((e) => setError(e.message)); }, []);
  useEffect(() => {
    let active = true;
    setAssets([]); setSelected(null); setResult(null); setCalls([]); setPermissions({ canConfigure: false, canPublish: false }); setForm({ ...blank, sourceId });
    setLoadingAssets(Boolean(sourceId));
    if (sourceId) listRemoteModels(sourceId).then((data) => {
      if (active) {
        setAssets(data.items);
        setPermissions({ canConfigure: Boolean(data.canConfigure), canPublish: Boolean(data.canPublish) });
      }
    }).catch((e) => { if (active) setError(e.message); }).finally(() => { if (active) setLoadingAssets(false); });
    return () => { active = false; };
  }, [sourceId]);

  const act = async (fn) => {
    setBusy(true); setError(""); setNotice("");
    try { await fn(); } catch (e) { setError(e.message); } finally { setBusy(false); }
  };
  const reset = () => { setSelected(null); setForm({ ...blank, sourceId }); setMapping('{"feature": "source_column"}'); setSamples(""); setResult(null); setCalls([]); setTools([]); setError(""); setNotice(""); };
  const select = (asset) => {
    setSelected(asset); setForm(asset.config); setMapping(JSON.stringify(asset.config.fieldMapping, null, 2));
    setResult(null); setCalls([]); setTools([]); setSamples(""); setError(""); setNotice("");
    act(async () => setCalls((await listRemoteCalls(asset.id)).items));
  };
  const execute = (action) => act(async () => {
    try {
      const response = await remoteModelAction(selected.id, action, ["validate", "predict"].includes(action) && samples.trim() ? JSON.parse(samples) : null);
      if (action === "validate" || action === "predict") setResult(response);
      const data = await listRemoteModels(sourceId);
      setAssets(data.items); setSelected(data.items.find((item) => item.id === selected.id));
      setNotice(action === "publish" ? "已发布，智能分析可以匹配调用此模型。" : action === "retire" ? "已停用，停止业务调用。" : "调用完成。");
    } finally { setCalls((await listRemoteCalls(selected.id)).items); }
  });
  const field = (key, label, placeholder = "") => (
    <label className="text-[11px] font-medium text-muted">{label}<input className={inputClass} value={form[key]} placeholder={placeholder} onChange={(event) => setForm({ ...form, [key]: event.target.value })} /></label>
  );

  const workflowState = selected?.status ?? "draft";
  const panel = (
    <section className={cn("overflow-hidden bg-surface", !embedded && "rounded-panel border border-line")}>
      <header className="sticky top-0 z-10 flex min-h-14 flex-wrap items-center gap-3 border-b border-line bg-surface/95 px-5 py-3 backdrop-blur">
        {!embedded && <Button size="sm" onClick={onBack}>{backLabel}</Button>}
        <span className="grid h-9 w-9 shrink-0 place-items-center rounded-control bg-ink text-white shadow-sm"><Icon name="model" size={16} /></span>
        <div className="min-w-0">
          <h2 className="text-sm font-semibold tracking-tight text-ink">{embedded ? "接入 MCP 远程模型" : "MCP 远程模型资产"}</h2>
          <p className="mt-0.5 text-[11px] text-muted">模型文件留在企业内，只通过受控接口完成预测。</p>
        </div>
        <label className="ml-auto flex items-center gap-2 text-[10px] font-medium text-muted">
          当前数据源
          <select aria-label="远程模型数据源" className="input-control h-8 min-w-[190px] px-3 text-xs font-semibold text-ink" value={sourceId} disabled={busy} onChange={(event) => { const next = new URLSearchParams(params); next.set("sourceId", event.target.value); setParams(next); }}>
            <option value={0}>选择数据源</option>{sources.map((source) => <option key={source.id} value={source.id}>{source.name}</option>)}
          </select>
        </label>
        {embedded && <Button size="sm" variant="ghost" aria-label="关闭 MCP 远程模型配置" onClick={onClose}><Icon name="close" size={15} /></Button>}
      </header>

      <div className="flex overflow-x-auto border-b border-line bg-paper/70">
        <WorkflowStep index="1" label="连接服务" caption="地址与服务端凭据" complete={Boolean(selected || form.endpoint)} active={!form.endpoint} />
        <WorkflowStep index="2" label="定义契约" caption="工具、任务与字段映射" complete={Boolean(selected || form.toolName)} active={Boolean(form.endpoint && !form.toolName)} />
        <WorkflowStep index="3" label="验证接口" caption="样例调用与契约校验" complete={["validated", "published"].includes(workflowState)} active={Boolean(selected && workflowState === "draft")} />
        <WorkflowStep index="4" label="发布使用" caption="进入模型资产与智能分析" complete={workflowState === "published"} active={workflowState === "validated"} />
      </div>

      {error && <div role="alert" className="flex items-center gap-2 border-b border-red/20 bg-red-soft px-5 py-2.5 text-xs text-red"><Icon name="alert" size={14} />{error}</div>}
      {notice && <div role="status" className="flex items-center gap-2 border-b border-teal/20 bg-teal-soft px-5 py-2.5 text-xs text-teal"><Icon name="check" size={14} />{notice}</div>}

      {!sourceId ? (
        <div className="grid min-h-64 place-items-center px-6 py-10 text-center">
          <div className="max-w-md">
            <span className="mx-auto grid h-12 w-12 place-items-center rounded-full border border-line bg-surface-2 text-primary"><Icon name="database" size={20} /></span>
            <h3 className="mt-4 text-base font-semibold text-ink">先选择模型服务所属的数据源</h3>
            <p className="mt-2 text-xs leading-5 text-muted">远程模型会按数据源隔离，字段查询、调用记录和发布范围都跟随当前选择。</p>
          </div>
        </div>
      ) : (
        <div className="grid min-h-[470px] lg:grid-cols-[260px_minmax(0,1fr)]">
          <aside className="border-b border-line bg-paper/55 lg:border-b-0 lg:border-r">
            <div className="flex h-12 items-center justify-between border-b border-line px-4">
              <div><strong className="block text-xs text-ink">远程模型资产</strong><span className="text-[10px] text-muted">{assets.length} 个接入项</span></div>
              {permissions.canConfigure && !loadingAssets && <Button size="sm" variant="primary" disabled={busy} onClick={reset}><Icon name="plus" size={12} />新增接入</Button>}
            </div>
            <div className="max-h-[610px] overflow-auto">
              {loadingAssets && <div className="px-4 py-8 text-center"><span className="mx-auto block h-5 w-5 animate-spin rounded-full border-2 border-line border-t-primary" /><p className="mt-3 text-[10px] text-muted">正在读取远程模型资产…</p></div>}
              {assets.map((asset) => (
                <button disabled={busy} key={asset.id} onClick={() => select(asset)} className={cn("group block w-full border-b border-line px-4 py-3 text-left transition-colors", selected?.id === asset.id ? "bg-surface shadow-[inset_3px_0_0_var(--color-primary)]" : "hover:bg-surface/75")}>
                  <span className="flex items-center justify-between gap-2"><strong className="truncate text-xs text-ink">{asset.name}</strong><StatusPill tone={statusTones[asset.status]}>{statuses[asset.status]}</StatusPill></span>
                  <span className="mt-1.5 flex items-center gap-1 text-[10px] text-muted"><Icon name="link" size={11} />MCP · v{asset.config.version}{asset.canEdit === false ? " · 只读" : ""}</span>
                </button>
              ))}
              {!loadingAssets && !assets.length && <div className="px-4 py-8 text-center"><p className="text-xs font-medium text-ink">还没有远程模型</p><p className="mt-1 text-[10px] leading-4 text-muted">点击“新增接入”配置企业模型服务。</p></div>}
            </div>
          </aside>

          <div className="min-w-0 bg-surface">
            {loadingAssets ? (
              <div className="grid min-h-64 place-items-center text-center"><div><span className="mx-auto block h-6 w-6 animate-spin rounded-full border-2 border-line border-t-primary" /><p className="mt-3 text-xs font-medium text-ink">正在读取 MCP 配置权限…</p><p className="mt-1 text-[10px] text-muted">数据源切换完成后会自动显示可用操作。</p></div></div>
            ) : !canEdit && !selected ? (
              <p className="p-6 text-sm text-muted">当前账号没有该数据源的 MCP 配置权限。</p>
            ) : (
              <>
                <fieldset disabled={busy || !canEdit || selected?.status === "published"}>
                  <legend className="sr-only">远程模型配置</legend>
                  <section className="border-b border-line px-5 py-4">
                    <div className="mb-3 flex items-center justify-between gap-3"><div><h3 className="text-xs font-semibold text-ink">服务连接</h3><p className="mt-0.5 text-[10px] text-muted">填写 MCP Streamable HTTP 地址，密钥只引用服务端保存的凭据。</p></div><span className="font-mono text-[10px] text-faint">CONNECTION</span></div>
                    <div className="grid gap-3 sm:grid-cols-2">
                      {field("name", "模型名称", "企业产量预测")}{field("version", "服务模型版本", "1")}
                      {field("endpoint", "MCP 服务地址", "https://model.company.com/mcp")}{field("credentialRef", "服务端凭据名称（可选）", "enterprise-model")}
                    </div>
                    <div className="mt-3 flex items-center justify-between gap-3 rounded-control border border-primary/15 bg-primary-soft/55 px-3 py-2">
                      <p className="text-[10px] leading-4 text-muted">地址必须在服务端白名单中；浏览器不会保存或展示明文密钥。</p>
                      <Button size="sm" variant="primary" onClick={() => act(async () => { setTools((await discoverRemoteTools(form)).items); setNotice("工具发现完成，请选择用于预测的工具。"); })}><Icon name="link" size={12} />连接并读取工具</Button>
                    </div>
                  </section>

                  <section className="border-b border-line px-5 py-4">
                    <div className="mb-3 flex items-center justify-between gap-3"><div><h3 className="text-xs font-semibold text-ink">调用契约</h3><p className="mt-0.5 text-[10px] text-muted">定义 Agent 如何向远程模型传入记录并读取结构化结果。</p></div><span className="font-mono text-[10px] text-faint">CONTRACT</span></div>
                    <div className="grid gap-3 sm:grid-cols-2">
                      <label className="text-[11px] font-medium text-muted">预测工具<input list="mcp-model-tools" className={inputClass} value={form.toolName} onChange={(event) => setForm({ ...form, toolName: event.target.value })} /><datalist id="mcp-model-tools">{tools.map((tool) => <option key={tool.name} value={tool.name} />)}</datalist></label>
                      <label className="text-[11px] font-medium text-muted">任务类型<select className={inputClass} value={form.taskType} onChange={(event) => setForm({ ...form, taskType: event.target.value })}><option value="regression">数值预测</option><option value="classification">分类</option><option value="clustering">聚类</option><option value="anomaly">异常识别</option></select></label>
                      {field("recordsArgument", "工具输入数组参数")}{field("outputKey", "结构化输出数组字段")}
                    </div>
                  </section>

                  <section className="px-5 py-4">
                    <div className="mb-3 flex items-center justify-between gap-3"><div><h3 className="text-xs font-semibold text-ink">业务与数据映射</h3><p className="mt-0.5 text-[10px] text-muted">限制只读取必要字段，并说明模型适用的业务问题。</p></div><span className="font-mono text-[10px] text-faint">MAPPING</span></div>
                    <div className="grid gap-3 sm:grid-cols-2">
                      <label className="text-[11px] font-medium text-muted sm:col-span-2">业务用途（供智能分析匹配）<textarea rows={2} className={cn(inputClass, "h-auto py-2")} value={form.description} onChange={(event) => setForm({ ...form, description: event.target.value })} /></label>
                      <label className="text-[11px] font-medium text-muted">特征查询 SQL（只读，最多 200 条）<textarea rows={4} className={cn(inputClass, "h-auto py-2 font-mono")} value={form.datasetSql} onChange={(event) => setForm({ ...form, datasetSql: event.target.value })} /></label>
                      <label className="text-[11px] font-medium text-muted">字段映射 JSON：远程字段 → SQL 结果列<textarea rows={4} className={cn(inputClass, "h-auto py-2 font-mono")} value={mapping} onChange={(event) => setMapping(event.target.value)} /></label>
                    </div>
                    {canEdit && <div className="mt-4 flex items-center justify-end border-t border-line pt-3"><Button variant="primary" onClick={() => act(async () => { const asset = await saveRemoteModel({ ...form, sourceId, fieldMapping: JSON.parse(mapping) }, selected?.id); setSelected(asset); setAssets((await listRemoteModels(sourceId)).items); setNotice("配置已保存，请验证接口后发布。"); })}><Icon name="check" size={13} />保存配置</Button></div>}
                  </section>
                </fieldset>

                {selected && (
                  <section className="border-t border-line bg-paper/45 px-5 py-4">
                    <div className="flex flex-wrap items-center justify-between gap-2"><div><h3 className="text-xs font-semibold text-ink">验证与调用</h3><p className="mt-0.5 text-[10px] text-muted">{canEdit ? "先验证接口契约，管理员发布后即可由智能分析调用。" : "该资产由其他成员维护，你可以调用已发布版本。"}</p></div><StatusPill tone={statusTones[selected.status]}>{statuses[selected.status]}</StatusPill></div>
                    {dirty && <p className="mt-2 text-xs text-amber">配置有未保存的修改，请先保存，再验证或调用。</p>}
                    <label className="mt-3 block text-[11px] font-medium text-muted" htmlFor="remote-samples">样例记录 JSON（留空则执行上方只读 SQL）</label>
                    <textarea id="remote-samples" disabled={busy} rows={3} className={cn(inputClass, "h-auto py-2 font-mono")} value={samples} placeholder='[{"source_column": 12}]' onChange={(event) => setSamples(event.target.value)} />
                    <div className="mt-3 flex flex-wrap gap-2">
                      {canEdit && <Button disabled={busy || dirty || selected.status === "published"} onClick={() => execute("validate")}><Icon name="shield" size={12} />验证接口</Button>}
                      {permissions.canPublish && <Button variant="primary" disabled={busy || dirty || selected.status !== "validated"} onClick={() => execute("publish")}><Icon name="check" size={12} />发布到模型资产</Button>}
                      <Button variant={selected.status === "published" ? "primary" : "secondary"} disabled={busy || dirty || selected.status !== "published"} onClick={() => execute("predict")}><Icon name="play" size={12} />运行预测</Button>
                      {permissions.canPublish && <Button disabled={busy || selected.status !== "published"} onClick={() => execute("retire")}>停用</Button>}
                      {busy && <span role="status" className="self-center text-xs text-muted">正在处理…</span>}
                    </div>
                    {result && <div className="mt-4 border-t border-line pt-4"><p className="text-sm font-semibold text-ink">{result.headline}</p><pre className="mt-2 max-h-60 overflow-auto rounded-control bg-ink p-3 text-xs text-white">{JSON.stringify(result.predictions, null, 2)}</pre></div>}
                  </section>
                )}

                {selected && (
                  <section className="border-t border-line px-5 py-4"><h3 className="mb-3 text-xs font-semibold text-ink">最近调用记录</h3><div className="overflow-auto"><table className="w-full text-left text-xs"><thead><tr className="border-b border-line text-[10px] text-muted"><th className="py-2 font-medium">时间</th><th className="font-medium">状态</th><th className="font-medium">记录数</th><th className="font-medium">耗时</th></tr></thead><tbody>{calls.map((call) => <tr key={call.id} className="border-b border-line"><td className="py-2">{call.createdAt.replace("T", " ")}</td><td>{call.status}</td><td>{call.recordCount}</td><td>{call.durationMs} ms</td></tr>)}</tbody></table></div>{!calls.length && <p className="text-xs text-muted">暂无调用记录。</p>}</section>
                )}
              </>
            )}
          </div>
        </div>
      )}
    </section>
  );

  return embedded ? panel : <PageFrame className="max-w-[1360px]">{panel}</PageFrame>;
}
