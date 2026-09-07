import { useEffect, useRef, useState } from "react";
import { useNavigate, useOutletContext } from "react-router-dom";
import Icon from "../../components/ui/Icon";
import forgeSightLogo from "../../assets/forgesight-logo.png";
import cn from "../../lib/cn";
import { Button, Card, ErrorNotice, StatusPill } from "../../components/ui";
import { ChartLegend, ComboMetricChart, CorrelationScatterChart, MatrixHeatmapChart, RankBarChart, ShareDonutChart, TrendLineChart } from "../../components/charts";
import { listSources } from "../../api/datasources";
import { createModelingTask } from "../../api/modeling";
import { submitSkillFeedback } from "../../api/skills";
import {
  createAnalysisBranch,
  createAnalysisSession,
  getAnalysisRun,
  getAnalysisSession,
  listAnalysisSessions,
  respondToClarification,
  submitAnalysisRun,
  subscribeAnalysisRun,
} from "../../features/analysis/api";
import { adaptAnalysisRun } from "../../features/analysis/runViewAdapter";
import AnalysisAdjustmentPanel from "../../features/analysis/AnalysisAdjustmentPanel";
import { buildTracePath, getTraceFitTransform } from "./traceGraphLayout";
import { waitForAgentThinking } from "./analysisThinking";
import { currentUserId } from "../../lib/auth";

const USER_ID = currentUserId;
const TERMINAL_STATUSES = new Set(["succeeded", "failed", "blocked", "cancelled"]);
const EVIDENCE_PANEL_MIN_WIDTH = 320;
const EVIDENCE_PANEL_DEFAULT_WIDTH = 400;
const EVIDENCE_PANEL_COLLAPSED_WIDTH = 56;
const EVIDENCE_PANEL_WIDTH_KEY = "forgesight:analysis-evidence-width";
const ACTIVE_SOURCE_KEY = "forgesight:active-source-id";
const ANALYSIS_DRAFT_KEY = "forgesight:analysis-draft";
const MODELING_DRAFT_KEY = "forgesight:modeling-draft";
const ANALYSIS_SESSION_KEY = "forgesight:analysis-session-id";
const ANALYSIS_NEW_SESSION_KEY = "forgesight:analysis-new-session";
const ANALYSIS_SESSION_SELECTED_EVENT = "forgesight:analysis-session-selected";

export default function AnalysisPage() {
  const { evidenceOpen, setEvidenceOpen, navigatePage, refreshSessions } = useOutletContext();
  const navigate = useNavigate();
  const [messages, setMessages] = useState(null);
  const [loadError, setLoadError] = useState(null);
  const [pending, setPending] = useState(false);
  const [busy, setBusy] = useState(false);
  const [evidenceRun, setEvidenceRun] = useState(null);
  const [branchNotice, setBranchNotice] = useState("");
  const [branchContext, setBranchContext] = useState(null);
  const [originRun, setOriginRun] = useState(null);
  const [adjustmentTarget, setAdjustmentTarget] = useState(null);
  const [branchSubmitting, setBranchSubmitting] = useState(false);
  const [query, setQuery] = useState("");
  const [evidenceWidth, setEvidenceWidth] = useState(() => {
    const stored = Number(window.localStorage?.getItem(EVIDENCE_PANEL_WIDTH_KEY));
    return Number.isFinite(stored) && stored >= EVIDENCE_PANEL_MIN_WIDTH ? stored : EVIDENCE_PANEL_DEFAULT_WIDTH;
  });
  const [isEvidenceResizing, setIsEvidenceResizing] = useState(false);
  const [viewportWidth, setViewportWidth] = useState(() => window.innerWidth);
  const [activeSession, setActiveSession] = useState(null);
  const [activeSource, setActiveSource] = useState(null);
  const [availableSources, setAvailableSources] = useState([]);
  const [scopeOpen, setScopeOpen] = useState(false);
  const scrollRef = useRef(null);
  const composerRef = useRef(null);
  const subscriptionsRef = useRef(new Map());
  const refreshTimersRef = useRef(new Map());
  // 每次加载都递增版本号，避免旧会话请求晚返回后覆盖“新建分析”的空白界面。
  const loadVersionRef = useRef(0);
  const [clarifySubmitting, setClarifySubmitting] = useState(false);
  const [clarifyError, setClarifyError] = useState(null);

  const evidenceMaxWidth = Math.max(EVIDENCE_PANEL_MIN_WIDTH, viewportWidth - 252 - 480);
  const effectiveEvidenceWidth = Math.min(evidenceWidth, evidenceMaxWidth);

  const beginEvidenceResize = (event) => {
    if (event.button !== 0) return;
    event.preventDefault();
    setIsEvidenceResizing(true);
    let latestWidth = evidenceWidth;
    const onMove = (moveEvent) => {
      const maxWidth = Math.max(EVIDENCE_PANEL_MIN_WIDTH, document.documentElement.clientWidth - 252 - 480);
      const next = document.documentElement.clientWidth - moveEvent.clientX;
      latestWidth = Math.max(EVIDENCE_PANEL_MIN_WIDTH, Math.min(maxWidth, next));
      setEvidenceWidth(latestWidth);
    };
    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      setIsEvidenceResizing(false);
      window.localStorage?.setItem(EVIDENCE_PANEL_WIDTH_KEY, String(latestWidth));
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  };

  useEffect(() => {
    const updateViewport = () => setViewportWidth(window.innerWidth);
    window.addEventListener("resize", updateViewport);
    return () => window.removeEventListener("resize", updateViewport);
  }, []);

  const scrollToBottom = () => {
    window.requestAnimationFrame(() => {
      const node = scrollRef.current;
      if (!node) return;
      if (typeof node.scrollTo === "function") {
        node.scrollTo({ top: node.scrollHeight, behavior: "smooth" });
        return;
      }
      node.scrollTop = node.scrollHeight;
    });
  };

  const upsertRun = (snapshot) => {
    const run = adaptAnalysisRun(snapshot);
    setMessages((current) => {
      if (current == null) return current;
      const existingIndex = current.findIndex((item) => item.role === "assistant" && item.run?.id === run.id);
      if (existingIndex < 0) return [...current, { id: run.id, role: "assistant", run, animate: false }];
      const next = [...current];
      next[existingIndex] = { ...next[existingIndex], run, animate: false };
      return next;
    });
    // 仅当面板正在展示该 run（或尚无面板）时更新，避免后台 run 到达终态抢占当前证据链。
    setEvidenceRun((current) => (current == null || current.id === run.id) ? run : current);
    if (TERMINAL_STATUSES.has(run.status)) setBusy(false);
    else if (["queued", "running", "retrying", "clarifying"].includes(run.status)) setBusy(true);
    return run;
  };

  const refreshRun = async (runId) => {
    const snapshot = await getAnalysisRun(runId);
    upsertRun(snapshot);
    if (TERMINAL_STATUSES.has(snapshot.status)) {
      subscriptionsRef.current.get(runId)?.();
      subscriptionsRef.current.delete(runId);
    }
  };

  const scheduleRefresh = (runId) => {
    window.clearTimeout(refreshTimersRef.current.get(runId));
    refreshTimersRef.current.set(runId, window.setTimeout(() => {
      refreshRun(runId).catch(() => {});
      refreshTimersRef.current.delete(runId);
    }, 80));
  };

  const watchRun = (runId) => {
    if (subscriptionsRef.current.has(runId)) return;
    const unsubscribe = subscribeAnalysisRun(runId, {
      // SSE 推送仅作变更通知：收到新事件/断流后触发一次全量快照刷新。
      onEvent: () => scheduleRefresh(runId),
      onError: () => scheduleRefresh(runId),
    });
    subscriptionsRef.current.set(runId, unsubscribe);
  };

  const load = async (forceNewSession = false) => {
    const loadVersion = ++loadVersionRef.current;
    setLoadError(null);
    setMessages(null);
    setQuery("");
    setActiveSession(null);
    setEvidenceRun(null);
    setBranchContext(null);
    setOriginRun(null);
    setAdjustmentTarget(null);
    setBusy(false);
    // 切换数据源/会话前先停掉旧 run 的订阅，避免旧分析串入新会话。
    subscriptionsRef.current.forEach((unsubscribe) => unsubscribe());
    subscriptionsRef.current.clear();
    refreshTimersRef.current.forEach((timer) => window.clearTimeout(timer));
    refreshTimersRef.current.clear();
    try {
      const sources = await listSources();
      if (loadVersion !== loadVersionRef.current) return;
      setAvailableSources(sources);
      const preferredSourceId = Number(window.sessionStorage.getItem(ACTIVE_SOURCE_KEY));
      const source = sources.find((item) => item.id === preferredSourceId)
        ?? sources.find((item) => item.isDefault)
        ?? sources[0];
      if (!source) throw new Error("尚未配置可用数据源");
      window.sessionStorage.setItem(ACTIVE_SOURCE_KEY, String(source.id));
      setActiveSource(source);
      const sessions = await listAnalysisSessions(USER_ID());
      if (loadVersion !== loadVersionRef.current) return;
      const isNewSession = forceNewSession || window.sessionStorage.getItem(ANALYSIS_NEW_SESSION_KEY) === "1";
      window.sessionStorage.removeItem(ANALYSIS_NEW_SESSION_KEY);
      const preferredSessionId = window.sessionStorage.getItem(ANALYSIS_SESSION_KEY);
      const selected = isNewSession
        ? null
        : sessions.find((item) => item.id === preferredSessionId && item.sourceId === source.id)
          ?? sessions.find((item) => item.sourceId === source.id)
          ?? null;
      window.sessionStorage.removeItem(ANALYSIS_SESSION_KEY);
      setActiveSession(selected);
      const draft = window.sessionStorage.getItem(ANALYSIS_DRAFT_KEY);
      window.sessionStorage.removeItem(ANALYSIS_DRAFT_KEY);
      setQuery(draft ?? "");
      if (!selected) {
        setMessages([]);
        setEvidenceRun(null);
        setBusy(false);
        return;
      }
      refreshSessions?.(selected.id);
      const sessionView = await getAnalysisSession(selected.id);
      if (loadVersion !== loadVersionRef.current) return;
      setBranchContext(sessionView.branchContext ?? null);
      const snapshots = await Promise.all((sessionView.runs ?? []).map((run) => getAnalysisRun(run.id)));
      if (loadVersion !== loadVersionRef.current) return;
      let items = snapshots.flatMap((snapshot) => {
        const run = adaptAnalysisRun(snapshot);
        const created = new Date(snapshot.createdAt);
        const time = Number.isNaN(created.getTime()) ? "" : created.toTimeString().slice(0, 5);
        return [
          { id: `${run.id}-q`, role: "user", text: snapshot.question, time },
          { id: run.id, role: "assistant", run, animate: false },
        ];
      });
      if (!sessionView.branchContext && snapshots.length === 0 && (sessionView.messages ?? []).length > 0) {
        items = sessionView.messages.map((message) => {
          const created = new Date(message.createdAt);
          const time = Number.isNaN(created.getTime()) ? "" : created.toTimeString().slice(0, 5);
          return message.role === "user"
            ? { id: message.id, role: "user", text: message.content, time }
            : { id: message.id, role: "assistant", plainText: message.content, inherited: true };
        });
      }
      if (sessionView.branchContext?.sourceRunId) {
        try {
          const sourceSnapshot = await getAnalysisRun(sessionView.branchContext.sourceRunId);
          if (loadVersion !== loadVersionRef.current) return;
          setOriginRun(adaptAnalysisRun(sourceSnapshot));
        } catch {
          if (loadVersion === loadVersionRef.current) setOriginRun(null);
        }
      } else {
        setOriginRun(null);
      }
      if (sessionView.branchContext && snapshots.length === 0) {
        setAdjustmentTarget({
          checkpoint: sessionView.branchContext.checkpoint,
          label: checkpointBusinessLabel(sessionView.branchContext.checkpoint?.stepKey),
          sourceQuestion: sessionView.branchContext.sourceQuestion,
          sourceRunId: sessionView.branchContext.sourceRunId,
        });
      }
      setMessages(items);
      const lastRun = snapshots.at(-1);
      setEvidenceRun(lastRun ? adaptAnalysisRun(lastRun) : null);
      setBusy(snapshots.some((snapshot) => !TERMINAL_STATUSES.has(snapshot.status)));
      for (const snapshot of snapshots) {
        if (!TERMINAL_STATUSES.has(snapshot.status)) watchRun(snapshot.id);
      }
    } catch (error) {
      if (loadVersion === loadVersionRef.current) setLoadError(error);
    }
  };
  useEffect(() => {
    load();
    const handleSessionSelected = (event) => load(Boolean(event.detail?.newSession));
    const subscriptions = subscriptionsRef.current;
    const refreshTimers = refreshTimersRef.current;
    window.addEventListener(ANALYSIS_SESSION_SELECTED_EVENT, handleSessionSelected);
    return () => {
      loadVersionRef.current += 1;
      window.removeEventListener(ANALYSIS_SESSION_SELECTED_EVENT, handleSessionSelected);
      subscriptions.forEach((unsubscribe) => unsubscribe());
      subscriptions.clear();
      refreshTimers.forEach((timer) => window.clearTimeout(timer));
      refreshTimers.clear();
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(scrollToBottom, [messages, pending]);

  const acceptRun = async (accepted, text, session = activeSession) => {
    if (!session) return;
    setActiveSession((current) => current ? { ...current, title: text } : current);
    refreshSessions?.(session.id);
    const immediateTerminal = TERMINAL_STATUSES.has(accepted.status);
    // 澄清（waiting_user）/建模提案（modeling_proposal）需要 interaction/proposal 数据才能渲染对应卡片，
    // 而验收响应只回 runId/status；占位快照缺这些数据，先按“运行中”展示，待 refreshRun 拉取全量快照后再切换。
    const needsCardData = ["waiting_user", "modeling_proposal"].includes(accepted.status);
    const placeholder = adaptAnalysisRun({
      id: accepted.runId,
      sessionId: session.id,
      sourceId: activeSource?.id,
      question: text,
      status: immediateTerminal || needsCardData ? "running" : accepted.status,
      tasks: [], steps: [], artifacts: [], checkpoints: [], usage: {},
    });
    setBusy(true);
    setMessages((current) => [...current, { id: accepted.runId, role: "assistant", run: placeholder, animate: false }]);
    if (immediateTerminal) {
      await waitForAgentThinking();
      await refreshRun(accepted.runId);
    } else {
      watchRun(accepted.runId);
      await refreshRun(accepted.runId);
    }
  };

  const submit = async (prompt = query) => {
    const text = prompt.trim();
    if (!text || pending || busy || messages == null) return;
    const idempotencyKey = crypto.randomUUID();
    const time = new Date().toTimeString().slice(0, 5);
    setMessages((current) => [...current, { id: `user-${Date.now()}`, role: "user", text, time }]);
    setQuery("");
    setBranchNotice("");
    setPending(true);
    try {
      let session = activeSession;
      if (!session) {
        session = await createAnalysisSession({
          title: text,
          sourceId: activeSource?.id,
          userId: USER_ID(),
        });
        setActiveSession(session);
        window.sessionStorage.setItem(ANALYSIS_SESSION_KEY, session.id);
        refreshSessions?.(session.id);
      }
      const accepted = await submitAnalysisRun(session.id, text, idempotencyKey);
      await acceptRun(accepted, text, session);
    } catch {
      // 失败时保留问题文字与幂等键，便于用同一 key 重试而不产生重复 run。
      setMessages((current) => [...current, { id: `error-${Date.now()}`, role: "assistant", failed: true, retry: { text, idempotencyKey } }]);
    } finally {
      setPending(false);
    }
  };

  const retryFailed = async (message) => {
    const { text, idempotencyKey } = message.retry ?? {};
    if (!text || !idempotencyKey || pending || busy || !activeSession) return;
    setBranchNotice("");
    setPending(true);
    setMessages((current) => current.filter((item) => item.id !== message.id));
    try {
      const accepted = await submitAnalysisRun(activeSession.id, text, idempotencyKey);
      await acceptRun(accepted, text);
    } catch {
      setMessages((current) => [...current, { id: `error-${Date.now()}`, role: "assistant", failed: true, retry: { text, idempotencyKey } }]);
    } finally {
      setPending(false);
    }
  };

  const clarify = async (_messageId, option, interactionId) => {
    setClarifySubmitting(true);
    setClarifyError(null);
    setBusy(true);
    try {
      const value = typeof option === "string"
        ? option
        : option?.value ?? JSON.stringify(option);
      const resumed = await respondToClarification(interactionId, value);
      if (TERMINAL_STATUSES.has(resumed.status)) {
        await waitForAgentThinking();
        await refreshRun(resumed.runId);
      } else {
        watchRun(resumed.runId);
        await refreshRun(resumed.runId);
      }
    } catch {
      setClarifyError("回答提交失败，请重试。");
      setBusy(false);
    } finally {
      setClarifySubmitting(false);
    }
  };

  const handleComplete = (run) => {
    setBusy(false);
    setEvidenceRun(run);
    scrollToBottom();
  };

  const branchFrom = (step) => {
    const checkpoint = step.checkpoint;
    if (!checkpoint?.rollbackable) return;
    setAdjustmentTarget({
      checkpoint,
      label: step.label,
      sourceQuestion: evidenceRun?.question ?? "",
      sourceRunId: evidenceRun?.id,
    });
  };

  const submitAdjustment = async ({ instruction, adjustments, reuseArtifacts }) => {
    const checkpoint = adjustmentTarget?.checkpoint;
    if (!checkpoint?.rollbackable || branchSubmitting) return;
    setBranchSubmitting(true);
    try {
      const branch = await createAnalysisBranch(checkpoint.id, {
        title: `${activeSession?.title ?? "制造业务问析"} · 调整方案`,
        userId: USER_ID(),
        instruction,
        adjustments,
        reuseArtifacts,
      });
      setActiveSession(branch.session);
      setBranchContext(branch.branchContext);
      setOriginRun(evidenceRun);
      window.sessionStorage.setItem(ANALYSIS_SESSION_KEY, branch.session.id);
      refreshSessions?.(branch.session.id);
      const time = new Date().toTimeString().slice(0, 5);
      if (branch.run?.runId) {
        const placeholder = adaptAnalysisRun({
          id: branch.run.runId,
          sessionId: branch.session.id,
          sourceId: activeSource?.id,
          question: instruction,
          status: branch.run.status,
          tasks: [],
          steps: [],
          artifacts: [],
          checkpoints: [],
          usage: {},
        });
        setMessages([
          { id: `${branch.run.runId}-q`, role: "user", text: instruction, time },
          { id: branch.run.runId, role: "assistant", run: placeholder, animate: false },
        ]);
        setBusy(true);
        watchRun(branch.run.runId);
        await refreshRun(branch.run.runId);
      } else {
        setMessages([]);
      }
      setEvidenceOpen(false);
      setAdjustmentTarget(null);
      setBranchNotice("已保留原分析，新方案会按调整后的条件独立执行。");
    } catch {
      setBranchNotice("新方案创建失败，请刷新运行状态后重试。");
    } finally {
      setBranchSubmitting(false);
    }
  };

  const chooseAnalysisSource = (source) => {
    setScopeOpen(false);
    if (!source || source.id === activeSource?.id) return;
    window.sessionStorage.setItem(ACTIVE_SOURCE_KEY, String(source.id));
    load();
  };

  const examples = ["统计每条产线最近 7 天的产量趋势", "找出最近一个月不良数量最高的产品", "比较各产线的非计划停机时长"];
  const showExamples = messages?.length === 0 && !pending;
  const currentBranchRun = messages?.filter((item) => item.role === "assistant" && item.run).at(-1)?.run ?? null;

  if (loadError) {
    return (
      <div className="grid h-full place-content-center bg-surface p-8">
        <ErrorNotice title="分析会话加载失败" detail={loadError.message || "请检查数据连接后重试。"} onRetry={load} className="w-[420px] max-w-full" />
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 overflow-hidden bg-surface">
      <section className="flex min-w-0 flex-1 flex-col">
        <div ref={scrollRef} className="min-h-0 flex-1 overflow-auto px-6 pt-6">
          <div className="mx-auto w-full max-w-[760px] pb-6">
            {branchContext && (
              <BranchOriginCard
                context={branchContext}
                originRun={originRun}
                onViewOrigin={() => {
                  if (!originRun) return;
                  setEvidenceRun(originRun);
                  setEvidenceOpen(true);
                }}
                onAdjust={() => setAdjustmentTarget({
                  checkpoint: branchContext.checkpoint,
                  label: checkpointBusinessLabel(branchContext.checkpoint?.stepKey),
                  sourceQuestion: branchContext.sourceQuestion,
                  sourceRunId: branchContext.sourceRunId,
                })}
              />
            )}
            {messages == null ? <ConversationSkeleton /> : messages.length === 0 ? (
              <AnalysisEmptyState
                onPrompt={(prompt) => {
                  setQuery(prompt);
                  window.requestAnimationFrame(() => composerRef.current?.focus());
                }}
                onNavigateModeling={() => navigatePage("modeling")}
                onNavigateReports={() => navigatePage("reports")}
                onNavigateProfile={() => navigate("/workspace/profile")}
              />
            ) : (
              <div className="analysis-conversation-state">
                {messages.map((message) => {
                  return message.role === "user" ? (
                    <UserMessage key={message.id} text={message.text} />
                  ) : (
                    <div key={message.id} className="motion-soft-rise mt-8">
                      <div className="min-w-0">
                        {message.plainText ? (
                          <div className="mt-1.5 rounded-card border border-line bg-surface-2 px-3.5 py-3 text-body leading-relaxed text-ink-2">
                            {message.plainText}
                            {message.inherited && <span className="mt-1 block text-[10px] text-faint">继承自分析分支起点</span>}
                          </div>
                        ) : message.failed ? (
                          <div className="mt-1.5 rounded-card border border-red/25 bg-red-soft px-3.5 py-3 text-xs text-ink-2">
                            分析请求发送失败，请稍后重试。
                            {message.retry && (
                              <button
                                type="button"
                                onClick={() => retryFailed(message)}
                                className="mt-2 inline-flex cursor-pointer items-center gap-1 rounded-control px-2 py-1 text-xs font-semibold text-red transition-colors hover:bg-red/10"
                              >
                                <Icon name="refresh" size={14} />重试
                              </button>
                            )}
                          </div>
                        ) : (
                          <AssistantRunMessage
                            run={message.run}
                            animate={message.animate}
                            onComplete={handleComplete}
                            onOpenEvidence={(run) => { setEvidenceRun(run); setEvidenceOpen(true); }}
                            onClarify={(option, interactionId) => clarify(message.id, option, interactionId)}
                            onFollowUp={(text) => submit(text)}
                            onOpenModeling={() => navigatePage("modeling")}
                            clarifySubmitting={clarifySubmitting}
                            clarifyError={clarifyError}
                          />
                        )}
                      </div>
                    </div>
                  );
                })}
                {branchContext && originRun && currentBranchRun && TERMINAL_STATUSES.has(currentBranchRun.status) && (
                  <BranchComparisonCard context={branchContext} originRun={originRun} currentRun={currentBranchRun} />
                )}
                {pending && (
                  <div className="mt-8 border-l-2 border-primary/35 pl-3">
                    <div className="flex items-center gap-2 text-xs text-muted">
                      <span className="flex items-center gap-1">
                        {[0, 1, 2].map((dot) => (
                          <span key={dot} className="h-1.5 w-1.5 animate-pulse rounded-full bg-faint" style={{ animationDelay: `${dot * 160}ms` }} />
                        ))}
                      </span>
                      正在理解问题并匹配业务知识…
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
        <div className="bg-surface px-6 pb-4 pt-0">
          <div className="relative mx-auto w-full max-w-[760px]">
            {branchNotice && (
              <div className="motion-soft-rise mb-2.5 flex items-center gap-2.5 rounded-card border border-primary/25 bg-primary-soft px-3 py-2.5 text-xs">
                <span className="shrink-0 text-primary"><Icon name="link" size={15} /></span>
                <div className="min-w-0 flex-1">
                  <strong className="block font-semibold text-ink">已创建新分析方案</strong>
                  <span className="text-muted">{branchNotice}</span>
                </div>
                <button type="button" className="shrink-0 cursor-pointer font-medium text-primary hover:opacity-75" onClick={() => setQuery("将时间范围改为最近 30 天，并按产品类别拆分")}>填写调整条件</button>
              </div>
            )}
            {showExamples && (
              <div className="absolute bottom-full left-0 z-10 mb-2.5 flex gap-2">
                {examples.map((example) => (
                  <button
                    type="button"
                    key={example}
                    onClick={() => submit(example)}
                    disabled={pending || busy || messages == null}
                    className="shrink-0 cursor-pointer rounded-full border border-line bg-surface px-3 py-1.5 text-xs text-ink-2 shadow-[0_4px_12px_rgb(24_34_45/6%)] transition-colors hover:border-line-strong hover:bg-surface-2 disabled:cursor-not-allowed disabled:opacity-55"
                  >
                    {example}
                  </button>
                ))}
              </div>
            )}
            <form
              className="rounded-panel border border-line bg-surface p-3 shadow-card transition focus-within:border-primary/50 focus-within:ring-2 focus-within:ring-primary/15"
              onSubmit={(event) => { event.preventDefault(); submit(); }}
            >
              <textarea
                ref={composerRef}
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="用业务语言描述你想分析的问题…"
                rows={2}
                className="w-full resize-none border-0 bg-transparent text-body text-ink outline-none placeholder:text-faint"
              />
              <div className="flex items-center justify-between pt-1.5">
                <div className="flex items-center gap-2.5">
                  <div className="relative">
                    <button
                      type="button"
                      aria-label="选择分析数据范围"
                      aria-haspopup="listbox"
                      aria-expanded={scopeOpen}
                      onClick={() => setScopeOpen((value) => !value)}
                      className="flex cursor-pointer items-center gap-1 rounded-control px-1.5 py-1 text-xs text-muted transition-colors hover:bg-surface-3 hover:text-ink"
                    >
                      <Icon name="plus" size={16} />
                      {activeSource?.name ?? "选择数据范围"}
                      <Icon name="chevron" size={12} className={cn("transition-transform", scopeOpen && "rotate-180")} />
                    </button>
                    {scopeOpen && (
                      <div
                        role="listbox"
                        aria-label="选择分析数据范围"
                        className="absolute bottom-full left-0 z-20 mb-2 min-w-[250px] rounded-card border border-line bg-surface p-1.5 shadow-card-hover"
                      >
                        <p className="px-2 py-1.5 text-[10px] font-semibold uppercase tracking-[0.12em] text-faint">分析数据范围</p>
                        {availableSources.length ? availableSources.map((source) => (
                          <button
                            key={source.id}
                            type="button"
                            role="option"
                            aria-selected={source.id === activeSource?.id}
                            onClick={() => chooseAnalysisSource(source)}
                            className={cn(
                              "flex w-full cursor-pointer items-center gap-2 rounded-control px-2 py-2 text-left text-xs transition-colors",
                              source.id === activeSource?.id ? "bg-primary-soft text-ink" : "text-muted hover:bg-surface-2 hover:text-ink",
                            )}
                          >
                            <Icon name="database" size={14} className="shrink-0 text-primary" />
                            <span className="min-w-0 flex-1 truncate">{source.name}</span>
                            {source.id === activeSource?.id && <Icon name="check" size={14} className="shrink-0 text-primary" />}
                          </button>
                        )) : (
                          <p className="px-2 py-2 text-xs text-muted">暂无可用数据源</p>
                        )}
                      </div>
                    )}
                  </div>
                  <span className="text-[11px] text-faint">企业业务语义已启用</span>
                </div>
                <button
                  type="submit"
                  disabled={pending || busy || messages == null || !query.trim()}
                  aria-label="开始分析"
                  className="grid h-8 w-8 cursor-pointer place-items-center rounded-control bg-ink text-white transition-colors hover:bg-ink-2 disabled:cursor-not-allowed disabled:opacity-55"
                >
                  <Icon name={pending || busy ? "refresh" : "send"} size={17} />
                </button>
              </div>
            </form>
          </div>
        </div>
      </section>
      {evidenceRun != null && (
        <div
          data-testid="analysis-evidence-shell"
          data-state={evidenceOpen ? "open" : "closed"}
          data-resizing={isEvidenceResizing ? "true" : "false"}
          className="evidence-panel-shell"
          style={{ "--evidence-panel-width": `${evidenceOpen ? effectiveEvidenceWidth : EVIDENCE_PANEL_COLLAPSED_WIDTH}px` }}
        >
          <div className="evidence-panel-shell__rail">
            {evidenceOpen && (
              <div className="evidence-panel__header flex items-center gap-3 border-b border-line px-4 pr-14">
                <div className="min-w-0">
                  <span className="truncate text-[10px] font-semibold text-faint">执行记录已保存</span>
                  <h2 className="mt-0.5 text-[15px] font-semibold tracking-tight text-ink">分析证据链</h2>
                </div>
              </div>
            )}
          <button
            type="button"
            onClick={() => setEvidenceOpen((value) => !value)}
            aria-label={evidenceOpen ? "关闭证据链" : "展开证据链"}
            title={evidenceOpen ? "关闭证据链" : "展开证据链"}
            className={cn(
              "evidence-panel-shell__toggle grid h-8 w-8 cursor-pointer place-items-center rounded-control border text-muted transition-[border-color,background-color,color,transform] duration-200",
              evidenceOpen
                ? "border-transparent hover:-translate-y-px hover:border-line hover:bg-surface-2 hover:text-ink"
                : "border-line bg-surface-2 shadow-card hover:-translate-y-px hover:border-line-strong hover:bg-surface hover:text-ink",
            )}
          >
            <Icon name="panel" size={18} />
          </button>
          </div>
          {evidenceOpen && (
            <div
              role="separator"
              aria-label="调整任务栏宽度"
              aria-orientation="vertical"
              aria-valuenow={Math.round(effectiveEvidenceWidth)}
              aria-valuemin={EVIDENCE_PANEL_MIN_WIDTH}
              aria-valuemax={Math.round(evidenceMaxWidth)}
              onPointerDown={beginEvidenceResize}
              onDoubleClick={() => {
                setEvidenceWidth(EVIDENCE_PANEL_DEFAULT_WIDTH);
                window.localStorage?.setItem(EVIDENCE_PANEL_WIDTH_KEY, String(EVIDENCE_PANEL_DEFAULT_WIDTH));
              }}
              title="拖拽调整宽度，双击恢复默认"
              className="group absolute -left-1 top-0 z-20 h-full w-2 cursor-col-resize touch-none"
            >
              <span className="absolute left-1/2 top-1/2 h-12 w-px -translate-x-1/2 -translate-y-1/2 bg-transparent transition-colors group-hover:bg-primary/50" />
            </div>
          )}
          <EvidencePanel
            run={evidenceRun}
            open={evidenceOpen}
            onBranch={branchFrom}
          />
        </div>
      )}
      <AnalysisAdjustmentPanel
        open={adjustmentTarget != null}
        sourceQuestion={adjustmentTarget?.sourceQuestion ?? ""}
        checkpointLabel={adjustmentTarget?.label ?? "已确认分析条件"}
        submitting={branchSubmitting}
        onClose={() => setAdjustmentTarget(null)}
        onSubmit={submitAdjustment}
      />
    </div>
  );
}

const CHECKPOINT_BUSINESS_LABELS = {
  "intent.plan": "业务问题已理解",
  "context.retrieve": "数据与指标已定位",
  "memory.retrieve": "相关经验已确认",
  "query.validate": "查询逻辑已校验",
  "query.execute": "查询结果已生成",
  "result.explain": "分析结论已生成",
};

const ADJUSTMENT_LABELS = {
  timeRange: "时间",
  objectScope: "对象",
  metric: "指标",
  groupBy: "分组",
  chartType: "图表",
  note: "说明",
};

function checkpointBusinessLabel(stepKey) {
  return CHECKPOINT_BUSINESS_LABELS[stepKey] ?? "已确认分析条件";
}

function BranchOriginCard({ context, originRun, onViewOrigin, onAdjust }) {
  const adjustments = Object.entries(context.adjustments ?? {}).filter(([, value]) => value);
  return (
    <section className="motion-soft-rise mt-5 overflow-hidden rounded-card border border-primary/20 bg-surface">
      <div className="flex items-start gap-3 px-4 py-3.5">
        <span className="mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-control bg-primary-soft text-primary">
          <Icon name="link" size={14} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <strong className="text-xs font-semibold text-ink">基于原分析的新方案</strong>
            <StatusPill tone={context.reuseArtifacts === "snapshot" ? "blue" : "green"}>
              {context.reuseArtifacts === "snapshot" ? "复用结果快照" : "重新查询最新数据"}
            </StatusPill>
          </div>
          <p className="mt-1.5 line-clamp-2 text-xs leading-relaxed text-ink-2">{context.sourceQuestion}</p>
          <span className="mt-1 block text-[10px] text-faint">
            调整起点：{checkpointBusinessLabel(context.checkpoint?.stepKey)}
          </span>
        </div>
        <div className="flex shrink-0 gap-1">
          <button
            type="button"
            onClick={onViewOrigin}
            disabled={!originRun}
            className="cursor-pointer rounded-control px-2 py-1.5 text-xs font-semibold text-muted transition-colors hover:bg-surface-2 hover:text-ink disabled:cursor-not-allowed disabled:opacity-45"
          >
            查看原方案
          </button>
          <button
            type="button"
            onClick={onAdjust}
            className="cursor-pointer rounded-control px-2 py-1.5 text-xs font-semibold text-primary transition-colors hover:bg-primary-soft"
          >
            再次调整
          </button>
        </div>
      </div>
      {adjustments.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5 border-t border-line bg-surface-2 px-4 py-2.5">
          <span className="mr-1 text-[10px] font-semibold text-faint">本方案变化</span>
          {adjustments.map(([key, value]) => (
            <span key={key} className="rounded-full border border-line bg-surface px-2 py-1 text-[10px] text-ink-2">
              <span className="text-faint">{ADJUSTMENT_LABELS[key] ?? key}</span>
              <span className="mx-1 text-line-strong">·</span>
              {String(value)}
            </span>
          ))}
        </div>
      )}
    </section>
  );
}

function BranchComparisonCard({ context, originRun, currentRun }) {
  const originKpis = new Map((originRun.result?.kpis ?? []).map((item) => [item.label, item]));
  const comparisons = (currentRun.result?.kpis ?? [])
    .filter((item) => originKpis.has(item.label))
    .slice(0, 4)
    .map((item) => ({ label: item.label, before: originKpis.get(item.label)?.value, after: item.value }));
  const adjustments = Object.entries(context.adjustments ?? {}).filter(([, value]) => value);
  return (
    <section className="motion-soft-rise mt-5 border-t border-line pt-4">
      <div className="flex items-end justify-between gap-3">
        <div>
          <span className="text-[10px] font-semibold tracking-wide text-primary">方案差异</span>
          <h3 className="mt-1 text-body font-semibold text-ink">原方案与当前方案</h3>
        </div>
        <span className="text-[10px] text-faint">两次分析均独立保留</span>
      </div>
      <div className="mt-3 overflow-hidden rounded-card border border-line">
        <div className="grid grid-cols-[112px_minmax(0,1fr)_minmax(0,1fr)] border-b border-line bg-surface-2 text-[10px] font-semibold text-faint">
          <span className="px-3 py-2">比较项</span>
          <span className="border-l border-line px-3 py-2">原方案</span>
          <span className="border-l border-line px-3 py-2 text-primary">当前方案</span>
        </div>
        {adjustments.map(([key, value]) => (
          <div key={key} className="grid grid-cols-[112px_minmax(0,1fr)_minmax(0,1fr)] border-b border-line text-xs last:border-b-0">
            <span className="px-3 py-2.5 font-semibold text-ink-2">{ADJUSTMENT_LABELS[key] ?? key}</span>
            <span className="border-l border-line px-3 py-2.5 text-muted">沿用原条件</span>
            <span className="border-l border-line px-3 py-2.5 font-semibold text-ink">{String(value)}</span>
          </div>
        ))}
        {comparisons.map((item) => (
          <div key={item.label} className="grid grid-cols-[112px_minmax(0,1fr)_minmax(0,1fr)] border-b border-line text-xs last:border-b-0">
            <span className="px-3 py-2.5 font-semibold text-ink-2">{item.label}</span>
            <span className="border-l border-line px-3 py-2.5 font-mono text-muted">{String(item.before ?? "—")}</span>
            <span className="border-l border-line px-3 py-2.5 font-mono font-semibold text-ink">{String(item.after ?? "—")}</span>
          </div>
        ))}
        {adjustments.length === 0 && comparisons.length === 0 && (
          <p className="px-3 py-3 text-xs text-muted">当前方案已完成；两个方案没有可直接对齐的指标，详细结果分别保留在各自证据链中。</p>
        )}
      </div>
    </section>
  );
}

function UserMessage({ text }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard?.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1400);
    } catch {
      setCopied(false);
    }
  };
  return (
    <div className="group relative motion-soft-rise mt-7 ml-auto w-fit max-w-[86%]">
      <p className="user-message__bubble inline-block max-w-full rounded-[18px] px-4 py-2.5 text-body leading-relaxed text-ink">{text}</p>
      <div className="absolute right-0 top-full z-10 mt-1 flex opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
        <button type="button" onClick={copy} aria-label="复制问题" className="grid h-5 w-5 cursor-pointer place-items-center rounded-tag text-faint transition-colors hover:bg-surface-3 hover:text-ink">
          <Icon name={copied ? "check" : "copy"} size={13} />
        </button>
      </div>
    </div>
  );
}

function AssistantRunMessage({ run, animate, onComplete, onOpenEvidence, onClarify, onFollowUp, onOpenModeling, clarifySubmitting = false, clarifyError = null }) {
  const hasEvidence = Boolean(run.plan?.length || run.artifacts?.length || run.checkpoints?.length);
  const openEvidence = hasEvidence ? () => onOpenEvidence(run) : null;
  if (run.status === "blocked") return <BlockedCard error={run.error} onOpenEvidence={openEvidence} />;
  if (run.status === "clarifying" && run.clarification) return <ClarifyCard clarification={run.clarification} onSelect={onClarify} submitting={clarifySubmitting} error={clarifyError} />;
  if (run.status === "failed" || run.status === "cancelled") return <FailedRunCard run={run} onOpenEvidence={openEvidence} />;
  if (run.status === "modeling-proposal" && run.proposal) return <ModelingProposalCard proposal={run.proposal} runId={run.id} onOpenModeling={onOpenModeling} />;
  // 澄清/建模提案在缺少 interaction/proposal 数据（如占位快照或异常快照）时按“运行中”兜底，避免空数据卡片抛错。
  if (["queued", "running", "retrying", "clarifying", "modeling-proposal"].includes(run.status)) {
    const livePlan = run.plan?.length ? run.plan : [{ id: "accepted", label: "正在理解业务问题", caption: "任务已进入可靠执行队列" }];
    const completed = livePlan.filter((step) => step.status === "succeeded").length;
    return <RunningAnalysis completed={Math.min(completed, livePlan.length - 1)} plan={livePlan} />;
  }
  if (run.result?.kind === "conversation") return <AssistantConversationMessage text={run.conclusion.text} />;
  return <RunFlow run={run} animate={animate} onComplete={onComplete} onOpenEvidence={onOpenEvidence} onFollowUp={onFollowUp} />;
}

function AssistantConversationMessage({ text }) {
  return (
    <div className="motion-soft-rise mt-2 max-w-[82%]">
      <p className="rounded-[18px] rounded-tl-[6px] bg-surface-2 px-4 py-3 text-body leading-6 text-ink">{text}</p>
    </div>
  );
}

function RunFlow({ run, animate, onComplete, onOpenEvidence, onFollowUp }) {
  const plan = run.plan ?? [];
  const [completed, setCompleted] = useState(animate ? 0 : plan.length);
  const doneRef = useRef(!animate);

  useEffect(() => {
    if (!animate) return undefined;
    const timer = window.setInterval(() => {
      setCompleted((current) => {
        const next = Math.min(current + 1, plan.length);
        if (next === plan.length) window.clearInterval(timer);
        return next;
      });
    }, 430);
    return () => window.clearInterval(timer);
  }, [animate, plan.length]);

  useEffect(() => {
    if (completed === plan.length && !doneRef.current) {
      doneRef.current = true;
      onComplete(run);
    }
  }, [completed, plan.length, onComplete, run]);

  if (completed < plan.length) return <RunningAnalysis completed={completed} plan={plan} />;
  return <AnalysisResult run={run} onOpenEvidence={() => onOpenEvidence(run)} onFollowUp={onFollowUp} />;
}

function EvidenceReviewAction({ onOpenEvidence }) {
  if (!onOpenEvidence) return null;
  return (
    <div className="mt-3 flex justify-end border-t border-current/10 pt-3">
      <button
        type="button"
        onClick={onOpenEvidence}
        className="inline-flex cursor-pointer items-center gap-1.5 rounded-control px-2 py-1 text-xs font-semibold text-primary transition-colors hover:bg-surface/65 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/25"
      >
        <Icon name="panel" size={14} />查看证据链
      </button>
    </div>
  );
}

function BlockedCard({ error, onOpenEvidence }) {
  return (
    <div className="mt-1.5 rounded-card border border-red/25 bg-red-soft p-4">
      <div className="flex items-center gap-2">
        <span className="text-red"><Icon name="shield" size={16} /></span>
        <strong className="text-body font-semibold text-red">已拦截本次请求</strong>
        <StatusPill tone="red">安全校验</StatusPill>
      </div>
      <p className="mt-2 text-body leading-relaxed text-ink">{error.message}</p>
      <p className="mt-1.5 text-xs leading-relaxed text-muted">{error.suggestion}</p>
      <EvidenceReviewAction onOpenEvidence={onOpenEvidence} />
    </div>
  );
}

function ClarifyCard({ clarification, onSelect, submitting = false, error = null }) {
  const [chosen, setChosen] = useState(null);
  const [answers, setAnswers] = useState({});
  const [customFields, setCustomFields] = useState({});
  const fields = clarification.fields ?? [];

  useEffect(() => {
    setChosen(null);
    setAnswers({});
    setCustomFields({});
  }, [clarification.interactionId]);

  const choose = (option) => {
    if (submitting) return;
    setChosen(option);
    onSelect(option, clarification.interactionId);
  };
  const setField = (fieldId, value) => setAnswers((current) => ({ ...current, [fieldId]: value }));
  const chooseFieldOption = (fieldId, value) => {
    setCustomFields((current) => ({ ...current, [fieldId]: false }));
    setField(fieldId, value);
  };
  const enableCustomField = (fieldId) => {
    setCustomFields((current) => ({ ...current, [fieldId]: true }));
    setField(fieldId, "");
  };
  const canSubmitFields = fields.length > 0 && fields.every((field) => String(answers[field.id] ?? "").trim());
  const submitFields = () => {
    if (!canSubmitFields || submitting) return;
    setChosen({ label: "已确认", value: answers });
    onSelect(answers, clarification.interactionId);
  };
  return (
    <div className="motion-soft-rise mt-5 max-w-[420px]">
      <p className="text-body leading-relaxed text-ink">{clarification.question}</p>
      {fields.length > 0 ? (
        <div className="mt-3">
          {fields.map((field, index) => (
            <section key={field.id} className={cn(index > 0 && "mt-4 border-t border-line pt-4")}>
              <div className="flex items-baseline gap-2">
                <strong className="text-xs font-semibold text-ink">{field.label}</strong>
                <span className="text-[11px] text-muted">{field.question}</span>
              </div>
              <div className="mt-2.5 w-full max-w-[330px] overflow-hidden rounded-control border border-line bg-surface p-1">
                {field.options.map((option) => {
                  const selected = !customFields[field.id] && answers[field.id] === option.value;
                  return (
                    <button
                      type="button"
                      key={option.value}
                      onClick={() => chooseFieldOption(field.id, option.value)}
                      disabled={submitting}
                      className={cn("flex w-full cursor-pointer items-center gap-2.5 rounded-tag px-2.5 py-2 text-left text-xs transition-colors disabled:cursor-not-allowed", selected ? "bg-primary-soft text-primary" : "text-ink-2 hover:bg-surface-2 disabled:opacity-55")}
                    >
                      <span className={cn("grid h-4 w-4 shrink-0 place-items-center rounded-[3px] border", selected ? "border-primary bg-primary text-white" : "border-line-strong bg-surface text-transparent")}><Icon name="check" size={11} strokeWidth={2.4} /></span>
                      <span className={cn(selected && "font-semibold")}>{option.label}</span>
                    </button>
                  );
                })}
                {field.allowCustom && (
                  <div>
                    <button
                      type="button"
                      onClick={() => enableCustomField(field.id)}
                      disabled={submitting}
                      className={cn("flex w-full cursor-pointer items-center gap-2.5 rounded-tag px-2.5 py-2 text-left text-xs transition-colors disabled:cursor-not-allowed", customFields[field.id] ? "bg-primary-soft text-primary" : "text-ink-2 hover:bg-surface-2 disabled:opacity-55")}
                    >
                      <span className={cn("grid h-4 w-4 shrink-0 place-items-center rounded-[3px] border", customFields[field.id] ? "border-primary bg-primary text-white" : "border-line-strong bg-surface text-transparent")}><Icon name="check" size={11} strokeWidth={2.4} /></span>
                      <span className={cn(customFields[field.id] && "font-semibold")}>自定义{field.label}</span>
                    </button>
                    {customFields[field.id] && (
                      <div className="px-2.5 pb-2 pt-1">
                        <input
                          autoFocus
                          value={answers[field.id] ?? ""}
                          onChange={(event) => setField(field.id, event.target.value)}
                          disabled={submitting}
                          placeholder={`输入${field.label}`}
                          className="h-8 w-full rounded-tag border border-line bg-surface px-2.5 text-xs text-ink outline-none transition placeholder:text-faint focus:border-primary/40 focus:ring-2 focus:ring-primary/10 disabled:opacity-55"
                        />
                      </div>
                    )}
                  </div>
                )}
              </div>
            </section>
          ))}
          <div className="mt-3 flex justify-end">
            <Button size="sm" onClick={submitFields} disabled={!canSubmitFields || submitting}>确认继续 <Icon name="arrow" size={14} /></Button>
          </div>
        </div>
      ) : (
        <div className="mt-3 flex flex-wrap gap-2">
          {clarification.options.map((option) => (
          <button
            type="button"
            key={option.value}
            onClick={() => choose(option)}
            disabled={submitting}
            className={cn(
              "cursor-pointer rounded-full border px-3 py-1.5 text-xs transition-colors disabled:cursor-not-allowed",
              chosen?.value === option.value
                ? "border-primary/25 bg-primary-soft font-semibold text-primary"
                : "border-line bg-surface text-ink-2 hover:border-line-strong hover:bg-surface-2 disabled:opacity-55",
            )}
          >
            {option.label}
          </button>
          ))}
        </div>
      )}
      {error && <p className="mt-2.5 text-[11px] leading-relaxed text-red">{error}</p>}
      {chosen && !error && <p className="mt-2.5 text-[11px] text-faint">已选择「{chosen.label}」，正在按该口径继续分析…</p>}
    </div>
  );
}

function FailedRunCard({ run, onOpenEvidence }) {
  const isBusinessLimitation = ["capability_missing", "entity_missing", "capability_and_entity_missing", "semantic_resource_missing"]
    .includes(run.error?.reason);
  return (
    <div className={cn(
      "mt-1.5 rounded-card border p-4",
      isBusinessLimitation ? "border-amber/25 bg-amber-soft" : "border-red/25 bg-red-soft",
    )}>
      <div className="flex items-center gap-2">
        <span className={isBusinessLimitation ? "text-amber" : "text-red"}><Icon name="warning" size={16} /></span>
        <strong className={cn("text-body font-semibold", isBusinessLimitation ? "text-ink" : "text-red")}>
          {run.status === "cancelled" ? "分析已取消" : run.error?.title || "分析未完成"}
        </strong>
        {run.error?.showCodeToUser && run.error?.code && <StatusPill tone="red">{run.error.code}</StatusPill>}
      </div>
      <p className="mt-2 text-body leading-relaxed text-ink">{run.error?.message}</p>
      <p className="mt-1.5 text-xs leading-relaxed text-muted">{run.error?.suggestion}</p>
      <EvidenceReviewAction onOpenEvidence={onOpenEvidence} />
    </div>
  );
}

function ModelingProposalCard({ proposal, runId, onOpenModeling }) {
  const [confirmed, setConfirmed] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [createError, setCreateError] = useState(null);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const reusableModel = proposal.recommendedAction === "reuse" ? proposal.reusableModel : null;

  const confirm = async () => {
    if (confirming) return;
    setConfirming(true);
    setCreateError(null);
    try {
      const task = await createModelingTask({
        title: proposal.title,
        goal: proposal.goal,
        taskType: proposal.taskType,
        datasetName: proposal.dataset,
        datasetSql: proposal.datasetSql,
        targetField: proposal.target,
        targetLabel: proposal.targetLabel,
        features: proposal.features ?? [],
        candidates: proposal.candidates ?? [],
        runId,
        sourceId: 1,
      });
      window.sessionStorage.setItem(MODELING_DRAFT_KEY, JSON.stringify({ taskId: task.id }));
      setConfirmed(true);
    } catch (error) {
      setCreateError(error.message || "创建建模任务失败，请重试。");
    } finally {
      setConfirming(false);
    }
  };

  return (
    <Card className="motion-soft-rise mt-1.5 overflow-hidden">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-line px-4 py-3.5">
        <div className="flex min-w-0 items-start gap-2.5">
          <span className="grid h-8 w-8 shrink-0 place-items-center rounded-control bg-primary-soft text-primary"><Icon name="model" size={16} /></span>
          <div className="min-w-0"><span className="text-[10px] font-semibold tracking-wide text-faint">AGENT 建模方案</span><h3 className="mt-0.5 truncate text-body font-semibold text-ink">{proposal.title}</h3></div>
        </div>
        <StatusPill tone={reusableModel || confirmed ? "green" : "amber"}>{reusableModel ? "生产模型可用" : confirmed ? "已进入任务台" : "等待确认"}</StatusPill>
      </div>
      <div className="px-4 py-3.5">
        <p className="text-body leading-relaxed text-ink">{reusableModel ? <>已发现同一数据源中用途相近的<strong className="font-semibold">已发布模型</strong>，优先复用可以避免重复训练。</> : <>我将这个问题识别为<strong className="font-semibold">{proposal.taskTypeLabel || proposal.taskType}</strong>任务。系统会先形成可复核方案，不会在你不知情的情况下直接训练。</>}</p>
        <div className="mt-3 grid grid-cols-2 gap-px overflow-hidden rounded-card border border-line bg-line sm:grid-cols-4">
          {reusableModel ? <>
            <ProposalField label="生产模型" value={reusableModel.name} />
            <ProposalField label="算法与版本" value={`${reusableModel.algorithm} · v${reusableModel.version}`} />
            <ProposalField label="预测目标" value={reusableModel.targetLabel || reusableModel.targetField} mono />
            <ProposalField label="最近评估" value={reusableModel.metric || "已通过发布复核"} />
          </> : <>
            <ProposalField label="建模目标" value={proposal.goal} />
            <ProposalField label="训练数据" value={`${proposal.dataset} · ${proposal.sampleCount} 条`} />
            <ProposalField label="预测目标" value={proposal.target} mono />
            <ProposalField label="评估重点" value={proposal.metric} />
          </>}
        </div>
        {!reusableModel && <div className="mt-3 flex flex-wrap items-center gap-1.5"><span className="mr-1 text-[10px] text-faint">候选模型</span>{(proposal.candidates ?? []).map((candidate) => <span key={candidate} className="rounded-full border border-line bg-surface-2 px-2 py-1 text-[10px] text-ink-2">{candidate}</span>)}</div>}
        {detailsOpen && <div className="mt-3 flex items-start gap-2 rounded-control border border-teal/25 bg-teal-soft px-3 py-2.5 text-[11px] leading-relaxed text-muted"><span className="mt-0.5 text-teal"><Icon name="shield" size={14} /></span><span>开始训练前将检查缺失值、类别比例、时间穿越和结果泄漏字段；训练代码在隔离 Python 环境运行，数据源保持只读。</span></div>}
        <div className="mt-3.5 flex flex-wrap justify-end gap-2">
          <Button variant="ghost" onClick={() => setDetailsOpen((value) => !value)}>{detailsOpen ? "收起检查项" : "查看检查项"}</Button>
          {reusableModel ? <Button variant="primary" onClick={onOpenModeling}>查看生产模型 <Icon name="arrow" size={14} /></Button> : !confirmed ? <><Button variant="secondary" onClick={onOpenModeling}>调整方案</Button><Button variant="primary" onClick={confirm} disabled={confirming}><Icon name="play" size={14} />{confirming ? "创建中…" : "确认并创建任务"}</Button></> : <Button variant="primary" onClick={onOpenModeling}>在智能建模中查看 <Icon name="arrow" size={14} /></Button>}
        </div>
        {createError && <p className="mt-2 text-right text-[11px] leading-relaxed text-red">{createError}</p>}
      </div>
    </Card>
  );
}

function ProposalField({ label, value, mono = false }) {
  return <div className="min-w-0 bg-surface p-2.5"><span className="block text-[10px] text-faint">{label}</span><strong className={cn("mt-1 block text-[11px] font-semibold leading-relaxed text-ink", mono && "font-mono")}>{value}</strong></div>;
}

function AnalysisEmptyState({ onPrompt, onNavigateModeling, onNavigateReports, onNavigateProfile }) {
  const guides = [
    {
      id: "analysis",
      icon: "analysis",
      tone: "blue",
      eyebrow: "START WITH A QUESTION",
      title: "分析引导",
      description: "从产量、质量或设备问题开始",
      action: "试问一个问题",
      onClick: () => onPrompt("统计每条产线最近 7 天的产量趋势"),
    },
    {
      id: "modeling",
      icon: "model",
      tone: "violet",
      eyebrow: "FIND THE NEXT SIGNAL",
      title: "建模引导",
      description: "识别风险因素，生成可复核方案",
      action: "查看建模工作台",
      onClick: onNavigateModeling,
    },
    {
      id: "report",
      icon: "report",
      tone: "amber",
      eyebrow: "SHARE WHAT IS VERIFIED",
      title: "周报引导",
      description: "把已验证结论整理成周报与交接",
      action: "查看周报与交接",
      onClick: onNavigateReports,
    },
    {
      id: "binding",
      icon: "link",
      tone: "teal",
      eyebrow: "KEEP THE TEAM IN SYNC",
      title: "飞书 / 钉钉绑定",
      description: "接收分析提醒和报告投递",
      action: "去绑定账号",
      onClick: onNavigateProfile,
    },
  ];

  return (
    <section className="analysis-empty-state" aria-labelledby="analysis-empty-title">
      <div className="analysis-empty-hero">
        <div className="analysis-empty-logo-wrap" aria-hidden="true">
          <span className="analysis-empty-logo-ring analysis-empty-logo-ring--outer" />
          <span className="analysis-empty-logo-ring analysis-empty-logo-ring--inner" />
          <img src={forgeSightLogo} alt="" className="analysis-empty-logo" />
        </div>
        <div className="analysis-empty-copy">
          <span className="analysis-empty-kicker">FORGESIGHT / START HERE</span>
          <h2 id="analysis-empty-title">从一个问题开始，进入你的数据现场</h2>
          <p>用业务语言描述现场疑问，ForgeSight 会把方向、证据和下一步整理在一起。</p>
        </div>
      </div>
      <div className="analysis-guide-grid">
        {guides.map((guide) => (
          <button type="button" key={guide.id} className={`analysis-guide-card analysis-guide-card--${guide.tone}`} onClick={guide.onClick}>
            <span className="analysis-guide-card__icon"><Icon name={guide.icon} size={17} /></span>
            <span className="analysis-guide-card__body">
              <span className="analysis-guide-card__eyebrow">{guide.eyebrow}</span>
              <strong>{guide.title}</strong>
              <span>{guide.description}</span>
            </span>
            <span className="analysis-guide-card__action">{guide.action}<Icon name="arrow" size={13} /></span>
          </button>
        ))}
      </div>
      <p className="analysis-empty-hint"><span className="analysis-empty-hint__dot" />也可以直接在下方输入问题，先从一个具体现场开始。</p>
    </section>
  );
}

function ConversationSkeleton() {
  return (
    <div className="mt-7 grid animate-pulse gap-7" role="status" aria-label="会话加载中">
      <div className="grid grid-cols-[32px_minmax(0,1fr)] gap-3">
        <i className="h-8 w-8 rounded-control bg-surface-3" />
        <div className="grid content-start gap-2">
          <i className="h-2 w-16 rounded-full bg-surface-3" />
          <i className="h-4 w-3/5 rounded-full bg-surface-3" />
        </div>
      </div>
      <div className="grid grid-cols-[32px_minmax(0,1fr)] gap-3">
        <i className="h-8 w-8 rounded-control bg-surface-3" />
        <div className="grid content-start gap-2">
          <i className="h-2 w-16 rounded-full bg-surface-3" />
          <i className="h-4 w-full rounded-full bg-surface-3" />
          <i className="h-4 w-4/5 rounded-full bg-surface-3" />
          <i className="mt-2 h-52 rounded-card bg-surface-3" />
        </div>
      </div>
    </div>
  );
}

function RunningAnalysis({ completed, plan }) {
  const current = plan[Math.min(completed, plan.length - 1)];
  return (
    <Card className="motion-soft-rise mt-1.5 p-4">
      <div className="flex items-center gap-3">
        <div className="flex shrink-0 items-center gap-1">
          {[0, 1, 2].map((dot) => (
            <span key={dot} className="h-1.5 w-1.5 animate-pulse rounded-full bg-primary" style={{ animationDelay: `${dot * 160}ms` }} />
          ))}
        </div>
        <div className="min-w-0 flex-1">
          <strong className="block text-body font-semibold text-ink">{current?.label ?? "正在准备分析"}</strong>
          <p className="mt-0.5 text-xs text-muted">{current?.caption ?? "正在读取业务上下文"}</p>
        </div>
        <span className="shrink-0 font-mono text-xs text-faint">{completed}/{plan.length}</span>
      </div>
      <div className="mt-3 h-1 overflow-hidden rounded-full bg-surface-3">
        <span className="block h-full rounded-full bg-primary transition-[width] duration-300" style={{ width: `${completed / plan.length * 100}%` }} />
      </div>
      <div className="mt-3 flex flex-wrap gap-1.5">
        {plan.map((step, index) => (
          <span
            key={step.id}
            className={cn(
              "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px]",
              index < completed ? "border-green/25 bg-green/8 text-green" : index === completed ? "border-primary/25 bg-primary-soft text-primary" : "border-line text-faint",
            )}
          >
            <Icon name={index < completed ? "check" : "clock"} size={12} />{step.label}
          </span>
        ))}
      </div>
    </Card>
  );
}

function formatDuration(value) {
  const milliseconds = Number(value);
  if (!Number.isFinite(milliseconds) || milliseconds < 0) return "未记录";
  if (milliseconds < 1) return "<1 毫秒";
  if (milliseconds < 1000) return `${Math.round(milliseconds)} 毫秒`;
  if (milliseconds < 10000) return `${(milliseconds / 1000).toFixed(1)} 秒`;
  if (milliseconds < 60000) return `${Math.round(milliseconds / 1000)} 秒`;
  const totalSeconds = Math.round(milliseconds / 1000);
  return `${Math.floor(totalSeconds / 60)} 分 ${totalSeconds % 60} 秒`;
}

function AnalysisResult({ run, onOpenEvidence, onFollowUp }) {
  const [showSql, setShowSql] = useState(false);
  const [copied, setCopied] = useState(false);
  const [exported, setExported] = useState(false);
  const [feedback, setFeedback] = useState("");
  const [feedbackBusy, setFeedbackBusy] = useState(false);
  const chart = run.chart;
  const tableRequested = run.tasks?.some((task) => (task.requestedChart ?? task.intent?.requestedChart) === "table");
  const [showTable, setShowTable] = useState(tableRequested || !chart);
  const sqlCode = run.planDetails?.query?.code;
  const chartTypeName = {
    line: "折线图",
    bar: "柱状图",
    donut: "环形图",
    scatter: "散点图",
    combo: "组合图",
    heatmap: "热力图",
  }[chart?.type] ?? "图表";
  const showBusinessKpis = run.result?.kpis?.length > 0;
  const legendItems = chart?.legend
    ?? (chart?.series ?? [...(chart?.bars ?? []), ...(chart?.lines ?? [])]).map((item) => ({ label: item.name, color: item.color }));
  const analysisDuration = run.execution.processingDurationMs ?? run.execution.durationMs;
  const confidence = run.conclusion?.confidence;
  const metricResolution = run.conclusion?.metricResolution;
  const inferredMetric = metricResolution?.source === "agent_inferred";
  const emptyResult = Number(run.execution.rowCount ?? 0) === 0;
  const copySql = async () => {
    if (!sqlCode) return;
    try {
      await navigator.clipboard?.writeText(sqlCode);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };
  const exportCsv = () => {
    const { columns = [], rows = [] } = run.execution ?? {};
    if (!columns.length) return;
    const quote = (value) => `"${String(value ?? "").replaceAll('"', '""')}"`;
    const csv = [columns, ...rows].map((row) => row.map(quote).join(",")).join("\n");
    const blob = new Blob([`\uFEFF${csv}`], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `ForgeSight-${run.id}.csv`;
    link.click();
    URL.revokeObjectURL(url);
    setExported(true);
  };
  const sendFeedback = async (rating) => {
    setFeedbackBusy(true);
    try {
      await submitSkillFeedback(run.id, rating);
      setFeedback(rating);
    } finally {
      setFeedbackBusy(false);
    }
  };
  return (
    <article className="motion-soft-rise mt-1.5 overflow-hidden rounded-panel border border-line bg-surface shadow-card">
      <header className="border-b border-line px-5 py-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex min-w-0 items-center gap-2.5">
            <span className="grid h-7 w-7 shrink-0 place-items-center rounded-tag bg-green/10 text-green"><Icon name="check" size={15} strokeWidth={2.2} /></span>
            <div><span className="block text-[10px] font-semibold tracking-wide text-faint">分析完成</span><h3 className="mt-0.5 text-body font-semibold text-ink">分析结果</h3></div>
          </div>
          <span className="text-[11px] text-faint">{emptyResult ? "当前范围无记录" : `${run.execution.rowCount} 条记录`} · 分析用时 {formatDuration(analysisDuration)}</span>
        </div>
        <p className="mt-3 max-w-[720px] break-words text-body leading-relaxed text-ink-2">{run.conclusion.text}</p>
        {emptyResult && (
          <div className="mt-3 flex items-start gap-2 rounded-control border border-amber/25 bg-amber-soft px-3 py-2.5 text-xs leading-relaxed text-amber">
            <Icon name="help" size={14} className="mt-0.5 shrink-0" />
            <span>查询已正常完成，但当前筛选范围没有匹配记录；系统不会据此生成数值性判断。</span>
          </div>
        )}
        {confidence && (
          <div className={cn(
            "mt-3 rounded-control border px-3.5 py-3",
            confidence.level === "high"
              ? "border-green/25 bg-green/5"
              : confidence.level === "medium"
                ? "border-amber/30 bg-amber/5"
                : "border-red/25 bg-red/5",
          )}>
            <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
              <span className={cn(
                "rounded-full px-2 py-0.5 text-[11px] font-semibold",
                confidence.level === "high"
                  ? "bg-green/10 text-green"
                  : confidence.level === "medium"
                    ? "bg-amber/10 text-amber"
                    : "bg-red/10 text-red",
              )}>
                {emptyResult ? "空结果已验证" : confidence.label} · {confidence.score}%
              </span>
              <strong className="text-xs font-semibold text-ink">
                {inferredMetric ? "Agent 临时推导口径" : "企业已发布口径"}
              </strong>
              {inferredMetric && <span className="text-[11px] text-faint">仅对本次分析有效</span>}
            </div>
            {metricResolution && (
              <div className="mt-2 grid gap-1 text-[11px] leading-relaxed text-ink-2">
                <p>{metricResolution.definition}</p>
                <p><span className="text-faint">计算公式：</span><code className="font-mono text-ink">{metricResolution.formula}</code></p>
                {metricResolution.assumptions?.length > 0 && (
                  <p><span className="text-faint">关键假设：</span>{metricResolution.assumptions.join("；")}</p>
                )}
              </div>
            )}
            {confidence.completenessIssues?.length > 0 && (
              <div className="mt-2 flex items-start gap-1.5 border-t border-red/15 pt-2 text-[11px] leading-relaxed text-red">
                <Icon name="alert" size={13} className="mt-0.5 shrink-0" />
                <span>{confidence.completenessIssues.join("；")}</span>
              </div>
            )}
          </div>
        )}
      </header>
      {showBusinessKpis && <div className="grid grid-cols-1 divide-y divide-line bg-surface-2/45 sm:grid-cols-3 sm:divide-x sm:divide-y-0">
        {run.result.kpis.map((kpi) => (
          <Finding key={kpi.label} label={kpi.label} value={kpi.value} caption={kpi.caption} captionTone={kpi.tone === "red" ? "text-red" : "text-faint"} />
        ))}
      </div>}
      {chart && (
        <section className="border-t border-line">
          <div className="flex flex-wrap items-center justify-between gap-3 px-5 pb-1 pt-4">
            <div className="min-w-0">
              <span className="text-[11px] font-semibold tracking-wide text-faint">{chart.kicker}</span>
              <h3 className="mt-0.5 truncate text-body font-semibold text-ink">{chart.title}</h3>
            </div>
            <div className="flex shrink-0 items-center gap-1">
              {sqlCode && (
                <button
                  type="button"
                  onClick={() => setShowSql((value) => !value)}
                  className={cn(
                    "flex cursor-pointer items-center gap-1 rounded-control px-2 py-1.5 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/25",
                    showSql ? "bg-surface-3 text-ink" : "text-muted hover:bg-surface-3 hover:text-ink",
                  )}
                >
                  <Icon name="code" size={14} />{showSql ? "收起 SQL" : "查看 SQL"}
                </button>
              )}
            </div>
          </div>
          {showSql && sqlCode && (
            <div className="mx-5 mt-3 rounded-control bg-ink">
              <div className="flex items-center justify-between px-4 pt-3">
                <span className="text-[11px] text-white/60">实际执行 SQL</span>
                <StatusPill tone="green">只读校验通过</StatusPill>
              </div>
              <pre className="m-0 overflow-auto px-4 py-3 font-mono text-[11px] leading-relaxed text-white/90"><code>{sqlCode}</code></pre>
              <footer className="flex items-center justify-between border-t border-white/10 px-4 py-2.5 text-[11px] text-white/60">
                <span>{run.execution.engine} · 查询 {formatDuration(run.execution.durationMs)} · 返回 {run.execution.rowCount} 行</span>
                <button type="button" onClick={copySql} className="cursor-pointer rounded-tag font-medium text-white/80 transition-colors hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/35">{copied ? "已复制" : "复制 SQL"}</button>
              </footer>
            </div>
          )}
          <div className="px-5 pb-2 pt-3" aria-label={`${chart.title}${chartTypeName}`}>
            {chart.type === "line" ? (
              <TrendLineChart data={chart.data} xKey={chart.xKey} series={chart.series} unit={chart.unit} yDomain={chart.yDomain} yTickFormatter={chart.yTickFormatter} xTickFormatter={chart.xTickFormatter} height={250} />
            ) : chart.type === "donut" ? (
              <div className="chart-frame"><div className="chart-frame__inner"><ShareDonutChart data={chart.data} unit={chart.unit} centerTitle={chart.centerTitle} centerValue={chart.centerValue} height={250} /></div></div>
            ) : chart.type === "scatter" ? (
              <div className="chart-frame"><div className="chart-frame__inner"><CorrelationScatterChart data={chart.data} xKey={chart.xNumericKey} yKey={chart.yNumericKey} xName={chart.xName} yName={chart.yName} height={250} /></div></div>
            ) : chart.type === "combo" ? (
              <ComboMetricChart data={chart.data} xKey={chart.xKey} bars={chart.bars} lines={chart.lines} leftUnit={chart.leftUnit} rightUnit={chart.rightUnit} xTickFormatter={chart.xTickFormatter} height={250} />
            ) : chart.type === "heatmap" ? (
              <MatrixHeatmapChart rows={chart.rows} columns={chart.columns} cells={chart.cells} rowName={chart.rowName} columnName={chart.columnName} valueName={chart.valueName} />
            ) : (
              <RankBarChart data={chart.data} xKey={chart.xKey} bars={chart.bars} lines={chart.lines ?? []} unit={chart.unit} yDomain={chart.yDomain} yTickFormatter={chart.yTickFormatter} xTickFormatter={chart.xTickFormatter} height={250} />
            )}
          </div>
          <div className="px-5 pb-4">
            {legendItems.length > 0 && <ChartLegend items={legendItems} />}
            <p className="mt-2 text-[11px] text-faint">{chart.note}</p>
          </div>
        </section>
      )}
      {run.execution?.columns?.length > 0 && (
        <section className="border-t border-line">
          <button
            type="button"
            onClick={() => setShowTable((value) => !value)}
            className="flex w-full cursor-pointer items-center justify-between gap-3 px-5 py-3.5 text-left transition-colors hover:bg-surface-2/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary/25"
            aria-expanded={showTable}
          >
            <span>
              <strong className="block text-xs font-semibold text-ink">查询结果明细</strong>
              <span className="mt-0.5 block text-[11px] text-faint">
                {run.execution.rowCount} 条记录 · 页面预览前 20 条
              </span>
            </span>
            <span className="flex items-center gap-1.5 text-xs font-medium text-primary">
              {showTable ? "收起" : "查看数据"}
              <Icon name="chevron" size={14} className={cn("transition-transform", showTable && "rotate-90")} />
            </span>
          </button>
          {showTable && (
            <ResultDataTable columns={run.execution.columns} rows={run.execution.rows} />
          )}
        </section>
      )}
      {run.conclusion.highlights.length > 0 && <section className="border-t border-line px-5 py-4">
        <div className="flex items-center gap-2"><Icon name="insight" size={16} className="text-primary" /><h4 className="text-body font-semibold text-ink">关键发现</h4></div>
        <div className="mt-2.5 grid gap-2.5 border-l-2 border-primary/20 pl-3">
          <ol className="mt-1.5 grid list-decimal gap-1.5 pl-4 text-body leading-relaxed text-ink-2">
            {run.conclusion.highlights.map((highlight) => (
              <li key={highlight.title}><strong className="font-semibold text-ink">{highlight.title}：</strong>{highlight.text}</li>
            ))}
          </ol>
          {run.conclusion.followUp && (
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <span className="text-[11px] text-faint">建议继续追问</span>
              <button
                type="button"
                onClick={() => onFollowUp(run.conclusion.followUp)}
                className="flex cursor-pointer items-center gap-1 text-xs font-medium text-primary transition-opacity hover:opacity-75"
              >
                {run.conclusion.followUp} <Icon name="arrow" size={13} />
              </button>
            </div>
          )}
        </div>
      </section>}
      <footer className="flex flex-wrap items-center gap-x-4 gap-y-2 border-t border-line bg-surface-2/45 px-5 py-3 text-xs text-ink-2">
        {chart && <span className="inline-flex items-center gap-1"><Icon name="chart" size={14} />{chartTypeName}</span>}
        {run.execution?.columns?.length > 0 && <span className="inline-flex items-center gap-1"><Icon name="database" size={14} />结果表</span>}
        {sqlCode && <span className="inline-flex items-center gap-1"><Icon name="code" size={14} />SQL</span>}
        <span className="inline-flex items-center gap-1 text-teal"><Icon name="shield" size={14} />结果已校验</span>
        <span className="ml-auto text-[11px] text-faint">这次分析有帮助吗？</span>
        <button type="button" disabled={feedbackBusy || Boolean(feedback)} onClick={() => sendFeedback("helpful")} className="cursor-pointer font-medium text-muted hover:text-green disabled:cursor-default disabled:opacity-60">{feedback === "helpful" ? "已反馈有帮助" : "有帮助"}</button>
        <button type="button" disabled={feedbackBusy || Boolean(feedback)} onClick={() => sendFeedback("needs_improvement")} className="cursor-pointer font-medium text-muted hover:text-red disabled:cursor-default disabled:opacity-60">{feedback === "needs_improvement" ? "已记录" : "需改进"}</button>
        <button type="button" onClick={exportCsv} disabled={!run.execution?.columns?.length} className="flex cursor-pointer items-center gap-1 font-medium text-primary transition-opacity hover:opacity-75 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/25 disabled:cursor-not-allowed disabled:opacity-45">
          {exported ? "结果已导出" : "导出完整结果"}<Icon name="arrow" size={13} />
        </button>
        <button type="button" onClick={onOpenEvidence} className="shrink-0 cursor-pointer font-semibold text-primary underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/25">查看证据链</button>
      </footer>
    </article>
  );
}

function ResultDataTable({ columns, rows }) {
  const preview = (rows ?? []).slice(0, 20);
  return (
    <div className="border-t border-line bg-surface-2/35 px-5 py-4">
      <div className="overflow-x-auto rounded-control border border-line bg-surface">
        <table className="w-full min-w-max border-collapse text-left text-xs">
          <thead className="bg-surface-2">
            <tr>
              {columns.map((column) => (
                <th key={column} scope="col" className="whitespace-nowrap border-b border-line px-3 py-2.5 font-semibold text-ink-2">
                  {column}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {preview.map((row, rowIndex) => {
              const values = Array.isArray(row) ? row : columns.map((column) => row[column]);
              return (
                <tr key={rowIndex} className="border-b border-line/70 last:border-b-0 hover:bg-primary-soft/35">
                  {values.map((value, columnIndex) => (
                    <td key={`${rowIndex}-${columns[columnIndex]}`} className="max-w-72 whitespace-nowrap px-3 py-2 font-mono text-[11px] text-ink-2">
                      {value == null || value === "" ? <span className="text-faint">—</span> : String(value)}
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {rows.length > preview.length && (
        <p className="mt-2 text-[11px] text-faint">其余 {rows.length - preview.length} 条记录可通过“导出完整结果”查看。</p>
      )}
    </div>
  );
}

function Finding({ label, value, caption, captionTone = "text-faint" }) {
  return (
    <div className="px-4 py-3">
      <span className="text-[11px] text-muted">{label}</span>
      <strong className="mt-1 block font-mono text-lg font-semibold tracking-tight text-ink">{value}</strong>
      <small className={cn("mt-0.5 block text-[11px]", captionTone)}>{caption}</small>
    </div>
  );
}

function buildEvidenceGroups(plan) {
  const groups = [];
  const byKey = new Map();
  plan.forEach((step) => {
    const key = step.taskId ?? "run";
    if (!byKey.has(key)) {
      const group = {
        id: key,
        taskOrder: step.taskOrder ?? 0,
        title: step.taskId ? `任务 ${step.taskOrder} · ${step.taskTitle}` : "运行准备",
        steps: [],
      };
      byKey.set(key, group);
      groups.push(group);
    }
    byKey.get(key).steps.push(step);
  });
  return groups;
}

function EvidencePanel({ run, open, onBranch }) {
  const plan = run.plan ?? [];
  const groups = buildEvidenceGroups(plan);
  const [view, setView] = useState("overview");
  const [active, setActive] = useState(plan[1]?.id ?? plan[0]?.id);
  const [expandedGroup, setExpandedGroup] = useState(plan.find((step) => step.taskId)?.taskId ?? "run");
  const [executionExpanded, setExecutionExpanded] = useState(false);
  const [sourcesExpanded, setSourcesExpanded] = useState(false);
  const [traceFullscreen, setTraceFullscreen] = useState(false);
  const contentScrollRef = useRef(null);
  const detail = run.planDetails?.[active];
  const activeIndex = plan.findIndex((step) => step.id === active);
  const activeCheckpoint = activeIndex >= 0 ? plan[activeIndex]?.checkpoint : null;
  const completedSteps = plan.filter((step) => step.status === "succeeded").length;
  const interrupted = ["failed", "cancelled", "blocked"].includes(run.status);
  const blocked = run.status === "blocked";

  useEffect(() => {
    setActive(plan[1]?.id ?? plan[0]?.id);
    setExpandedGroup(plan.find((step) => step.taskId)?.taskId ?? "run");
    setExecutionExpanded(false);
    setSourcesExpanded(false);
    setView("overview");
  }, [run.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const changeView = (nextView) => {
    setView(nextView);
    window.requestAnimationFrame(() => {
      const node = contentScrollRef.current;
      if (!node) return;
      if (typeof node.scrollTo === "function") node.scrollTo({ top: 0, behavior: "auto" });
      else node.scrollTop = 0;
    });
  };

  if (!open) {
    return (
      <aside
        id="analysis-evidence-panel"
        className="evidence-panel evidence-panel-rail flex flex-col items-center border-l border-line bg-surface"
        aria-label="已收起的分析证据链"
      />
    );
  }

  return (
    <aside id="analysis-evidence-panel" className="evidence-panel flex flex-col overflow-hidden border-l border-line bg-surface">
      <div ref={contentScrollRef} className="min-h-0 flex-1 overflow-x-hidden overflow-y-scroll pb-4" style={{ scrollbarGutter: "stable" }}>
        <section className={cn("evidence-disclosure evidence-disclosure--execution mx-4 mt-3.5", executionExpanded && "is-open")}>
          <button
            type="button"
            aria-expanded={executionExpanded}
            aria-controls="analysis-evidence-execution-content"
            data-evidence-state={blocked ? "blocked" : interrupted ? "interrupted" : "ready"}
            className={cn(
              "evidence-card evidence-card--trust evidence-disclosure__trigger flex w-full items-center gap-2.5 rounded-card border px-3 py-2.5 text-left transition-[transform,opacity,box-shadow,border-radius] duration-300",
              interrupted ? "border-amber/30 bg-amber-soft" : "border-teal/30 bg-teal-soft",
              executionExpanded && "is-open",
              open && "motion-soft-rise",
            )}
            onClick={() => setExecutionExpanded((expanded) => !expanded)}
          >
            <span className={cn("shrink-0", interrupted ? "text-amber" : "text-teal")}><Icon name={blocked ? "shield" : interrupted ? "warning" : "shield"} size={16} /></span>
            <span className="min-w-0 flex-1">
              <strong className="block text-xs font-semibold text-ink">
                {blocked ? "安全拦截已记录" : interrupted ? "执行未完成，过程已保留" : "可信执行"}
              </strong>
              <span className="text-[11px] text-muted">
                {interrupted ? "可回看已完成步骤、失败位置与已有产物" : "只读、白名单与执行计划均已校验"}
              </span>
            </span>
            <StatusPill tone={interrupted ? "amber" : "green"}>{completedSteps}/{plan.length}</StatusPill>
            <Icon name="chevron" size={14} className={cn("shrink-0 text-muted transition-transform duration-300", executionExpanded && "rotate-90")} />
          </button>
          <div id="analysis-evidence-execution-content" aria-hidden={!executionExpanded} inert={!executionExpanded} className={cn("evidence-disclosure__content", executionExpanded && "is-open")}>
            <div className="evidence-disclosure__content-inner">
              {run.usage && (
                <div className="evidence-card evidence-card--usage mt-2.5 flex items-center justify-between gap-3 rounded-control border border-line bg-surface-2 px-3 py-2 text-[11px] text-muted">
                  <span className="min-w-0 truncate">模型调用 {run.usage.callCount ?? 0} 次 · {Number(run.usage.totalTokens ?? 0).toLocaleString()} Token</span>
                  <span className="shrink-0 font-mono text-faint">{run.usage.estimatedCost > 0 ? `约 ¥${Number(run.usage.estimatedCost).toFixed(4)}` : "费用待配置"}</span>
                </div>
              )}
              {run.trust?.costGate && (
                <div
                  data-cost-state={run.trust.costGate.allowed ? "allowed" : run.trust.costGate.ok === false ? "failed" : "review"}
                  className="evidence-card evidence-card--cost mt-2 flex items-center justify-between gap-3 rounded-control border border-line bg-surface-2 px-3 py-2 text-[11px] text-muted"
                >
                  <span className="min-w-0 truncate" title={run.trust.costGate.reason || ""}>
                    {run.trust.costGate.ok === false
                      ? `执行计划未生成 · ${run.trust.costGate.reason || "数据库未能解析查询"}`
                      : `执行计划 · 预计扫描 ${Number(run.trust.costGate.estimated_rows ?? 0).toLocaleString()} 行`}
                  </span>
                  <span className={cn("shrink-0 font-medium", run.trust.costGate.allowed ? "text-teal" : run.trust.costGate.ok === false ? "text-red" : "text-amber")}>
                    {run.trust.costGate.allowed ? "成本门控通过" : run.trust.costGate.ok === false ? "执行计划失败" : "成本需优化"}
                  </span>
                </div>
              )}
            </div>
          </div>
        </section>
        <section className="evidence-main-container mx-4 mt-3.5">
          <div className={cn("evidence-view-tabs grid grid-cols-4 gap-0.5 rounded-control border border-line bg-surface-2 p-0.5 transition-[transform,opacity] duration-300", open && "motion-soft-rise")}>
            <TabButton active={view === "overview"} onClick={() => changeView("overview")}>概览</TabButton>
            <TabButton active={view === "steps"} onClick={() => changeView("steps")}>过程 <span className="font-mono text-[10px]">{plan.length}</span></TabButton>
            <TabButton active={view === "graph"} onClick={() => changeView("graph")}>追踪图</TabButton>
            <TabButton active={view === "artifacts"} onClick={() => changeView("artifacts")}>产物 <span className="font-mono text-[10px]">{run.artifacts?.length ?? 0}</span></TabButton>
          </div>
          {view === "overview" && (
            <EvidenceOverview run={run} plan={plan} onOpenArtifacts={() => changeView("artifacts")} />
          )}
          {view === "steps" && (
            <div className={cn("evidence-steps mt-3 transition-[transform,opacity] duration-300", open && "motion-soft-rise")}>
              {groups.map((group) => {
                const completed = group.steps.filter((step) => step.status === "succeeded").length;
                const hasFailed = group.steps.some((step) => step.status === "failed");
                const expanded = expandedGroup === group.id;
                return (
                  <section key={group.id} className={cn("evidence-step-group relative pb-4 last:pb-0", expanded && "is-expanded")}>
                    {expanded && <span className="evidence-step-group__rail" aria-hidden="true" />}
                    <button
                      type="button"
                      onClick={() => setExpandedGroup(expanded ? null : group.id)}
                      className="evidence-step-group__trigger relative z-[1] flex w-full cursor-pointer items-center gap-2 py-2 text-left transition-colors hover:text-primary"
                    >
                      <span
                        data-status={completed === group.steps.length ? "succeeded" : hasFailed ? "failed" : "running"}
                        className={cn("evidence-step-group__node grid h-6 w-6 shrink-0 place-items-center rounded-tag", completed === group.steps.length ? "bg-green/10 text-green" : hasFailed ? "bg-red-soft text-red" : "bg-primary-soft text-primary")}
                      >
                        <Icon name={completed === group.steps.length ? "check" : hasFailed ? "alert" : "clock"} size={12} />
                      </span>
                      <span className="min-w-0 flex-1">
                        <strong className="block truncate text-xs font-semibold text-ink">{group.title}</strong>
                        <span className="text-[10px] text-faint">{completed}/{group.steps.length} 个节点完成</span>
                      </span>
                      <Icon name="chevron" size={14} className={cn("shrink-0 text-faint transition-transform", expanded && "rotate-90")} />
                    </button>
                    {expanded && (
                      <div className="evidence-step-list relative z-[1] mt-1 grid gap-0.5">
                        {group.steps.map((step) => (
                          <button
                            type="button"
                            key={step.id}
                            onClick={() => setActive(step.id)}
                            className={cn(
                              "evidence-step relative grid w-full cursor-pointer grid-cols-[24px_minmax(0,1fr)_auto] items-center gap-2 rounded-tag px-2 py-2 text-left transition-colors",
                              active === step.id ? "bg-primary-soft text-ink" : "hover:bg-surface-2",
                            )}
                          >
                            <div className="trace-step__node relative z-[1]" data-status={step.status}><Icon name={step.status === "succeeded" ? "check" : step.status === "failed" ? "alert" : "clock"} size={11} /></div>
                            <span className="min-w-0">
                              <strong className="block truncate text-[11px] font-semibold text-ink">{step.label}</strong>
                              <span className="block truncate text-[10px] text-faint">{step.caption}</span>
                            </span>
                            <small className="font-mono text-[10px] text-faint">{step.duration}</small>
                          </button>
                        ))}
                      </div>
                    )}
                  </section>
                );
              })}
            </div>
          )}
          {view === "graph" && <TraceGraph plan={plan} active={active} onSelect={setActive} onFullscreen={() => setTraceFullscreen(true)} />}
          {view === "artifacts" && <TraceArtifacts artifacts={run.artifacts ?? []} />}
          {view !== "artifacts" && detail && (
            <div className={cn("evidence-card evidence-card--detail mt-3.5 rounded-card border border-line bg-surface-2 p-3.5 transition-[transform,opacity,box-shadow] duration-300", open && "motion-soft-rise")}>
            <span className="text-[11px] font-semibold tracking-wide text-faint">当前节点详情</span>
            <h3 className="mt-1 text-body font-semibold text-ink">{detail.title}</h3>
            <p className="mt-1 text-xs leading-relaxed text-muted">{detail.body}</p>
            {detail.tags && (
              <div className="mt-2 flex flex-wrap gap-1.5">
                {detail.tags.map((tag) => (
                  <span key={tag} className="rounded-tag border border-line bg-surface px-1.5 py-0.5 font-mono text-[10px] text-ink-2">{tag}</span>
                ))}
              </div>
            )}
            {detail.code && (
              <pre className="mt-2 overflow-auto rounded-control bg-ink p-3 font-mono text-[11px] leading-relaxed text-white/90"><code>{detail.code}</code></pre>
            )}
            {activeCheckpoint?.rollbackable && (
              <div className="mt-3 flex items-center justify-between gap-3 rounded-control border border-line bg-surface px-2.5 py-2">
                <div className="min-w-0">
                  <span className="block text-[10px] font-semibold text-ink-2">调整分析条件</span>
                  <span className="block truncate text-[10px] text-faint">保留当前结果，生成可对比的新方案</span>
                </div>
                <button
                  type="button"
                  onClick={() => onBranch(plan[activeIndex])}
                  title="修改时间、对象、指标、分组或图表，当前分析不会被修改"
                  className="flex shrink-0 cursor-pointer items-center gap-1 rounded-tag px-1.5 py-1 text-xs font-semibold text-primary transition-colors hover:bg-primary-soft"
                >
                  <Icon name="link" size={13} />调整条件
                </button>
              </div>
            )}
            </div>
          )}
        </section>
        {view !== "artifacts" && run.sources?.length > 0 && (
          <section className={cn("evidence-disclosure evidence-disclosure--sources mx-4 mt-3.5", sourcesExpanded && "is-open")}>
            <button
              type="button"
              aria-expanded={sourcesExpanded}
              aria-controls="analysis-evidence-sources-content"
              className={cn("evidence-disclosure__trigger evidence-disclosure__trigger--sources flex w-full items-center gap-2.5 rounded-card border px-3 py-2.5 text-left transition-[transform,opacity,box-shadow,border-radius] duration-300", sourcesExpanded && "is-open", open && "motion-soft-rise")}
              onClick={() => setSourcesExpanded((expanded) => !expanded)}
            >
              <span className="grid h-6 w-6 shrink-0 place-items-center rounded-tag bg-primary-soft text-primary"><Icon name="database" size={14} /></span>
              <span className="min-w-0 flex-1">
                <strong className="block text-xs font-semibold text-ink">引用资产</strong>
                <span className="text-[10px] text-muted">{run.sources.length} 项数据、指标与语义资产</span>
              </span>
              <Icon name="chevron" size={14} className={cn("shrink-0 text-muted transition-transform duration-300", sourcesExpanded && "rotate-90")} />
            </button>
            <div id="analysis-evidence-sources-content" aria-hidden={!sourcesExpanded} inert={!sourcesExpanded} className={cn("evidence-disclosure__content", sourcesExpanded && "is-open")}>
              <div className="evidence-disclosure__content-inner">
                <div className="evidence-sources grid gap-1">
                  {run.sources.map((source) => (
                    <SourceButton key={source.title} icon={source.icon} title={source.title} caption={source.caption} />
                  ))}
                </div>
              </div>
            </div>
          </section>
        )}
      </div>
      <TraceFullscreen
        open={traceFullscreen}
        onClose={() => setTraceFullscreen(false)}
        plan={plan}
        active={active}
        onSelect={setActive}
      />
    </aside>
  );
}

function BusinessPlanCard({ planner, question, onOpenArtifacts }) {
  const [expanded, setExpanded] = useState(false);
  const [selectedId, setSelectedId] = useState(null);
  const selected = planner.steps.find((step) => step.id === selectedId);
  return (
    <section className="overflow-hidden rounded-card border border-primary/20 bg-primary-soft/20">
      <button
        type="button"
        onClick={() => setExpanded((value) => !value)}
        aria-expanded={expanded}
        className="flex w-full cursor-pointer items-start justify-between gap-3 px-3 py-2.5 text-left transition-colors hover:bg-primary-soft/45"
      >
        <span className="min-w-0">
          <span className="flex items-center gap-1.5 text-primary">
            <Icon name="model" size={14} />
            <span className="text-[10px] font-semibold tracking-wide">AGENT 调查计划</span>
          </span>
          <strong className="mt-1 block line-clamp-2 text-xs leading-relaxed text-ink">{planner.plan?.goal ?? question}</strong>
        </span>
        <span className="flex shrink-0 items-center gap-2">
          <StatusPill tone={planner.failed > 0 ? "amber" : "blue"}>{planner.completed}/{planner.steps.length}</StatusPill>
          <Icon name="chevron" size={13} className={cn("text-muted transition-transform", expanded && "rotate-180")} />
        </span>
      </button>
      {expanded && (
        <div className="border-t border-primary/10">
          <div className="divide-y divide-primary/10 px-3">
            {planner.steps.map((step) => {
              const complete = ["succeeded", "accepted", "skipped"].includes(step.status);
              return (
                <button
                  type="button"
                  key={step.id}
                  onClick={() => setSelectedId((value) => value === step.id ? null : step.id)}
                  className={cn(
                    "grid w-full cursor-pointer grid-cols-[20px_minmax(0,1fr)] gap-2 py-2.5 text-left transition-colors hover:bg-white/45",
                    selectedId === step.id && "bg-white/60",
                  )}
                >
                  <span className={cn(
                    "mt-0.5 grid h-5 w-5 place-items-center rounded-full",
                    step.status === "failed" ? "bg-red-soft text-red" : complete ? "bg-teal-soft text-teal" : "bg-surface text-primary",
                  )}>
                    <Icon name={step.status === "failed" ? "alert" : complete ? "check" : "clock"} size={11} />
                  </span>
                  <span className="min-w-0">
                    <span className="flex items-center justify-between gap-2">
                      <strong className="truncate text-[11px] font-semibold text-ink">{step.purpose}</strong>
                      <span className="shrink-0 font-mono text-[9px] text-faint">{step.capability}</span>
                    </span>
                    <span className="mt-0.5 block line-clamp-2 text-[10px] leading-relaxed text-muted">{step.summary}</span>
                  </span>
                </button>
              );
            })}
          </div>
          {selected && (
            <div className="border-t border-primary/10 bg-surface/70 px-3 py-2.5 text-[10px] leading-relaxed text-muted">
              <div className="flex flex-wrap items-center gap-1.5">
                <span className="font-semibold text-ink">步骤详情</span>
                {selected.reasonCode && <span className="rounded-tag border border-line px-1.5 py-0.5 font-mono">{selected.reasonCode}</span>}
                <button
                  type="button"
                  disabled={!selected.artifactRefs?.length}
                  onClick={onOpenArtifacts}
                  className="rounded-tag border border-line px-1.5 py-0.5 transition-colors enabled:cursor-pointer enabled:hover:border-primary/40 enabled:hover:text-primary disabled:opacity-70"
                >关联产物 {selected.artifactRefs?.length ?? 0}</button>
              </div>
              <p className="mt-1.5">{selected.summary}</p>
            </div>
          )}
          {planner.degraded && (
            <p className="border-t border-primary/10 px-3 py-2 text-[10px] text-muted">模型规划不可用时已安全降级为确定性计划，受治理分析链和权限边界保持不变。</p>
          )}
        </div>
      )}
    </section>
  );
}

function EvidenceOverview({ run, plan, onOpenArtifacts }) {
  const completed = plan.filter((step) => step.status === "succeeded").length;
  const failed = plan.filter((step) => step.status === "failed").length;
  return (
    <div className="evidence-overview grid gap-3">
      {run.planner?.mode === "bounded" && run.planner?.steps?.length > 0 && (
        <BusinessPlanCard planner={run.planner} question={run.question} onOpenArtifacts={onOpenArtifacts} />
      )}
      <div className="evidence-card evidence-card--summary rounded-card border border-line bg-surface p-3">
        <span className="text-[10px] font-semibold tracking-wide text-faint">本次分析</span>
        <p className="mt-1 text-xs leading-relaxed text-ink">{run.question}</p>
        <div className="mt-3 grid grid-cols-2 gap-y-2 divide-x divide-line border-y border-line py-2 sm:grid-cols-4">
          <OverviewMetric label="步骤" value={`${completed}/${plan.length}`} />
          <OverviewMetric label="产物" value={run.artifacts?.length ?? 0} />
          <OverviewMetric label="总历时" value={formatDuration(run.execution?.totalDurationMs)} />
          <OverviewMetric label="查询耗时" value={formatDuration(run.execution?.durationMs)} />
        </div>
        {failed > 0 && <p className="mt-2 text-[11px] text-red">其中 {failed} 个工具调用失败，已保留执行记录。</p>}
      </div>
    </div>
  );
}

function OverviewMetric({ label, value }) {
  return <span className="px-2 first:pl-0 last:pr-0"><small className="block text-[10px] text-faint">{label}</small><strong className="mt-0.5 block font-mono text-sm text-ink">{value}</strong></span>;
}

function TraceFullscreen({ open, onClose, plan, active, onSelect }) {
  useEffect(() => {
    if (!open) return undefined;
    const closeOnEscape = (event) => { if (event.key === "Escape") onClose(); };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-ink/35 p-6 backdrop-blur-[2px]" role="presentation" onClick={onClose}>
      <section className="flex h-[min(760px,82vh)] w-full max-w-[1180px] flex-col overflow-hidden rounded-overlay border border-line bg-surface p-5 shadow-card-hover" role="dialog" aria-modal="true" aria-label="全屏追踪图" onClick={(event) => event.stopPropagation()}>
        <header className="flex items-center justify-between border-b border-line pb-4">
          <div><span className="font-mono text-[10px] font-semibold tracking-wide text-faint">TRACE GRAPH</span><h2 className="mt-1 text-lg font-semibold text-ink">分析执行追踪图</h2></div>
          <button type="button" onClick={onClose} aria-label="关闭全屏追踪图" className="grid h-9 w-9 cursor-pointer place-items-center rounded-control border border-line text-muted transition-colors hover:bg-surface-2 hover:text-ink"><Icon name="close" size={17} /></button>
        </header>
        <div className="min-h-0 flex-1 py-5"><TraceGraph plan={plan} active={active} onSelect={onSelect} fullscreen /></div>
      </section>
    </div>
  );
}

function TabButton({ active, onClick, children }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "flex h-7 cursor-pointer items-center justify-center gap-1 rounded-tag text-[11px] font-medium transition-colors",
        active ? "bg-surface text-ink shadow-card" : "text-muted hover:text-ink",
      )}
    >
      {children}
    </button>
  );
}

function SourceButton({ icon, title, caption }) {
  return (
    <button type="button" className="evidence-source-card flex w-full cursor-pointer items-center gap-2.5 rounded-control border border-line bg-surface px-2.5 py-2 text-left transition-colors hover:border-line-strong hover:bg-surface-2">
      <span className="shrink-0 text-muted"><Icon name={icon} size={15} /></span>
      <span className="min-w-0 flex-1">
        <strong className="block truncate text-xs font-semibold text-ink">{title}</strong>
        <small className="block truncate text-[10px] text-faint">{caption}</small>
      </span>
      <span className="shrink-0 text-faint"><Icon name="chevron" size={14} /></span>
    </button>
  );
}

function TraceGraph({ plan, active, onSelect, onFullscreen, fullscreen = false }) {
  const groups = buildEvidenceGroups(plan);
  // The SVG path uses node centers while each absolutely positioned button is
  // anchored by its top edge. Keep the two coordinate systems in sync so the
  // connector terminates at the center of the circular node instead of its
  // upper edge.
  const graphNodeRadius = 14;
  const graphRowCenter = 64 + graphNodeRadius;
  const maxSteps = Math.max(1, ...groups.map((group) => group.steps.length));
  const canvasWidth = Math.max(420, 72 + Math.max(maxSteps - 1, 0) * 96);
  const canvasHeight = Math.max(220, 62 + groups.length * 120);
  const positions = [];
  const groupPaths = [];
  groups.forEach((group, groupIndex) => {
    const points = group.steps.map((step, stepIndex) => {
      const point = [44 + stepIndex * 96, graphRowCenter + groupIndex * 120];
      positions.push({ id: step.id, point, groupTitle: group.title, step });
      return point;
    });
    groupPaths.push(buildTracePath(points));
  });
  const canvasRef = useRef(null);
  const viewportRef = useRef(null);
  const [viewportTransform, setViewportTransform] = useState(null);

  const fitCanvas = () => {
    const node = viewportRef.current;
    const viewportWidth = node?.clientWidth || canvasWidth;
    const viewportHeight = node?.clientHeight || canvasHeight;
    setViewportTransform(getTraceFitTransform({ positions: positions.map((item) => item.point), viewportWidth, viewportHeight }));
  };

  return (
    <div className={cn("evidence-trace-view", fullscreen ? "flex h-full min-h-0 flex-col" : "mx-4 mt-3")} aria-label="Agent 分析追踪图">
      <div className="flex items-center justify-between text-[11px] text-faint">
        <span>{plan.length} 个节点 · {groups.length} 条任务路径</span>
        <span className="flex items-center gap-3">
          <button type="button" onClick={fitCanvas} className="cursor-pointer font-medium text-primary transition-opacity hover:opacity-75">适应画布</button>
          {!fullscreen && <button type="button" onClick={onFullscreen} aria-label="全屏查看追踪图" className="grid h-7 w-7 cursor-pointer place-items-center rounded-control text-muted transition-colors hover:bg-surface-3 hover:text-ink"><Icon name="expand" size={15} /></button>}
        </span>
      </div>
      <div ref={viewportRef} className={cn("mt-2 grid overflow-auto rounded-card border border-line bg-surface-2", fullscreen ? "min-h-0 flex-1" : "max-h-[420px]")}>
        <div ref={canvasRef} data-testid="analysis-trace-canvas" className="relative min-w-max" style={{ width: canvasWidth, height: canvasHeight }}>
          <div
            data-testid="analysis-trace-viewport"
            className="absolute inset-0 transition-transform duration-300 ease-out"
            style={viewportTransform
              ? { transformOrigin: "0 0", transform: `translate(${viewportTransform.x}px, ${viewportTransform.y}px) scale(${viewportTransform.scale})` }
              : undefined}
          >
            <svg viewBox={`0 0 ${canvasWidth} ${canvasHeight}`} className="absolute inset-0 h-full w-full fill-none stroke-line-strong" aria-hidden="true">
              {groupPaths.map((path, index) => <path key={groups[index]?.id} d={path} />)}
            </svg>
            {groups.map((group, index) => <span key={group.id} style={{ top: 24 + index * 120 }} className="absolute left-3 text-[10px] font-semibold text-faint">{group.title}</span>)}
            {positions.map(({ id, point, step }) => {
              const [x, y] = point;
              return (
                <button
                  type="button"
                  key={id}
                  style={{ left: x, top: y - graphNodeRadius }}
                  onClick={() => onSelect(id)}
                  aria-label={step.label}
                  className="absolute grid -translate-x-1/2 cursor-pointer justify-items-center gap-1"
                >
                  <div
                    className={cn(
                      "trace-graph-node__mark transition-shadow",
                      active === id && "ring-2 ring-primary/15",
                    )}
                    data-status={step.status}
                  >
                    <Icon name={step.status === "succeeded" ? "check" : step.status === "failed" ? "alert" : "clock"} size={11} />
                  </div>
                  <small className={cn("max-w-20 truncate text-[10px]", active === id ? "font-semibold text-ink" : "text-faint")}>{step.label}</small>
                </button>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}

function TraceArtifacts({ artifacts }) {
  const tones = {
    blue: "bg-primary-soft text-primary",
    teal: "bg-teal-soft text-teal",
    violet: "bg-violet/8 text-violet",
  };
  const labels = {
    "memory-context": { icon: "memory", name: "相关记忆", type: "偏好、经验与召回依据", tone: "blue" },
    context: { icon: "knowledge", name: "语义上下文", type: "指标、表与关联", tone: "blue" },
    sql: { icon: "code", name: "分析 SQL", type: "只读查询", tone: "violet" },
    validation: { icon: "shield", name: "校验报告", type: "安全与语义检查", tone: "teal" },
    result: { icon: "database", name: "查询结果", type: "结构化结果集", tone: "teal" },
    conclusion: { icon: "insight", name: "分析结论", type: "可追溯解释", tone: "blue" },
  };
  const items = artifacts.map((artifact) => ({
    ...(labels[artifact.type] ?? { icon: "file", name: artifact.type, type: "分析产物", tone: "blue" }),
    id: artifact.id,
    meta: artifact.id,
  }));
  return (
    <div className="evidence-artifacts mx-4 mt-3 grid gap-1.5">
      <div className="flex items-center gap-1.5 rounded-control border border-line bg-surface-2 px-2.5 py-2 text-[11px] text-muted">
        <Icon name="link" size={14} />
        <span>所有产物均关联本次 SQL、指标版本和数据时间。</span>
      </div>
      {items.length === 0 && <div className="rounded-control border border-dashed border-line px-3 py-5 text-center text-xs text-faint">产物将在步骤完成后出现在这里</div>}
      {items.map((item) => (
        <button
          type="button"
          key={item.id}
          className="grid w-full cursor-pointer grid-cols-[32px_minmax(0,1fr)_auto_14px] items-center gap-2.5 rounded-control border border-line bg-surface px-2.5 py-2 text-left transition-colors hover:border-line-strong hover:bg-surface-2"
        >
          <span className={cn("grid h-8 w-8 place-items-center rounded-control", tones[item.tone])}><Icon name={item.icon} size={16} /></span>
          <span className="min-w-0">
            <strong className="block truncate text-xs font-semibold text-ink">{item.name}</strong>
            <small className="block truncate text-[10px] text-faint">{item.type}</small>
          </span>
          <code className="max-w-24 truncate font-mono text-[10px] text-faint">{item.meta}</code>
          <span className="text-faint"><Icon name="chevron" size={14} /></span>
        </button>
      ))}
    </div>
  );
}
