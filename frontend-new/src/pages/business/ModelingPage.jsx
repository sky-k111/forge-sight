import { useEffect, useRef, useState } from "react";
import { useOutletContext } from "react-router-dom";
import {
  diagnoseModelingTask,
  getModelingEvaluation,
  getModelingTask,
  getModelVersionHistory,
  listAlgorithms,
  listModelingTasks,
  trainModelingTask,
} from "../../api/modeling";
import Icon from "../../components/ui/Icon";
import cn from "../../lib/cn";
import { Button, Card, EmptyState, ErrorNotice, PageFrame, RouteSkeleton, StatusPill } from "../../components/ui";
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

const steps = ["训练数据体检", "隔离验证数据", "搜索候选方案", "评估与对比", "确定业务阈值", "生成业务解释"];

const OPTIMIZATION_LEVELS = [
  { id: "fast", label: "快速验证", caption: "默认参数，适合检查链路" },
  { id: "balanced", label: "平衡搜索", caption: "小范围调参，推荐日常使用" },
  { id: "thorough", label: "深入搜索", caption: "搜索范围更大，耗时更长" },
];

const DIAGNOSTIC_STATUS = {
  ready: { label: "可以训练", tone: "green" },
  warning: { label: "有提醒", tone: "amber" },
  blocked: { label: "暂不能训练", tone: "red" },
};

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
  const [diagnostics, setDiagnostics] = useState(null);
  const [diagnosing, setDiagnosing] = useState(false);
  const [versionHistory, setVersionHistory] = useState(null);
  const [optimizationLevel, setOptimizationLevel] = useState("balanced");
  const [trainError, setTrainError] = useState(null);
  const timerRef = useRef(null);

  const loadEvaluation = (taskId) => {
    setResults(null);
    getModelingEvaluation(taskId).then(setResults).catch(() => setResults(null));
  };

  const loadTaskIntelligence = (taskId) => {
    setDiagnosing(true);
    Promise.allSettled([diagnoseModelingTask(taskId), getModelVersionHistory(taskId)]).then(([diagnosticResult, historyResult]) => {
      setDiagnostics(diagnosticResult.status === "fulfilled" ? diagnosticResult.value : null);
      setVersionHistory(historyResult.status === "fulfilled" ? historyResult.value : null);
    }).finally(() => setDiagnosing(false));
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
      if (first?.id) loadTaskIntelligence(first.id);
    }).catch(setError);
  };

  useEffect(load, []);
  useEffect(() => () => window.clearInterval(timerRef.current), []);

  if (error) return <PageFrame><ErrorNotice title="建模任务加载失败" detail="请检查建模服务后重试。" onRetry={load} /></PageFrame>;
  if (!catalog || !tasks) return <RouteSkeleton />;

  const pageHeader = (
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
  );

  if (!tasks.length) {
    return (
      <PageFrame className="max-w-[1240px] gap-4">
        {pageHeader}
        <Card className="p-4">
          <EmptyState
            title="还没有建模任务"
            description="先在智能问析中描述预测、分类、聚类或异常识别需求；Agent 形成方案后，任务会进入这里等待确认。"
            action={<Button variant="primary" className="mt-3" onClick={() => navigatePage("analysis")}><Icon name="message" size={15} />描述建模需求</Button>}
            className="min-h-56 place-content-center border-0 bg-surface-2"
          />
        </Card>
      </PageFrame>
    );
  }

  const task = tasks.find((item) => item.id === selectedTaskId) ?? tasks[0];
  const running = training.active;
  const hasResult = task.status === "trained";
  const hasFailed = task.status === "failed";
  const needsNewPlan = hasFailed && /(^id$|_id$|^id_)/i.test(task.targetField ?? "");
  const selectedModel = catalog.find((item) => item.id === (strategy === "auto" ? task.selectedModel : manualModelId));
  const recommendedResult = (results?.models ?? []).find((item) => item.algorithm === results?.recommendedModel);
  const qualityAssessment = results?.qualityAssessment ?? recommendedResult?.qualityAssessment ?? recommendedResult?.explanation?.qualityAssessment ?? null;
  const publishedModel = (versionHistory?.models ?? []).find((model) => (
    model.status === "published"
    && model.algorithm === task.selectedModel
    && (!results?.trainingRevision || model.trainingRevision === results.trainingRevision)
  ));
  const canUseModel = Boolean(hasResult && qualityAssessment?.status !== "blocked" && publishedModel);

  const selectTask = (taskId) => {
    const nextTask = tasks.find((item) => item.id === taskId) ?? tasks[0];
    setSelectedTaskId(nextTask.id);
    setCandidateIds(nextTask.candidates);
    setManualModelId(nextTask.selectedModel ?? nextTask.candidates[0]);
    setStrategy("auto");
    setOptimizationLevel("balanced");
    setDiagnostics(null);
    setVersionHistory(null);
    setTrainError(null);
    setOpenPanel(nextTask.status === "trained" ? null : "plan");
    setTraining({ active: false, step: steps.length });
    if (nextTask.status === "trained") loadEvaluation(nextTask.id);
    loadTaskIntelligence(nextTask.id);
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
        optimizationLevel: strategy === "auto" ? optimizationLevel : "fast",
      });
      setTasks((current) => current.map((item) => item.id === response.task.id ? response.task : item));
      setResults(response.results);
      setDiagnostics(response.results?.data?.diagnostics ?? diagnostics);
      getModelVersionHistory(task.id).then(setVersionHistory).catch(() => {});
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
      {pageHeader}

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
        needsNewPlan={needsNewPlan}
        running={running}
        trainError={trainError}
        diagnostics={diagnostics}
        qualityAssessment={qualityAssessment}
        canUseModel={canUseModel}
        onUse={() => navigatePage("analysis")}
        onConfirm={train}
        onReviewPlan={() => setOpenPanel("plan")}
        onRetry={train}
        onNewPlan={() => navigatePage("analysis")}
      />

      <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1fr)_300px]">
        <Card className="overflow-hidden">
          <div className="border-b border-line px-5 py-4">
            <span className="text-[10px] font-semibold tracking-wide text-faint">业务建模任务</span>
            <h3 className="mt-1 text-base font-semibold tracking-tight text-ink">任务概览</h3>
          </div>
          <div className="grid sm:grid-cols-2">
            <SummaryItem label="要解决的问题" value={task.goal} wide />
            <SummaryItem
              label="使用数据"
              value={task.dataset}
              caption={sampleCaption({
                ...task,
                sampleCount: task.sampleCount || diagnostics?.sampleCount,
                sampleSummary: task.sampleSummary || (diagnostics?.usableSampleCount ? `可用于训练 ${Number(diagnostics.usableSampleCount).toLocaleString("zh-CN")} 条` : ""),
              })}
            />
            <SummaryItem label="预测目标" value={task.targetLabel || task.targetField || "未指定"} caption={task.targetField} mono />
            <SummaryItem label="当前方案" value={hasResult ? task.selectedModelName : "Agent 智能推荐"} caption={hasResult ? task.metric : `${task.candidates.length} 个候选模型待比较`} />
          </div>
        </Card>

        <Card className="p-5">
          <span className="text-[11px] font-semibold tracking-wide text-faint">接下来</span>
          <h3 className="mt-1.5 text-base font-semibold text-ink">{hasResult ? (qualityAssessment?.status === "blocked" ? "改进数据与特征" : canUseModel ? "用新数据进行分析" : "等待管理员复核") : running ? "等待训练完成" : "确认 Agent 的理解"}</h3>
          <p className="mt-2 text-xs leading-relaxed text-muted">
            {hasResult ? (qualityAssessment?.status === "blocked" ? (qualityAssessment.nextAction || "当前模型效果不足，暂时不会进入生产使用。") : canUseModel ? "生产版本已发布，Agent 可在授权范围内调用并返回预测与主要影响因素。" : "训练与隔离评估已经完成，管理员复核并发布后才会开放给 Agent 使用。") : running ? "训练与评估由后端同步执行，完成后会自动生成效果摘要。" : "确认预测目标和数据范围无误后，再开始训练。算法细节可交给系统推荐。"}
          </p>
          {canUseModel ? (
            <Button variant="primary" className="mt-4 w-full" onClick={() => navigatePage("analysis")}><Icon name="analysis" size={15} />使用模型分析</Button>
          ) : hasResult ? (
            <Button variant="secondary" className="mt-4 w-full" onClick={() => setOpenPanel("evaluation")}><Icon name="chart" size={15} />查看评估与建议</Button>
          ) : !running ? (
            <Button variant="primary" className="mt-4 w-full" onClick={() => setOpenPanel("plan")}><Icon name="check" size={15} />检查建模方案</Button>
          ) : null}
          <div className="mt-4 border-t border-line pt-3 text-[10px] leading-relaxed text-faint">模型发布、版本切换与权限由管理员统一管理。</div>
        </Card>
      </div>

      <Card className="overflow-hidden">
        <DisclosureRow
          icon="shield"
          title="训练数据体检"
          summary={diagnosing ? "正在读取训练数据并检查可用性" : diagnostics ? `${diagnostics.score ?? "—"} 分 · ${diagnostics.recommendedSplitLabel ?? "验证方式待确认"} · ${(diagnostics.warnings ?? []).length} 项提醒` : "尚未完成数据体检"}
          open={openPanel === "diagnostics"}
          onClick={() => togglePanel("diagnostics")}
        />
        {openPanel === "diagnostics" && (
          <DataReadiness diagnostics={diagnostics} loading={diagnosing} onRefresh={() => loadTaskIntelligence(task.id)} />
        )}

        <DisclosureRow
          icon="model"
          title="建模方案"
          summary={`智能推荐 · ${task.candidates.length} 个候选模型 · ${task.features.length} 个输入特征`}
          open={openPanel === "plan"}
          onClick={() => togglePanel("plan")}
          bordered
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
            diagnostics={diagnostics}
            optimizationLevel={optimizationLevel}
            setOptimizationLevel={setOptimizationLevel}
          />
        )}

        <DisclosureRow
          icon="clock"
          title="训练过程"
          summary={running ? `正在执行第 ${Math.min(training.step + 1, steps.length)} 步` : "6 个步骤 · 数据体检、隔离验证、受控搜索与解释"}
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
            <ModelResults selectedModelName={selectedModel?.name ?? task.selectedModelName} results={results} versionHistory={versionHistory} />
          </div>
        )}
      </Card>
    </PageFrame>
  );
}

function TaskOutcome({ task, results, diagnostics, qualityAssessment, canUseModel, hasResult, hasFailed, needsNewPlan, running, trainError, onUse, onConfirm, onReviewPlan, onRetry, onNewPlan }) {
  const qualityBlocked = hasResult && qualityAssessment?.status === "blocked";
  const state = needsNewPlan ? {
    eyebrow: "旧方案需要更新",
    title: "预测目标选择错误，请重新生成方案",
    description: `原方案把记录标识 ${task.targetField} 当作预测目标，不能直接重新训练。新方案会使用正确的数值指标并重新检查特征。`,
    icon: "warning",
    tone: "text-red bg-red-soft border-red/20",
    statusLabel: "方案不可用",
    statusTone: "red",
  } : hasFailed ? {
    eyebrow: "训练未完成",
    title: "模型训练失败，可以重新发起",
    description: trainError || "训练过程中发生错误。可检查建模方案后重新发起训练。",
    icon: "warning",
    tone: "text-red bg-red-soft border-red/20",
    statusLabel: "训练失败",
    statusTone: "red",
  } : qualityBlocked ? {
    eyebrow: "隔离评估未通过",
    title: "本轮模型暂不建议使用",
    description: qualityAssessment.reason,
    icon: "warning",
    tone: "text-red bg-red-soft border-red/20",
    statusLabel: qualityAssessment.label || "效果不足",
    statusTone: "red",
  } : hasResult && canUseModel ? {
    eyebrow: "生产版本已发布",
    title: "模型已经可以被 Agent 调用",
    description: "管理员已完成复核与发布，Agent 会在授权范围内使用模型并披露版本与主要影响因素。",
    icon: "check",
    tone: "text-green bg-green/10 border-green/20",
    statusLabel: "已发布",
    statusTone: "green",
  } : hasResult ? {
    eyebrow: "训练与隔离评估已完成",
    title: "等待管理员复核后发布",
    description: qualityAssessment?.reason || "候选模型已经生成，但尚未进入生产模型目录。",
    icon: "clock",
    tone: "text-amber bg-amber-soft border-amber/20",
    statusLabel: qualityAssessment?.label || "待复核",
    statusTone: "amber",
  } : running ? {
    eyebrow: "训练正在进行",
    title: "Agent 正在比较候选方案",
    description: "训练完成后会生成业务可读的结果摘要，并保留完整过程供你检查。",
    icon: "refresh",
    tone: "text-primary bg-primary-soft border-primary/20",
    statusLabel: "训练中",
    statusTone: "blue",
  } : {
    eyebrow: diagnostics?.status === "blocked" ? "训练数据未就绪" : "需要你的确认",
    title: diagnostics?.status === "blocked" ? "先处理数据问题，再开始训练" : "Agent 已准备好建模方案",
    description: diagnostics?.status === "blocked" ? diagnostics.blockers?.[0] : "请确认它理解了正确的预测目标和数据范围。算法选择可以继续使用智能推荐。",
    icon: diagnostics?.status === "blocked" ? "warning" : "model",
    tone: diagnostics?.status === "blocked" ? "text-red bg-red-soft border-red/20" : "text-amber bg-amber-soft border-amber/20",
    statusLabel: diagnostics?.status === "blocked" ? "数据未就绪" : "待确认",
    statusTone: diagnostics?.status === "blocked" ? "red" : "amber",
  };

  const metricPill = hasResult ? task.metric : null;
  const testCount = results?.data?.testCount;

  return (
    <section className="goal-banner goal-banner--neutral rounded-panel border border-line bg-surface px-5 py-5 text-ink shadow-card">
      <div className="flex flex-wrap items-center gap-4">
        <span className={cn("grid h-11 w-11 shrink-0 place-items-center rounded-card border", state.tone)}><Icon name={state.icon} size={20} /></span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2"><span className="text-[11px] font-semibold text-muted">{state.eyebrow}</span><StatusPill tone={state.statusTone}>{state.statusLabel}</StatusPill></div>
          <h3 className="mt-1.5 text-lg font-semibold tracking-tight text-ink">{state.title}</h3>
          <p className="mt-1 text-xs leading-relaxed text-muted">{state.description}</p>
        </div>
        <div className="flex gap-2">
          {needsNewPlan ? <Button variant="primary" onClick={onNewPlan}><Icon name="refresh" size={15} />重新生成方案</Button> : hasFailed ? <><Button variant="secondary" onClick={onReviewPlan}>检查建模方案</Button><Button variant="primary" onClick={onRetry}><Icon name="play" size={15} />重新训练</Button></> : qualityBlocked ? <><Button variant="secondary" onClick={onReviewPlan}>调整方案</Button><Button variant="primary" onClick={onRetry}><Icon name="refresh" size={15} />重新评估</Button></> : hasResult && canUseModel ? <><Button variant="secondary" onClick={onReviewPlan}>评估新版本</Button><Button variant="primary" onClick={onUse}><Icon name="analysis" size={15} />使用模型分析</Button></> : hasResult ? <Button variant="secondary" onClick={onReviewPlan}>评估新版本</Button> : running ? <Button variant="secondary" disabled><Icon name="refresh" size={15} />训练中</Button> : <Button variant="primary" onClick={diagnostics?.status === "blocked" ? onReviewPlan : onConfirm} disabled={!diagnostics}><Icon name={diagnostics?.status === "blocked" ? "warning" : "play"} size={15} />{diagnostics?.status === "blocked" ? "查看阻断原因" : "确认并开始训练"}</Button>}
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

function DataReadiness({ diagnostics, loading, onRefresh }) {
  if (loading) {
    return <div className="border-t border-line bg-surface-2 px-5 py-6 text-xs text-muted">正在读取训练数据，检查样本规模、缺失、类别分布、结果泄漏与验证切分…</div>;
  }
  if (!diagnostics) {
    return <div className="border-t border-line bg-surface-2 px-5 py-5"><ErrorNotice title="数据体检暂不可用" detail="没有改变训练任务，可重新检查。" onRetry={onRefresh} /></div>;
  }
  const state = DIAGNOSTIC_STATUS[diagnostics.status] ?? DIAGNOSTIC_STATUS.warning;
  return (
    <div className="border-t border-line bg-surface-2 px-5 py-5">
      <div className="mx-auto max-w-5xl">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div><h4 className="text-sm font-semibold text-ink">数据准备度 {diagnostics.score ?? "—"} 分</h4><p className="mt-1 text-[11px] text-muted">{diagnostics.sampleCount?.toLocaleString?.() ?? "—"} 条记录 · {diagnostics.featureCount ?? "—"} 个可用特征 · {diagnostics.recommendedSplitLabel ?? "验证方式待确认"}</p></div>
          <div className="flex items-center gap-2"><StatusPill tone={state.tone}>{state.label}</StatusPill><Button variant="secondary" onClick={onRefresh}>重新检查</Button></div>
        </div>
        <div className="mt-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
          {(diagnostics.checks ?? []).map((check) => (
            <div key={check.key} className="rounded-control border border-line bg-surface px-3 py-3">
              <div className="flex items-center justify-between gap-2"><strong className="text-[11px] text-ink">{check.label}</strong><span className={cn("h-2 w-2 rounded-full", check.status === "pass" ? "bg-green" : check.status === "fail" ? "bg-red" : "bg-amber")} /></div>
              <p className="mt-1.5 text-[10px] leading-relaxed text-muted">{check.message}</p>
            </div>
          ))}
        </div>
        {!!diagnostics.blockers?.length && <IssueList tone="red" title="必须先处理" items={diagnostics.blockers} />}
        {!!diagnostics.warnings?.length && <IssueList tone="amber" title="训练前请确认" items={diagnostics.warnings} />}
      </div>
    </div>
  );
}

function IssueList({ tone, title, items }) {
  return <div className={cn("mt-3 rounded-control border px-3 py-2.5", tone === "red" ? "border-red/25 bg-red-soft" : "border-amber/25 bg-amber-soft")}><strong className={cn("text-[11px]", tone === "red" ? "text-red" : "text-amber")}>{title}</strong><ul className="mt-1.5 grid gap-1 text-[10px] leading-relaxed text-muted">{items.map((item) => <li key={item}>· {item}</li>)}</ul></div>;
}

function PlanDetails({ task, catalog, strategy, setStrategy, candidateIds, toggleCandidate, manualModelId, setManualModelId, running, onTrain, hasResult, diagnostics, optimizationLevel, setOptimizationLevel }) {
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
          {strategy === "auto" && (
            <div className="mt-4">
              <label className="text-[11px] font-semibold text-muted">搜索强度</label>
              <div className="mt-2 grid gap-1.5 sm:grid-cols-3">
                {OPTIMIZATION_LEVELS.map((level) => <ModeButton key={level.id} active={optimizationLevel === level.id} onClick={() => setOptimizationLevel(level.id)} title={level.label} caption={level.caption} />)}
              </div>
            </div>
          )}
        </div>
        <div className="rounded-card border border-line bg-surface p-4">
          <h4 className="text-xs font-semibold text-ink">Agent 已确认</h4>
          <div className="mt-3 grid gap-3">
            <PlanFact label="预测目标" value={task.targetLabel || task.targetField || "未指定"} />
            <PlanFact label="训练数据" value={`${task.dataset} · ${task.sampleCount ? `${task.sampleCount.toLocaleString()} 条` : "待抽样"}`} />
            <PlanFact label="输入信息" value={task.features.length ? task.features.join("、") : "全部业务字段"} />
            <PlanFact label="验证方式" value={diagnostics?.recommendedSplitLabel ?? "等待数据体检"} />
            <div className={cn("flex items-start gap-2 rounded-control border px-2.5 py-2 text-[10px] leading-relaxed", diagnostics?.status === "blocked" ? "border-red/25 bg-red-soft text-red" : "border-teal/25 bg-teal-soft text-muted")}><span className={cn("mt-0.5", diagnostics?.status === "blocked" ? "text-red" : "text-teal")}><Icon name="shield" size={13} /></span>{diagnostics?.status === "blocked" ? diagnostics.blockers?.[0] : `数据准备度 ${diagnostics?.score ?? "—"} 分；最终测试集不会参与调参和阈值选择。`}</div>
          </div>
          <Button variant="primary" className="mt-4 w-full" onClick={onTrain} disabled={running || diagnostics?.status === "blocked" || !diagnostics || (strategy === "auto" && candidateIds.length === 0)}><Icon name="play" size={14} />{hasResult ? "训练候选新版本" : "确认并开始训练"}</Button>
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
  const summaries = [
    "规模、缺失、分布与泄漏检查",
    task.splitStrategy === "time" ? "按时间先后留出" : task.splitStrategy === "group" ? "按业务实体分组留出" : "自动选择防泄漏切分",
    candidateNames || "等待选择模型",
    task.taskType === "classification" ? "AUC / F1 / Precision / Recall" : task.metric || "按任务类型评估",
    task.taskType === "classification" ? "训练集折外概率选择阈值" : "当前任务无需分类阈值",
    "主要因素、适用边界与非因果声明",
  ];
  return (
    <div className="mx-auto max-w-5xl">
      <div className="mb-4 flex items-center justify-between"><div><h4 className="text-sm font-semibold text-ink">训练记录</h4><p className="mt-1 text-[10px] text-muted">过程由后端同步执行，展开仅用于核查。</p></div><StatusPill tone={running ? "blue" : trainError ? "red" : "green"}>{running ? "执行中" : trainError ? "失败" : "已完成"}</StatusPill></div>
      {trainError && <div className="mb-4 flex items-start gap-2 rounded-control border border-red/25 bg-red-soft px-3 py-2.5 text-[11px] leading-relaxed text-red"><span className="mt-0.5"><Icon name="warning" size={14} /></span><span>{trainError}</span></div>}
      <div className="grid gap-2 sm:grid-cols-3 lg:grid-cols-6">
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

function ModelResults({ selectedModelName, results, versionHistory }) {
  const models = results?.models ?? [];
  const recommended = results?.recommendedModel;
  const recommendedModel = models.find((model) => model.algorithm === recommended) ?? models[0];
  const metricKeys = METRIC_KEYS[recommendedModel?.family] ?? [];
  const importance = recommendedModel?.featureImportance ?? [];
  const tuning = recommendedModel?.tuning ?? {};
  const explanation = recommendedModel?.explanation ?? {};
  const quality = results?.qualityAssessment ?? recommendedModel?.qualityAssessment ?? explanation?.qualityAssessment;
  const threshold = explanation?.threshold;
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
        {quality && (
          <div className={cn(
            "mt-3 rounded-control border px-4 py-3 text-xs leading-relaxed",
            quality.status === "blocked" ? "border-red/25 bg-red-soft text-red" : quality.status === "ready" ? "border-green/25 bg-green/10 text-green" : "border-amber/25 bg-amber-soft text-amber",
          )}>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <strong>{quality.label}</strong>
              <span className="text-[10px] font-semibold">{quality.publishable === false ? "禁止发布" : "等待人工复核"}</span>
            </div>
            <p className="mt-1 text-ink-2">{quality.reason}</p>
            {quality.nextAction && <p className="mt-1 text-muted">建议：{quality.nextAction}</p>}
          </div>
        )}
        {recommendedModel && (
          <div className="mt-3 grid gap-2 sm:grid-cols-3">
            <ResultFact label="方案搜索" value={tuning.searched ? `${tuning.candidateCount} 组参数` : "默认参数"} caption={tuning.cvPolicy || "快速验证模式"} />
            <ResultFact label="交叉验证" value={tuning.cvScore == null ? "未执行" : String(tuning.cvScore)} caption={tuning.cvMetric || "最终测试集未参与选择"} />
            <ResultFact label="业务阈值" value={threshold?.threshold == null ? "不适用" : Number(threshold.threshold).toFixed(2)} caption={threshold ? `误报 ${threshold.falsePositive ?? "—"} · 漏报 ${threshold.falseNegative ?? "—"}` : "非二分类任务"} />
          </div>
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
            {data.missingPolicy && <span>缺失值策略：{formatMissingPolicy(data.missingPolicy)}</span>}
            <span>编码特征 {data.encodedFeatures?.length ?? 0} 列</span>
          </div>
        )}
        <div className="mt-4 rounded-control border border-amber/25 bg-amber-soft px-3 py-3 text-[11px] leading-relaxed text-muted">
          <div className="flex items-start gap-2"><span className="mt-0.5 text-amber"><Icon name="shield" size={14} /></span><span>{explanation.summary || "模型用于风险筛查，不代表产线或工序直接导致结果变化。"}</span></div>
          <p className="mt-2 border-t border-amber/20 pt-2 text-[10px]">适用边界：{explanation.applicabilityBoundary || "仅适用于当前数据源、字段口径和训练数据覆盖范围；发布前仍需管理员复核。"}</p>
        </div>
        <VersionHistory history={versionHistory} />
      </div>
    </div>
  );
}

function ResultFact({ label, value, caption }) {
  return <div className="rounded-control border border-line bg-surface px-3 py-2.5"><span className="text-[10px] text-faint">{label}</span><strong className="mt-1 block font-mono text-sm text-ink">{value}</strong><small className="mt-0.5 block truncate text-[10px] text-muted">{caption}</small></div>;
}

function formatMissingPolicy(policy) {
  if (typeof policy === "string") return policy;
  if (!policy || typeof policy !== "object") return "自动处理";
  return [policy.numeric, policy.categorical].filter(Boolean).join("；") || "按字段类型自动填补";
}

function VersionHistory({ history }) {
  const models = history?.models ?? [];
  if (!models.length) return null;
  return (
    <details className="mt-4 rounded-control border border-line bg-surface">
      <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-3 py-3 text-xs font-semibold text-ink"><span>模型版本记录</span><span className="text-[10px] font-normal text-muted">{models.length} 个版本 · 管理员负责发布与回滚</span></summary>
      <div className="border-t border-line">
        {models.map((model) => <div key={model.id} className="grid grid-cols-[minmax(0,1fr)_auto_auto] items-center gap-3 border-b border-line px-3 py-2.5 last:border-b-0"><div className="min-w-0"><strong className="block truncate text-[11px] text-ink">{model.algorithmName} v{model.version}</strong><small className="mt-0.5 block truncate text-[10px] text-muted">{model.metric || "等待评估"}{model.decisionThreshold != null ? ` · 阈值 ${Number(model.decisionThreshold).toFixed(2)}` : ""}</small></div><StatusPill tone={model.tone}>{model.statusLabel}</StatusPill><time className="text-[10px] text-faint">{formatDateTime(model.updatedAt)}</time></div>)}
      </div>
    </details>
  );
}
