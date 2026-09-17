import { useEffect, useRef, useState } from "react";
import { useOutletContext } from "react-router-dom";
import RemoteModelsPanel from "./RemoteModelsPanel";
import {
  diagnoseModelingTask,
  getModelingEvaluation,
  getModelingTask,
  getModelVersionHistory,
  listAlgorithms,
  listModelingTasks,
  publishModelDirect,
  submitModelReview,
  trainModelingTask,
} from "../../api/modeling";
import Icon from "../../components/ui/Icon";
import cn from "../../lib/cn";
import { getCachedUser } from "../../lib/auth";
import { Button, Card, EmptyState, ErrorNotice, Modal, PageFrame, RouteSkeleton, StatusPill } from "../../components/ui";
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
  const [remoteOpen, setRemoteOpen] = useState(false);
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
      // 默认展开有信息量的分区，避免整页只剩空折叠头
      setOpenPanel(first ? (first.status === "trained" ? "evaluation" : "plan") : null);
      if (first?.id) loadTaskIntelligence(first.id);
    }).catch(setError);
  };

  useEffect(load, []);
  useEffect(() => () => window.clearInterval(timerRef.current), []);

  if (error) return <PageFrame><ErrorNotice title="建模任务加载失败" detail="请检查建模服务后重试。" onRetry={load} /></PageFrame>;
  if (!catalog || !tasks) return <RouteSkeleton />;

  // 管理员工作区没有智能问析页，跳转入口对管理员隐藏
  const viewerIsAdmin = getCachedUser()?.role === "admin";
  const remoteDialog = (
    <Modal open={remoteOpen} onClose={() => setRemoteOpen(false)} maxWidth={1040} label="MCP 远程模型配置">
      <RemoteModelsPanel embedded onClose={() => setRemoteOpen(false)} />
    </Modal>
  );

  if (!tasks.length) {
    return (
      <>
        <PageFrame className="max-w-[1360px] gap-4">
          <header className="flex h-12 shrink-0 items-center gap-2 border-b border-line px-1">
            <h2 className="text-sm font-semibold tracking-tight text-ink">智能建模</h2>
            <div className="ml-auto flex items-center gap-1.5">
              <Button size="sm" variant="primary" onClick={() => setRemoteOpen(true)}><Icon name="plus" size={13} />MCP 远程模型</Button>
              {!viewerIsAdmin && <Button size="sm" variant="primary" onClick={() => navigatePage("analysis")}><Icon name="plus" size={13} />描述预测需求</Button>}
            </div>
          </header>
          <EmptyState
            title="还没有建模任务"
            description={viewerIsAdmin
              ? "在上方选择数据源与目标字段直接发起训练；训练通过后管理员可免审直发为生产版本。"
              : "先在智能问析中描述预测、分类、聚类或异常识别需求；Agent 形成方案后，任务会进入这里等待确认。"}
            action={viewerIsAdmin ? null : <Button variant="primary" className="mt-3" onClick={() => navigatePage("analysis")}><Icon name="message" size={15} />描述建模需求</Button>}
            className="min-h-44 place-content-center rounded-panel border border-line bg-surface"
          />
        </PageFrame>
        {remoteDialog}
      </>
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
  const personalModel = (versionHistory?.models ?? []).find((model) => (
    model.status === "personal"
    && model.algorithm === task.selectedModel
    && (!results?.trainingRevision || model.trainingRevision === results.trainingRevision)
  ));
  const isAdmin = viewerIsAdmin;
  const governAct = async (fn) => {
    try {
      await fn();
      refreshTask(task.id);
    } catch (err) {
      setTrainError(err?.message || "治理操作失败，请稍后重试");
    }
  };

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
    setOpenPanel(nextTask.status === "trained" ? "evaluation" : "plan");
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

  const outcome = buildOutcome({
    needsNewPlan, hasFailed, hasResult, canUseModel, running, diagnostics, trainError, qualityAssessment,
  });
  const sampleCount = task.sampleCount || diagnostics?.sampleCount;

  return (
    <>
    <PageFrame className="max-w-[1360px]">
      {/* 风格基线：整页只有一个工作区容器，内部用 1px 分隔线衔接 */}
      <div className="overflow-hidden rounded-panel border border-line bg-surface shadow-card">
      <header className="flex h-12 shrink-0 flex-wrap items-center gap-2 border-b border-line px-4">
        <h2 className="text-sm font-semibold tracking-tight text-ink">智能建模</h2>
        <span className="hidden h-4 w-px bg-line sm:block" aria-hidden="true" />
        <label className="relative hidden min-w-0 max-w-[340px] flex-1 items-center sm:flex">
          <select
            aria-label="切换建模任务"
            value={task.id}
            onChange={(event) => selectTask(event.target.value)}
            className="input-control h-7 w-full cursor-pointer appearance-none truncate px-2.5 pr-6 text-xs font-semibold"
          >
            {tasks.map((item) => <option key={item.id} value={item.id}>{item.title} · {item.statusLabel}</option>)}
          </select>
          <Icon name="chevron" size={12} className="pointer-events-none absolute right-2 rotate-90 text-faint" />
        </label>
        <StatusPill tone={task.tone}>{task.statusLabel}</StatusPill>
        <span className="hidden truncate font-mono text-[11px] text-faint lg:inline">{task.sourceLabel} · 更新 {formatDateTime(task.updatedAt)}</span>
        <div className="ml-auto flex shrink-0 items-center">
          <Button size="sm" variant="primary" onClick={() => setRemoteOpen(true)}><Icon name="plus" size={13} />MCP 远程模型</Button>
        </div>
      </header>

      <div className="flex flex-wrap items-center gap-3 border-b border-line px-4 py-2.5">
        <span className={cn("grid h-8 w-8 shrink-0 place-items-center rounded-control border", outcome.tone)}><Icon name={outcome.icon} size={15} /></span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <strong className="truncate text-sm font-semibold tracking-tight text-ink">{outcome.title}</strong>
            <StatusPill tone={outcome.statusTone}>{outcome.statusLabel}</StatusPill>
            {hasResult && task.metric && (
              <span className="truncate font-mono text-[11px] text-muted">{task.selectedModelName} · {task.metric}</span>
            )}
          </div>
          <p className="mt-0.5 truncate text-[11px] text-muted">{outcome.description}</p>
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-1.5">
          {needsNewPlan ? (
            !isAdmin && <Button size="sm" variant="primary" onClick={() => navigatePage("analysis")}><Icon name="refresh" size={13} />重新生成方案</Button>
          ) : hasFailed || (hasResult && qualityAssessment?.status === "blocked") ? (
            <Button size="sm" variant="primary" onClick={train}><Icon name="refresh" size={13} />重新训练</Button>
          ) : running ? (
            <Button size="sm" disabled><Icon name="refresh" size={13} />训练中</Button>
          ) : hasResult && canUseModel ? (
            !isAdmin && <Button size="sm" variant="primary" onClick={() => navigatePage("analysis")}><Icon name="analysis" size={13} />使用模型分析</Button>
          ) : (
            <Button size="sm" variant="primary" onClick={train} disabled={!diagnostics || diagnosing}><Icon name="play" size={13} />确认并开始训练</Button>
          )}
          {hasResult && (
            <Button size="sm" variant="ghost" onClick={() => togglePanel("evaluation")}><Icon name="chart" size={13} />查看评估</Button>
          )}
        </div>
      </div>
      <div className="grid grid-cols-2 divide-y divide-line border-b border-line bg-paper/60 sm:grid-cols-5 sm:divide-y-0 sm:divide-x">
        <Fact label="训练样本" value={sampleCount ? sampleCount.toLocaleString("zh-CN") : "—"} note={sampleCaption({ ...task, sampleCount, sampleSummary: task.sampleSummary || (diagnostics?.usableSampleCount ? `可用于训练 ${Number(diagnostics.usableSampleCount).toLocaleString("zh-CN")} 条` : "") })} />
        <Fact label="预测目标" value={task.targetLabel || task.targetField || "未指定"} note={task.targetField} mono />
        <Fact label="候选方案" value={`${task.candidates.length} 个`} note={strategy === "auto" ? "智能推荐算法" : "人工指定算法"} />
        <Fact label="质量门禁" value={qualityAssessment?.label ?? (hasResult ? "已通过" : "待训练")} note={qualityAssessment?.reason ?? "隔离评估后生成"} />
        <Fact label="模型状态" value={publishedModel ? "已发布" : personalModel ? "个人自用" : hasResult ? "待复核" : "未训练"} note={personalModel && !publishedModel ? `有效期至 ${personalModel.personalExpiresAt ?? "—"}` : task.dataset} />
      </div>

      <div className="grid lg:grid-cols-[minmax(0,1.6fr)_300px]">
        <section className="min-w-0 border-b border-line lg:border-b-0 lg:border-r">
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
              diagnostics={diagnostics}
              optimizationLevel={optimizationLevel}
              setOptimizationLevel={setOptimizationLevel}
            />
          )}

          <DisclosureRow
            icon="shield"
            title="训练数据体检"
            summary={diagnosing ? "正在读取训练数据并检查可用性" : diagnostics ? `${diagnostics.score ?? "—"} 分 · ${diagnostics.recommendedSplitLabel ?? "验证方式待确认"} · ${(diagnostics.warnings ?? []).length} 项提醒` : "尚未完成数据体检"}
            open={openPanel === "diagnostics"}
            onClick={() => togglePanel("diagnostics")}
            bordered
          />
          {openPanel === "diagnostics" && (
            <DataReadiness diagnostics={diagnostics} loading={diagnosing} onRefresh={() => loadTaskIntelligence(task.id)} />
          )}

          <DisclosureRow
            icon="chart"
            title="候选与效果"
            summary={hasResult ? `${task.selectedModelName} · ${task.metric}` : "训练完成后查看候选对比与解释"}
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
        </section>

        <aside className="min-w-0">
          <section>
            <h3 className="border-b border-line px-4 py-2.5 text-[11px] font-semibold text-muted">接下来</h3>
            <div className="px-4 py-3">
              <p className="text-xs leading-relaxed text-muted">{outcome.railCopy}</p>
              <div className="mt-3 grid gap-2">
                {canUseModel ? (
                  !isAdmin && <Button variant="primary" onClick={() => navigatePage("analysis")}><Icon name="analysis" size={15} />使用模型分析</Button>
                ) : personalModel ? (
                  <>
                    <Button variant="secondary" onClick={() => governAct(() => submitModelReview(personalModel.id))}><Icon name="check" size={15} />提交复核</Button>
                    {isAdmin && (
                      <Button variant="primary" onClick={() => governAct(() => publishModelDirect(personalModel.id))}><Icon name="shield" size={15} />管理员免审直发</Button>
                    )}
                  </>
                ) : hasResult ? (
                  <Button variant="secondary" onClick={() => togglePanel("evaluation")}><Icon name="chart" size={15} />查看评估与建议</Button>
                ) : !running ? (
                  <Button variant="primary" onClick={() => togglePanel("plan")}><Icon name="check" size={15} />检查建模方案</Button>
                ) : null}
              </div>
            </div>
            <div className="border-t border-line px-4 py-3">
              <h4 className="text-[11px] font-semibold text-muted">治理状态</h4>
              <dl className="mt-2 grid grid-cols-[64px_minmax(0,1fr)] gap-y-1.5 text-[11px]">
                <dt className="text-muted">生产版本</dt>
                <dd className="truncate text-ink-2">{publishedModel ? `v${publishedModel.version} · 发布于 ${publishedModel.publishedAt ?? "—"}` : "未发布"}</dd>
                <dt className="text-muted">个人档</dt>
                <dd className="truncate text-ink-2">{personalModel ? `v${personalModel.version} · 有效期至 ${personalModel.personalExpiresAt ?? "—"}` : "无"}</dd>
                <dt className="text-muted">任务来源</dt>
                <dd className="truncate text-ink-2">{task.sourceLabel}</dd>
              </dl>
            </div>
            <div className="border-t border-line px-4 py-3 text-[11px] leading-relaxed text-faint">个人档仅本人可用；发布、版本切换与权限由管理员统一管理。</div>
          </section>
        </aside>
      </div>
      </div>
    </PageFrame>
    {remoteDialog}
    </>
  );
}

function buildOutcome({ needsNewPlan, hasFailed, hasResult, canUseModel, running, diagnostics, trainError, qualityAssessment }) {
  const qualityBlocked = hasResult && qualityAssessment?.status === "blocked";
  if (needsNewPlan) {
    return {
      icon: "warning", tone: "border-red/20 bg-red-soft text-red", statusTone: "red", statusLabel: "方案不可用",
      title: "预测目标选择错误，请重新生成方案",
      description: "原方案把记录标识当作预测目标，不能直接重新训练；新方案会使用正确的数值指标并重新检查特征。",
      railCopy: "回到智能问析重新描述需求，Agent 会带着正确的目标重新生成建模方案。",
    };
  }
  if (hasFailed) {
    return {
      icon: "warning", tone: "border-red/20 bg-red-soft text-red", statusTone: "red", statusLabel: "训练失败",
      title: "模型训练失败，可以重新发起",
      description: trainError || "训练过程中发生错误。可检查建模方案后重新发起训练。",
      railCopy: "检查建模方案中的目标、特征与候选算法，然后重新发起训练。",
    };
  }
  if (qualityBlocked) {
    return {
      icon: "warning", tone: "border-red/20 bg-red-soft text-red", statusTone: "red", statusLabel: qualityAssessment.label || "效果不足",
      title: "本轮模型暂不建议使用",
      description: qualityAssessment.reason,
      railCopy: qualityAssessment.nextAction || "当前模型效果不足，暂时不会进入生产使用。",
    };
  }
  if (hasResult && canUseModel) {
    return {
      icon: "check", tone: "border-green/20 bg-green/10 text-green", statusTone: "green", statusLabel: "已发布",
      title: "模型已经可以被 Agent 调用",
      description: "管理员已完成复核与发布，Agent 会在授权范围内使用模型并披露版本与主要影响因素。",
      railCopy: "生产版本已发布，Agent 可在授权范围内调用并返回预测与主要影响因素。",
    };
  }
  if (hasResult) {
    return {
      icon: "clock", tone: "border-amber/20 bg-amber-soft text-amber", statusTone: "amber", statusLabel: qualityAssessment?.label || "待复核",
      title: "训练与隔离评估已完成",
      description: qualityAssessment?.reason || "候选模型已经生成，但尚未进入生产模型目录。",
      railCopy: "训练与隔离评估已经完成；个人自用档已开启，提交复核并发布后才会开放给全企业。",
    };
  }
  if (running) {
    return {
      icon: "refresh", tone: "border-primary/20 bg-primary-soft text-primary", statusTone: "blue", statusLabel: "训练中",
      title: "Agent 正在比较候选方案",
      description: "训练完成后会生成业务可读的结果摘要，并保留完整过程供你检查。",
      railCopy: "训练与评估由后端同步执行，完成后会自动生成效果摘要。",
    };
  }
  const blocked = diagnostics?.status === "blocked";
  return {
    icon: blocked ? "warning" : "model",
    tone: blocked ? "border-red/20 bg-red-soft text-red" : "border-amber/20 bg-amber-soft text-amber",
    statusTone: blocked ? "red" : "amber",
    statusLabel: blocked ? "数据未就绪" : "待确认",
    title: blocked ? "先处理数据问题，再开始训练" : "Agent 已准备好建模方案",
    description: blocked ? diagnostics.blockers?.[0] : "请确认它理解了正确的预测目标和数据范围。算法选择可以继续使用智能推荐。",
    railCopy: "确认预测目标和数据范围无误后，再开始训练。算法细节可交给系统推荐。",
  };
}

function InlineMetric({ label, value }) {
  return <div className="flex items-baseline gap-2"><span className="text-[10px] text-faint">{label}</span><strong className="font-mono text-sm font-semibold text-ink">{value}</strong></div>;
}

function Fact({ label, value, note, mono = false }) {
  return (
    <div className="min-w-0 px-4 py-3">
      <span className="block text-[11px] text-muted">{label}</span>
      <strong className={cn("mt-1 block truncate text-base font-semibold leading-none tracking-tight text-ink", mono && "font-mono")}>{value}</strong>
      {note && <small className="mt-1.5 block truncate text-[11px] text-faint">{note}</small>}
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
      className={cn("flex w-full items-center gap-3 px-5 py-3 text-left transition-colors hover:bg-surface-2 disabled:cursor-default disabled:opacity-45 disabled:hover:bg-transparent", bordered && "border-t border-line")}
    >
      <span className="grid h-7 w-7 shrink-0 place-items-center rounded-tag bg-surface-3 text-muted"><Icon name={icon} size={14} /></span>
      <span className="min-w-0 flex-1"><strong className="block text-xs font-semibold text-ink">{title}</strong><small className="mt-0.5 block truncate text-[11px] text-muted">{summary}</small></span>
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
          <div className="mt-4 flex items-center justify-between gap-3"><label className="text-[11px] font-semibold text-muted">{strategy === "auto" ? "候选模型" : "指定算法"}</label>{strategy === "auto" && <span className="text-[11px] text-faint">召回率优先，兼顾可解释性</span>}</div>
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
            <div className={cn("flex items-start gap-2 rounded-control border px-2.5 py-2 text-[11px] leading-relaxed", diagnostics?.status === "blocked" ? "border-red/25 bg-red-soft text-red" : "border-teal/25 bg-teal-soft text-muted")}><span className={cn("mt-0.5", diagnostics?.status === "blocked" ? "text-red" : "text-teal")}><Icon name="shield" size={13} /></span>{diagnostics?.status === "blocked" ? diagnostics.blockers?.[0] : `数据准备度 ${diagnostics?.score ?? "—"} 分；最终测试集不会参与调参和阈值选择。`}</div>
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
      <div className="mb-4 flex items-center justify-between"><div><h4 className="text-sm font-semibold text-ink">训练记录</h4><p className="mt-1 text-[11px] text-muted">过程由后端同步执行，展开仅用于核查。</p></div><StatusPill tone={running ? "blue" : trainError ? "red" : "green"}>{running ? "执行中" : trainError ? "失败" : "已完成"}</StatusPill></div>
      {trainError && <div className="mb-4 flex items-start gap-2 rounded-control border border-red/25 bg-red-soft px-3 py-2.5 text-[11px] leading-relaxed text-red"><span className="mt-0.5"><Icon name="warning" size={14} /></span><span>{trainError}</span></div>}
      <div className="grid gap-2 sm:grid-cols-3 lg:grid-cols-6">
        {steps.map((step, index) => (
          <div key={step} className="modeling-step flex gap-2 sm:flex-col">
            {index < steps.length - 1 && <span aria-hidden="true" className={cn("modeling-step__connector modeling-step__connector--dashed", index < trainingStep ? "modeling-step__connector--done" : index === trainingStep ? "modeling-step__connector--active" : "")} />}
            <span className={cn("grid h-6 w-6 shrink-0 place-items-center rounded-tag border text-[10px] font-semibold", index < trainingStep ? "border-green/50 bg-green/10 text-green" : index === trainingStep ? "border-primary/25 bg-primary-soft text-primary" : "border-line bg-surface text-faint")}>{index < trainingStep ? <Icon name="check" size={13} /> : index + 1}</span>
            <div className="min-w-0"><strong className={cn("block text-xs font-semibold", index <= trainingStep ? "text-ink" : "text-faint")}>{step}</strong><small className="mt-0.5 block text-[11px] leading-relaxed text-faint">{index < trainingStep ? summaries[index] : index === trainingStep ? "正在处理…" : "等待"}</small></div>
          </div>
        ))}
      </div>
    </div>
  );
}

function ModelResults({ selectedModelName, results, versionHistory }) {
  const [scriptOpen, setScriptOpen] = useState(false);
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
            <span className="text-[11px] font-semibold text-faint">分层测试集 · {data?.testCount != null ? `${data.testCount.toLocaleString()} 条` : "训练时自动划分"}</span>
            <h4 className="mt-1 text-sm font-semibold text-ink">{selectedModelName}完整评估</h4>
          </div>
          <StatusPill tone="blue">系统推荐</StatusPill>
        </div>
        {recommendedModel && metricKeys.length ? (
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
              <span className="text-[11px] font-semibold">{quality.publishable === false ? "禁止发布" : "等待人工复核"}</span>
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
            ) : explanation.clusterProfiles?.length ? (
              <div className="mt-2 grid gap-1.5">
                {explanation.clusterProfiles.map((profile) => (
                  <div key={profile.cluster} className="rounded-control border border-line bg-surface px-3 py-2">
                    <div className="flex items-center justify-between gap-2 text-[11px]">
                      <strong className="text-ink">群 {profile.cluster}</strong>
                      <span className="font-mono text-faint">{profile.size?.toLocaleString?.() ?? profile.size} 条 · {profile.share}%</span>
                    </div>
                    {profile.drivers?.length > 0 && (
                      <p className="mt-1 truncate text-[11px] text-muted">
                        特征：{profile.drivers.map((driver) => `${driver.name}${driver.direction === "高于整体" ? "↑" : "↓"}（z ${driver.z}）`).join(" · ")}
                      </p>
                    )}
                  </div>
                ))}
              </div>
            ) : (
              <p className="mt-3 text-[11px] leading-relaxed text-muted">该算法族（异常检测）不提供原生特征重要性，请以指标与业务边界判断。</p>
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
          <p className="mt-2 border-t border-amber/20 pt-2 text-[11px]">适用边界：{explanation.applicabilityBoundary || "仅适用于当前数据源、字段口径和训练数据覆盖范围；发布前仍需管理员复核。"}</p>
        </div>
        {explanation.script && (
          <div className="mt-4 rounded-control border border-line bg-surface">
            <button
              type="button"
              aria-expanded={scriptOpen}
              onClick={() => setScriptOpen((value) => !value)}
              className="flex w-full cursor-pointer items-center justify-between gap-2 px-3 py-2.5 text-left"
            >
              <span className="flex items-center gap-2 text-xs font-semibold text-ink"><Icon name="code" size={14} />建模代码（与训练配置一致，可复现）</span>
              <span className={cn("text-faint transition-transform", scriptOpen && "rotate-90")}><Icon name="chevron" size={13} /></span>
            </button>
            {scriptOpen && (
              <pre className="max-h-72 overflow-auto border-t border-line bg-surface-2 px-3 py-3 font-mono text-[11px] leading-relaxed text-ink-2">{explanation.script}</pre>
            )}
          </div>
        )}
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
      <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-3 py-3 text-xs font-semibold text-ink"><span>模型版本记录</span><span className="text-[11px] font-normal text-muted">{models.length} 个版本 · 管理员负责发布与回滚</span></summary>
      <div className="border-t border-line">
        {models.map((model) => <div key={model.id} className="grid grid-cols-[minmax(0,1fr)_auto_auto] items-center gap-3 border-b border-line px-3 py-2.5 last:border-b-0"><div className="min-w-0"><strong className="block truncate text-[11px] text-ink">{model.algorithmName} v{model.version}</strong><small className="mt-0.5 block truncate text-[11px] text-muted">{model.metric || "等待评估"}{model.decisionThreshold != null ? ` · 阈值 ${Number(model.decisionThreshold).toFixed(2)}` : ""}</small></div><StatusPill tone={model.tone}>{model.statusLabel}</StatusPill><time className="text-[10px] text-faint">{formatDateTime(model.updatedAt)}</time></div>)}
      </div>
    </details>
  );
}
