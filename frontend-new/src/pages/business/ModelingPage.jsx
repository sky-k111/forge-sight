import { useEffect, useRef, useState } from "react";
import { useOutletContext } from "react-router-dom";
import { getModelingEvaluation, getModelingTask, listAlgorithms, listModelingTasks, trainModelingTask } from "../../api/modeling";
import Icon from "../../components/ui/Icon";
import cn from "../../lib/cn";
import { Button, Card, ErrorNotice, PageFrame, RouteSkeleton, StatusPill } from "../../components/ui";
import { RankBarChart } from "../../components/charts";

const MODELING_DRAFT_KEY = "forgesight:modeling-draft";

const accentTones = {
  blue: "bg-primary-soft text-primary",
  teal: "bg-teal-soft text-teal",
  violet: "bg-violet/8 text-violet",
  amber: "bg-amber-soft text-amber",
  rose: "bg-red-soft text-red",
  indigo: "bg-violet/8 text-violet",
};

const steps = ["理解建模目标", "准备训练数据", "训练候选模型", "评估与对比", "生成解释"];

// 各算法族的指标展示顺序与百分号单位
const METRIC_KEYS = {
  classification: [["auc", "AUC"], ["f1", "F1 Score"], ["precision", "Precision"], ["recall", "Recall"]],
  regression: [["r2", "R²"], ["mae", "MAE"], ["rmse", "RMSE"]],
  clustering: [["silhouette", "轮廓系数"]],
  anomaly: [["anomalyRate", "异常占比"]],
};
const PERCENT_KEYS = new Set(["accuracy", "precision", "recall", "anomalyRate"]);

function formatMetric(key, value) {
  if (value == null || value === "") return "—";
  if (PERCENT_KEYS.has(key)) return `${value}%`;
  return String(value);
}

function formatDateTime(value) {
  if (!value) return "暂无更新";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

function sampleCaption(task) {
  if (!task.sampleCount) return "待训练时完成抽样统计";
  const count = Number(task.sampleCount);
  const countText = `${count.toLocaleString("zh-CN")} 个样本`;
  const summary = String(task.sampleSummary ?? "").trim();
  const repeatsCount = summary.replaceAll(",", "").includes(String(count)) && /样本/.test(summary);
  return !summary || repeatsCount ? countText : `${countText} · ${summary}`;
}

function metricLine(model) {
  const keys = METRIC_KEYS[model.family] ?? [];
  const parts = keys.map(([key, label]) => `${label} ${formatMetric(key, model.metrics?.[key])}`);
  return parts.length ? parts.join(" · ") : "暂无指标";
}

export default function ModelingPage() {
  const { navigatePage } = useOutletContext();
  const [catalog, setCatalog] = useState(null);
  const [tasks, setTasks] = useState(null);
  const [error, setError] = useState(null);
  const [selectedTaskId, setSelectedTaskId] = useState(null);
  const [strategy, setStrategy] = useState("auto");
  const [candidateIds, setCandidateIds] = useState([]);
  const [manualModelId, setManualModelId] = useState("logistic");
  const [openPanel, setOpenPanel] = useState(null);
  const [training, setTraining] = useState({ active: false, step: steps.length });
  const [results, setResults] = useState(null);
  const [trainError, setTrainError] = useState(null);
  const timerRef = useRef(null);

  const loadEvaluation = (taskId) => {
    setResults(null);
    getModelingEvaluation(taskId).then(setResults).catch(() => setResults(null));
  };

  const load = () => {
    setError(null);
    setCatalog(null);
    setTasks(null);
    setResults(null);
    Promise.all([listAlgorithms(), listModelingTasks()]).then(([algorithmItems, taskItems]) => {
      let draftTaskId = null;
      try {
        const draft = JSON.parse(window.sessionStorage.getItem(MODELING_DRAFT_KEY) ?? "null");
        if (draft?.taskId) draftTaskId = draft.taskId;
      } catch {
        // 忽略损坏的草稿
      }
      const first = taskItems.find((item) => item.id === draftTaskId) ?? taskItems[0];
      setCatalog(algorithmItems);
      setTasks(taskItems);
      setSelectedTaskId(first?.id ?? null);
      setCandidateIds(first?.candidates ?? []);
      setManualModelId(first?.selectedModel ?? first?.candidates?.[0] ?? "logistic");
      if (draftTaskId) window.sessionStorage.removeItem(MODELING_DRAFT_KEY);
      if (first?.status === "trained") loadEvaluation(first.id);
    }).catch(setError);
  };

  useEffect(load, []);
  useEffect(() => () => window.clearInterval(timerRef.current), []);

  if (error) return <PageFrame><ErrorNotice title="建模任务加载失败" detail="请检查建模服务后重试。" onRetry={load} /></PageFrame>;
  if (!catalog || !tasks) return <RouteSkeleton />;

  const task = tasks.find((item) => item.id === selectedTaskId) ?? tasks[0];
  const running = training.active;
  const hasResult = task.status === "trained";
  const hasFailed = task.status === "failed";
  const selectedModel = catalog.find((item) => item.id === (strategy === "auto" ? task.selectedModel : manualModelId));

  const selectTask = (taskId) => {
    const nextTask = tasks.find((item) => item.id === taskId) ?? tasks[0];
    setSelectedTaskId(nextTask.id);
    setCandidateIds(nextTask.candidates);
    setManualModelId(nextTask.selectedModel ?? nextTask.candidates[0]);
    setStrategy("auto");
    setTrainError(null);
    setOpenPanel(nextTask.status === "trained" ? null : "plan");
    setTraining({ active: false, step: steps.length });
    if (nextTask.status === "trained") loadEvaluation(nextTask.id);
  };

  const refreshTask = (taskId) => {
    getModelingTask(taskId).then((fresh) => {
      setTasks((current) => current.map((item) => item.id === fresh.id ? fresh : item));
    }).catch(() => {});
  };

  const train = async () => {
    if (running) return;
    setTrainError(null);
    setOpenPanel("training");
    setTraining({ active: true, step: 0 });
    window.clearInterval(timerRef.current);
    timerRef.current = window.setInterval(() => {
      setTraining((current) => (current.active && current.step < steps.length - 1 ? { ...current, step: current.step + 1 } : current));
    }, 160);
    try {
      const response = await trainModelingTask(task.id, {
        strategy,
        candidateIds,
        manualModelId: strategy === "manual" ? manualModelId : null,
      });
      setTasks((current) => current.map((item) => item.id === response.task.id ? response.task : item));
      setResults(response.results);
      setOpenPanel("evaluation");
    } catch (err) {
      setTrainError(err.message || "训练失败，请重试。");
      setOpenPanel("training");
      refreshTask(task.id);
    } finally {
      window.clearInterval(timerRef.current);
      setTraining({ active: false, step: steps.length });
    }
  };

  const toggleCandidate = (modelId) => {
    setCandidateIds((current) => current.includes(modelId) ? current.filter((id) => id !== modelId) : [...current, modelId]);
  };

  const togglePanel = (panel) => setOpenPanel((current) => current === panel ? null : panel);

  return (
    <PageFrame className="max-w-[1240px] gap-4">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <span className="text-[11px] font-semibold tracking-wide text-faint">业务模型</span>
          <h2 className="mt-1.5 text-xl font-semibold tracking-tight text-ink">查看任务，确认下一步</h2>
          <p className="mt-1.5 max-w-2xl text-body leading-relaxed text-muted">Agent 负责准备方案和技术细节，你只需要确认业务目标、查看结果并决定是否使用。</p>
        </div>
        <div className="flex gap-2">
          <Button variant="secondary" onClick={() => navigatePage("analysis")}><Icon name="message" size={15} />回到智能问析</Button>
          <Button variant="primary" onClick={() => navigatePage("analysis")}><Icon name="plus" size={15} />描述预测需求</Button>
        </div>
      </header>

      <Card className="flex flex-wrap items-center gap-4 px-4 py-3">
        <span className="grid h-9 w-9 shrink-0 place-items-center rounded-control bg-primary-soft text-primary"><Icon name="model" size={17} /></span>
        <label className="min-w-[230px] flex-1">
          <span className="block text-[10px] font-semibold text-faint">当前任务</span>
          <select
            aria-label="切换建模任务"
            value={task.id}
            onChange={(event) => selectTask(event.target.value)}
            className="mt-0.5 w-full cursor-pointer border-0 bg-transparent p-0 text-sm font-semibold text-ink outline-none"
          >
            {tasks.map((item) => <option key={item.id} value={item.id}>{item.title} · {item.statusLabel}</option>)}
          </select>
        </label>
        <div className="hidden h-8 w-px bg-line sm:block" />
        <div className="min-w-36"><span className="block text-[10px] text-faint">任务来源</span><strong className="mt-0.5 block text-xs font-semibold text-ink-2">{task.sourceLabel}</strong></div>
        <div className="min-w-36"><span className="block text-[10px] text-faint">最近更新</span><strong className="mt-0.5 block text-xs font-semibold text-ink-2">{formatDateTime(task.updatedAt)}</strong></div>
        <StatusPill tone={task.tone}>{task.statusLabel}</StatusPill>
      </Card>

      <TaskOutcome
        task={task}
        results={results}
        hasResult={hasResult}
        hasFailed={hasFailed}
        running={running}
        trainError={trainError}
        onUse={() => navigatePage("analysis")}
        onConfirm={train}
        onReviewPlan={() => setOpenPanel("plan")}
        onRetry={train}
      />

      <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1fr)_300px]">
        <Card className="overflow-hidden">
          <div className="border-b border-line px-5 py-4">
            <span className="text-[10px] font-semibold tracking-wide text-faint">业务建模任务</span>
            <h3 className="mt-1 text-base font-semibold tracking-tight text-ink">任务概览</h3>
          </div>
          <div className="grid sm:grid-cols-2">
            <SummaryItem label="要解决的问题" value={task.goal} wide />
            <SummaryItem label="使用数据" value={task.dataset} caption={sampleCaption(task)} />
            <SummaryItem label="预测目标" value={task.targetLabel || task.targetField || "未指定"} caption={task.targetField} mono />
            <SummaryItem label="当前方案" value={hasResult ? task.selectedModelName : "Agent 智能推荐"} caption={hasResult ? task.metric : `${task.candidates.length} 个候选模型待比较`} />
          </div>
        </Card>

        <Card className="p-5">
          <span className="text-[11px] font-semibold tracking-wide text-faint">接下来</span>
          <h3 className="mt-1.5 text-base font-semibold text-ink">{hasResult ? "用新数据进行分析" : running ? "等待训练完成" : "确认 Agent 的理解"}</h3>
          <p className="mt-2 text-xs leading-relaxed text-muted">
            {hasResult ? "模型已经完成评估。使用时，Agent 会查询最新生产记录并返回风险概率和主要影响因素。" : running ? "训练与评估由后端同步执行，完成后会自动生成效果摘要。" : "确认预测目标和数据范围无误后，再开始训练。算法细节可交给系统推荐。"}
          </p>
          {hasResult ? (
            <Button variant="primary" className="mt-4 w-full" onClick={() => navigatePage("analysis")}><Icon name="analysis" size={15} />使用模型分析</Button>
          ) : !running ? (
            <Button variant="primary" className="mt-4 w-full" onClick={() => setOpenPanel("plan")}><Icon name="check" size={15} />检查建模方案</Button>
          ) : null}
          <div className="mt-4 border-t border-line pt-3 text-[10px] leading-relaxed text-faint">模型发布、版本切换与权限由管理员统一管理。</div>
        </Card>
      </div>

      <Card className="overflow-hidden">
        <DisclosureRow
          icon="model"
          title="建模方案"
          summary={`智能推荐 · ${task.candidates.length} 个候选模型 · ${task.features.length} 个输入特征`}
          open={openPanel === "plan"}
          onClick={() => togglePanel("plan")}
        />
        {openPanel === "plan" && (
          <PlanDetails
            task={task}
            catalog={catalog}
            strategy={strategy}
            setStrategy={setStrategy}
            candidateIds={candidateIds}
            toggleCandidate={toggleCandidate}
            manualModelId={manualModelId}
            setManualModelId={setManualModelId}
            running={running}
            onTrain={train}
            hasResult={hasResult}
          />
        )}

        <DisclosureRow
          icon="clock"
          title="训练过程"
          summary={running ? `正在执行第 ${training.step + 1} 步` : "5 个步骤 · 数据检查、训练、评估与解释"}
          open={openPanel === "training"}
          onClick={() => togglePanel("training")}
          bordered
        />
        {openPanel === "training" && (
          <div className="border-t border-line bg-surface-2 px-5 py-5">
            <TrainingPipeline task={task} catalog={catalog} candidateIds={candidateIds} strategy={strategy} manualModelId={manualModelId} trainingStep={training.step} running={running} trainError={trainError} />
          </div>
        )}

        <DisclosureRow
          icon="chart"
          title="效果与解释"
          summary={hasResult ? `${task.selectedModelName} · ${task.metric}` : "训练完成后查看效果摘要"}
          open={openPanel === "evaluation"}
          onClick={() => hasResult && togglePanel("evaluation")}
          disabled={!hasResult}
          bordered
        />
        {openPanel === "evaluation" && hasResult && (
          <div className="border-t border-line">
            <ModelResults selectedModelName={selectedModel?.name ?? task.selectedModelName} results={results} />
          </div>
        )}
      </Card>
    </PageFrame>
  );
}

function TaskOutcome({ task, results, hasResult, hasFailed, running, trainError, onUse, onConfirm, onReviewPlan, onRetry }) {
  const state = hasFailed ? {
    eyebrow: "训练未完成",
    title: "模型训练失败，可以重新发起",
    description: trainError || "训练过程中发生错误。可检查建模方案后重新发起训练。",
    icon: "warning",
    tone: "text-red bg-red-soft border-red/20",
  } : hasResult ? {
    eyebrow: "模型已完成评估",
    title: "可以开始识别高风险生产记录",
    description: "系统会把模型作为 Agent 工具使用。你只需提出业务问题，无需选择算法或手动整理数据。",
    icon: "check",
    tone: "text-green bg-green/10 border-green/20",
  } : running ? {
    eyebrow: "训练正在进行",
    title: "Agent 正在比较候选方案",
    description: "训练完成后会生成业务可读的结果摘要，并保留完整过程供你检查。",
    icon: "refresh",
    tone: "text-primary bg-primary-soft border-primary/20",
  } : {
    eyebrow: "需要你的确认",
    title: "Agent 已准备好建模方案",
    description: "请确认它理解了正确的预测目标和数据范围。算法选择可以继续使用智能推荐。",
    icon: "model",
    tone: "text-amber bg-amber-soft border-amber/20",
  };

  const metricPill = hasResult ? task.metric : null;
  const testCount = results?.data?.testCount;

  return (
    <section className="goal-banner goal-banner--neutral rounded-panel border border-line bg-surface px-5 py-5 text-ink shadow-card">
      <div className="flex flex-wrap items-center gap-4">
        <span className={cn("grid h-11 w-11 shrink-0 place-items-center rounded-card border", state.tone)}><Icon name={state.icon} size={20} /></span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2"><span className="text-[11px] font-semibold text-muted">{state.eyebrow}</span><StatusPill tone={task.tone}>{task.statusLabel}</StatusPill></div>
          <h3 className="mt-1.5 text-lg font-semibold tracking-tight text-ink">{state.title}</h3>
          <p className="mt-1 text-xs leading-relaxed text-muted">{state.description}</p>
        </div>
        <div className="flex gap-2">
          {hasFailed ? <><Button variant="secondary" onClick={onReviewPlan}>检查建模方案</Button><Button variant="primary" onClick={onRetry}><Icon name="play" size={15} />重新训练</Button></> : hasResult ? <><Button variant="secondary" onClick={onReviewPlan}>评估新版本</Button><Button variant="primary" onClick={onUse}><Icon name="analysis" size={15} />使用模型分析</Button></> : running ? <Button variant="secondary" disabled><Icon name="refresh" size={15} />训练中</Button> : <Button variant="primary" onClick={onConfirm}><Icon name="play" size={15} />确认并开始训练</Button>}
        </div>
      </div>
      {hasResult && (
        <div className="mt-5 flex flex-wrap items-center gap-x-8 gap-y-3 border-t border-line pt-4">
          <InlineMetric label="推荐模型" value={task.selectedModelName} />
          {metricPill && <InlineMetric label="效果摘要" value={metricPill} />}
          {testCount != null && <InlineMetric label="测试集" value={`${testCount.toLocaleString()} 条`} />}
        </div>
      )}
    </section>
  );
}

function InlineMetric({ label, value }) {
  return <div className="flex items-baseline gap-2"><span className="text-[10px] text-faint">{label}</span><strong className="font-mono text-sm font-semibold text-ink">{value}</strong></div>;
}

function SummaryItem({ label, value, caption, mono = false, wide = false }) {
  return (
    <div className={cn("border-b border-line px-5 py-4 sm:border-r", wide && "sm:col-span-2 sm:border-r-0")}>
      <span className="block text-[10px] font-semibold text-faint">{label}</span>
      <strong className="mt-1.5 block text-xs font-semibold leading-relaxed text-ink">{value}</strong>
      {caption && <small className={cn("mt-1 block text-[10px] text-muted", mono && "font-mono")}>{caption}</small>}
    </div>
  );
}

function DisclosureRow({ icon, title, summary, open, onClick, bordered = false, disabled = false }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-expanded={open}
      className={cn("flex w-full items-center gap-3 px-5 py-4 text-left transition-colors hover:bg-surface-2 disabled:cursor-default disabled:opacity-45 disabled:hover:bg-transparent", bordered && "border-t border-line")}
    >
      <span className="grid h-8 w-8 shrink-0 place-items-center rounded-control bg-surface-3 text-muted"><Icon name={icon} size={15} /></span>
      <span className="min-w-0 flex-1"><strong className="block text-xs font-semibold text-ink">{title}</strong><small className="mt-0.5 block truncate text-[10px] text-muted">{summary}</small></span>
      <span className={cn("text-faint transition-transform", open && "rotate-90")}><Icon name="chevron" size={15} /></span>
    </button>
  );
}

function PlanDetails({ task, catalog, strategy, setStrategy, candidateIds, toggleCandidate, manualModelId, setManualModelId, running, onTrain, hasResult }) {
  return (
    <div className="border-t border-line bg-surface-2 px-5 py-5">
      <div className="mx-auto grid max-w-4xl gap-5 lg:grid-cols-[minmax(0,1fr)_280px]">
        <div>
          <div className="flex items-center justify-between gap-3">
            <div><h4 className="text-sm font-semibold text-ink">由系统选择，或由你指定</h4><p className="mt-1 text-[11px] text-muted">大多数情况下保留智能推荐即可，只有明确业务限制时才需要人工指定。</p></div>
          </div>
          <div className="mt-3 grid grid-cols-2 rounded-control border border-line bg-surface p-0.5">
            <ModeButton active={strategy === "auto"} onClick={() => setStrategy("auto")} title="智能推荐" caption="自动比较并推荐结果" />
            <ModeButton active={strategy === "manual"} onClick={() => setStrategy("manual")} title="人工指定" caption="只训练选择的算法" />
          </div>
          <div className="mt-4 flex items-center justify-between gap-3"><label className="text-[11px] font-semibold text-muted">{strategy === "auto" ? "候选模型" : "指定算法"}</label>{strategy === "auto" && <span className="text-[10px] text-faint">召回率优先，兼顾可解释性</span>}</div>
          <div className="mt-2 grid gap-1.5 sm:grid-cols-2 lg:grid-cols-3">
            {catalog.map((model) => {
              const active = strategy === "auto" ? candidateIds.includes(model.id) : manualModelId === model.id;
              return (
                <button type="button" key={model.id} onClick={() => strategy === "auto" ? toggleCandidate(model.id) : setManualModelId(model.id)} disabled={running} className={cn("flex cursor-pointer items-center gap-2 rounded-control border bg-surface p-2 text-left transition-colors disabled:cursor-not-allowed disabled:opacity-55", active ? "border-ink/20 ring-1 ring-inset ring-line" : "border-line hover:border-line-strong")}>
                  <span className={cn("grid h-7 w-7 shrink-0 place-items-center rounded-tag", accentTones[model.accent] ?? accentTones.blue)}><Icon name="model" size={14} /></span>
                  <span className="min-w-0 flex-1"><strong className="block truncate text-xs font-semibold text-ink">{model.name}</strong><small className="block truncate text-[10px] text-faint">{model.type}</small></span>
                  {active && <span className="text-green"><Icon name="check" size={13} /></span>}
                </button>
              );
            })}
          </div>
        </div>
        <div className="rounded-card border border-line bg-surface p-4">
          <h4 className="text-xs font-semibold text-ink">Agent 已确认</h4>
          <div className="mt-3 grid gap-3">
            <PlanFact label="预测目标" value={task.targetLabel || task.targetField || "未指定"} />
            <PlanFact label="训练数据" value={`${task.dataset} · ${task.sampleCount ? `${task.sampleCount.toLocaleString()} 条` : "待抽样"}`} />
            <PlanFact label="输入信息" value={task.features.length ? task.features.join("、") : "全部业务字段"} />
            <div className="flex items-start gap-2 rounded-control border border-teal/25 bg-teal-soft px-2.5 py-2 text-[10px] leading-relaxed text-muted"><span className="mt-0.5 text-teal"><Icon name="shield" size={13} /></span>训练前会检查缺失值、类别比例与结果泄漏，数据源保持只读。</div>
          </div>
          <Button variant="primary" className="mt-4 w-full" onClick={onTrain} disabled={running || (strategy === "auto" && candidateIds.length === 0)}><Icon name="play" size={14} />{hasResult ? "训练候选新版本" : "确认并开始训练"}</Button>
        </div>
      </div>
    </div>
  );
}

function ModeButton({ active, onClick, title, caption }) {
  return <button type="button" onClick={onClick} className={cn("cursor-pointer rounded-tag px-3 py-2 text-left transition-colors", active ? "bg-surface-2 text-ink shadow-card" : "text-muted hover:text-ink")}><strong className="block text-xs font-semibold">{title}</strong><small className="mt-0.5 block text-[10px] text-faint">{caption}</small></button>;
}

function PlanFact({ label, value }) {
  return <div><span className="block text-[10px] text-faint">{label}</span><strong className="mt-0.5 block text-[11px] font-semibold leading-relaxed text-ink-2">{value}</strong></div>;
}

function TrainingPipeline({ task, catalog, candidateIds, strategy, manualModelId, trainingStep, running, trainError }) {
  const candidateNames = strategy === "auto" ? catalog.filter((model) => candidateIds.includes(model.id)).map((model) => model.name).join(" / ") : catalog.find((model) => model.id === manualModelId)?.name;
  const summaries = [task.taskTypeLabel, `${task.sampleCount ? task.sampleCount.toLocaleString() : "—"} 条 · 训练集 ${task.sampleCount ? Math.round(task.sampleCount * 0.8).toLocaleString() : "—"} 条`, candidateNames || "等待选择模型", "AUC / F1 / Precision / Recall", "特征重要性 · 适用边界"];
  return (
    <div className="mx-auto max-w-5xl">
      <div className="mb-4 flex items-center justify-between"><div><h4 className="text-sm font-semibold text-ink">训练记录</h4><p className="mt-1 text-[10px] text-muted">过程由后端同步执行，展开仅用于核查。</p></div><StatusPill tone={running ? "blue" : trainError ? "red" : "green"}>{running ? "执行中" : trainError ? "失败" : "已完成"}</StatusPill></div>
      {trainError && <div className="mb-4 flex items-start gap-2 rounded-control border border-red/25 bg-red-soft px-3 py-2.5 text-[11px] leading-relaxed text-red"><span className="mt-0.5"><Icon name="warning" size={14} /></span><span>{trainError}</span></div>}
      <div className="grid gap-2 sm:grid-cols-5">
        {steps.map((step, index) => (
          <div key={step} className="modeling-step flex gap-2 sm:flex-col">
            {index < steps.length - 1 && <span aria-hidden="true" className={cn("modeling-step__connector modeling-step__connector--dashed", index < trainingStep ? "modeling-step__connector--done" : index === trainingStep ? "modeling-step__connector--active" : "")} />}
            <span className={cn("grid h-6 w-6 shrink-0 place-items-center rounded-tag border text-[10px] font-semibold", index < trainingStep ? "border-green/50 bg-green/10 text-green" : index === trainingStep ? "border-primary/25 bg-primary-soft text-primary" : "border-line bg-surface text-faint")}>{index < trainingStep ? <Icon name="check" size={13} /> : index + 1}</span>
            <div className="min-w-0"><strong className={cn("block text-xs font-semibold", index <= trainingStep ? "text-ink" : "text-faint")}>{step}</strong><small className="mt-0.5 block text-[10px] leading-relaxed text-faint">{index < trainingStep ? summaries[index] : index === trainingStep ? "正在处理…" : "等待"}</small></div>
          </div>
        ))}
      </div>
    </div>
  );
}

function ModelResults({ selectedModelName, results }) {
  const models = results?.models ?? [];
  const recommended = results?.recommendedModel;
  const recommendedModel = models.find((model) => model.algorithm === recommended) ?? models[0];
  const metricKeys = METRIC_KEYS[recommendedModel?.family] ?? [];
  const importance = recommendedModel?.featureImportance ?? [];
  const data = results?.data;

  return (
    <div className="bg-surface-2 px-5 py-5">
      <div className="mx-auto max-w-5xl">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <span className="text-[10px] font-semibold text-faint">分层测试集 · {data?.testCount != null ? `${data.testCount.toLocaleString()} 条` : "训练时自动划分"}</span>
            <h4 className="mt-1 text-sm font-semibold text-ink">{selectedModelName}完整评估</h4>
          </div>
          <StatusPill tone="blue">系统推荐</StatusPill>
        </div>
        {recommendedModel ? (
          <div className="mt-4 flex flex-wrap gap-x-9 gap-y-3 rounded-card border border-line bg-surface px-4 py-3">
            {metricKeys.map(([key, label]) => <InlineMetric key={key} label={label} value={formatMetric(key, recommendedModel.metrics?.[key])} />)}
          </div>
        ) : (
          <p className="mt-4 text-xs text-muted">评估数据尚未生成，训练完成后自动显示指标。</p>
        )}
        <div className="mt-4 grid gap-5 lg:grid-cols-2">
          <div>
            <h5 className="text-xs font-semibold text-ink">候选模型对比</h5>
            <div className="mt-2 grid gap-1.5">
              {models.map((item) => (
                <div key={item.algorithm} className={cn("grid grid-cols-[minmax(100px,1fr)_minmax(150px,1.4fr)] items-center gap-2 rounded-control border px-3 py-2 text-[10px]", item.algorithm === recommended ? "border-primary/25 bg-primary-soft" : "border-line bg-surface")}>
                  <strong className="truncate text-xs text-ink">{item.name}</strong>
                  <span className="truncate font-mono text-[10px] text-ink-2">{metricLine(item)}</span>
                </div>
              ))}
              {!models.length && <p className="text-[11px] text-muted">暂无候选对比数据。</p>}
            </div>
          </div>
          <div>
            <h5 className="text-xs font-semibold text-ink">主要影响因素</h5>
            {importance.length ? (
              <div className="mt-3">
                <RankBarChart
                  data={importance.map((item) => ({ name: item.name, importance: item.importance }))}
                  xKey="name"
                  bars={[{ key: "importance", name: "重要性" }]}
                  height={200}
                  yTickFormatter={(value) => `${value}%`}
                />
              </div>
            ) : (
              <p className="mt-3 text-[11px] leading-relaxed text-muted">该算法族（聚类 / 异常检测）不提供原生特征重要性，请以指标与业务边界判断。</p>
            )}
          </div>
        </div>
        {data && (
          <div className="mt-4 flex flex-wrap gap-x-8 gap-y-2 text-[10px] text-faint">
            <span>训练集 {data.trainCount?.toLocaleString()} 条</span>
            <span>输入特征 {data.features?.length} 个</span>
            {data.missingPolicy && <span>缺失值策略：{data.missingPolicy}</span>}
            <span>编码特征 {data.encodedFeatures?.length ?? 0} 列</span>
          </div>
        )}
        <div className="mt-4 flex items-start gap-2 rounded-control border border-amber/25 bg-amber-soft px-3 py-2.5 text-[11px] leading-relaxed text-muted"><span className="mt-0.5 text-amber"><Icon name="shield" size={14} /></span>模型用于风险筛查，不代表产线或工序直接导致质量问题；发布前仍需管理员复核适用边界。</div>
      </div>
    </div>
  );
}
