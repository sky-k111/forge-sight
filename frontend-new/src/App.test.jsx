import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { vi } from "vitest";
import App from "./App";
import { businessObjects, businessRules, businessTopics, metrics } from "./mocks/data";

const okJson = (data) => Promise.resolve({ ok: true, json: () => Promise.resolve(data) });

function makeAnalysisSnapshot({ id = "run-real", status = "succeeded", question = "分析 7 月各工序良率，并与各工序标准值比较" } = {}) {
  const taskId = `${id}-task`;
  const task = { id: taskId, order: 1, title: "实际良率与工序标准值", question, mode: "single", analysisType: "comparison", status };
  const stepDefs = [
    ["intent.guard", "检查只读分析边界"], ["intent.parse", "理解并拆解业务问题"], ["context.retrieve", "定位数据资源"],
    ["query.generate", "生成只读 SQL"], ["query.validate", "安全与语义校验"], ["query.execute", "执行并检查结果"], ["result.explain", "生成图表与结论"],
  ];
  const steps = stepDefs.map(([stepKey, label], index) => ({
    id: `${id}-step-${index}`, taskId: index < 2 ? null : taskId, stepKey, stepOrder: index,
    attempt: 1, status: "succeeded", label, detail: "已完成",
  }));
  const artifacts = [
    { id: `${id}-ctx`, taskId, type: "context", content: { coverage: { metricCount: 1, tableCount: 2 }, metrics: [{ id: "M-001", name: "工序良率", version: "2.3" }], tables: [{ name: "mes_process_output", cnName: "工序产量表" }] } },
    { id: `${id}-sql`, taskId, type: "sql", content: { sql: "SELECT process_name, yield_rate, target FROM process_yield LIMIT 100" } },
    { id: `${id}-val`, taskId, type: "validation", content: { ok: true, checks: ["只读通过", "白名单通过"] } },
    { id: `${id}-result`, taskId, type: "result", content: { columns: ["process_name", "yield_rate", "target"], rows: [["装配", 98.7, 98.0], ["焊接", 97.9, 97.5], ["功能测试", 95.83, 96.5]], row_count: 3, duration_ms: 1400 } },
    { id: `${id}-conclusion`, taskId, type: "conclusion", content: { text: "功能测试良率低于标准 0.67 个百分点。" } },
  ];
  const checkpoints = steps.map((step, index) => ({ id: `${id}-ckpt-${index}`, stepKey: step.stepKey, summary: { taskId: step.taskId }, rollbackable: true }));
  return {
    id, sessionId: "ses-real", sourceId: 1, status, question, tasks: [task], steps, artifacts, checkpoints,
    interaction: null, assistantMessage: { content: "已分析 2,752 条工序产量记录，功能测试良率低于标准。" },
    usage: { callCount: 2, totalTokens: 1800, estimatedCost: 0.012, currency: "CNY" },
    createdAt: "2026-07-16T10:32:00+08:00",
  };
}

let analysisRuns;

beforeEach(() => {
  window.history.replaceState({}, "", "/workspace/overview");
  window.sessionStorage.clear();
  analysisRuns = { "run-real": makeAnalysisSnapshot() };
  // 资源中心与安全审计已接真实后端，测试环境统一打桩 /api/*，避免依赖本机服务
  vi.stubGlobal("fetch", vi.fn((url, options) => {
    const u = String(url);
    const method = options?.method ?? "GET";
    if (u.includes("/api/analysis/sessions?") && method === "GET") {
      return okJson([{ id: "ses-real", title: "制造业务问析", sourceId: 1, sourceName: "华东制造数据底座" }]);
    }
    if (u.endsWith("/api/analysis/sessions/ses-real") && method === "GET") {
      return okJson({ id: "ses-real", runs: Object.values(analysisRuns).map((run) => ({ id: run.id })) });
    }
    if (u.includes("/api/analysis/sessions/ses-real?userId=") && method === "DELETE") {
      return okJson({ sessionId: "ses-real", deleted: true, retention: "audit" });
    }
    if (u.includes("/api/analysis/sessions/") && u.endsWith("/runs") && method === "POST") {
      const question = JSON.parse(options?.body ?? "{}").question ?? "";
      const id = `run-test-${Object.keys(analysisRuns).length}`;
      let snapshot = makeAnalysisSnapshot({ id, question });
      if (/删除|写入|更新/.test(question)) snapshot = { ...snapshot, status: "blocked", errorCode: "WRITE_INTENT_BLOCKED", tasks: [], steps: [], artifacts: [], checkpoints: [], assistantMessage: null };
      if (/最近良率/.test(question)) snapshot = {
        ...snapshot, status: "waiting_user", assistantMessage: null,
        interaction: { id: `${id}-interaction`, prompt: { question: "请选择本次良率统计口径。", options: [{ label: "本月至今 · 按工序统计", value: "M-001" }], allowCustom: true } },
      };
      analysisRuns[id] = snapshot;
      return okJson({ runId: id, sessionId: "ses-real", status: "queued" });
    }
    if (u.includes("/api/analysis/interactions/") && u.endsWith("/responses") && method === "POST") {
      const run = Object.values(analysisRuns).find((item) => item.interaction && u.includes(item.interaction.id));
      analysisRuns[run.id] = { ...makeAnalysisSnapshot({ id: run.id, question: run.question }), assistantMessage: { content: "已按你选择的口径完成分析，共核对 2,752 条记录。" } };
      return okJson({ runId: run.id, sessionId: "ses-real", status: "queued", resumed: true });
    }
    if (u.includes("/api/analysis/checkpoints/") && u.endsWith("/branches") && method === "POST") {
      const payload = JSON.parse(options?.body ?? "{}");
      const id = "run-branch";
      analysisRuns[id] = makeAnalysisSnapshot({ id, question: payload.instruction });
      return okJson({
        branchId: "branch-1",
        session: { id: "ses-branch", title: "分析调整方案", sourceId: 1, parentSessionId: "ses-real" },
        branchContext: {
          branchId: "branch-1",
          sourceSessionId: "ses-real",
          sourceRunId: "run-real",
          sourceQuestion: analysisRuns["run-real"].question,
          checkpoint: { id: "run-real-ckpt-6", stepKey: "result.explain", rollbackable: true },
          adjustments: payload.adjustments,
          reuseArtifacts: payload.reuseArtifacts,
        },
        run: { runId: id, sessionId: "ses-branch", status: "queued" },
      });
    }
    const runMatch = u.match(/\/api\/analysis\/runs\/([^/?]+)$/);
    if (runMatch && method === "GET") return okJson(analysisRuns[runMatch[1]]);
    if (u.endsWith("/api/datasources") && method === "POST") return okJson({ id: 2 });
    if (u.includes("/api/datasources") && u.endsWith("/test")) return okJson({ ok: true, status: "连接正常", message: "连接正常" });
    if (u.includes("/api/datasources") && u.endsWith("/scan")) return okJson({ tables: 1, fields: 6, relations: 0 });
    if (u.endsWith("/api/datasources")) {
      return okJson([
        { id: 1, name: "华东制造数据底座", type: "mysql", typeLabel: "MySQL", host: "127.0.0.1", port: 3306, database: "forge_sight", username: "forgesight_reader", status: "连接正常", isDefault: true },
      ]);
    }
    if (u.includes("/api/overview")) {
      return okJson({
        source: { id: 1, name: "华东制造数据底座", type: "mysql", database: "forge_sight", status: "连接正常" },
        capability: { state: "ready", label: "可直接问析", detail: "已登记数据表并建立可执行的业务指标口径。", canAnalyze: true },
        snapshot: { registeredTables: 10, records: 8019, latestData: "2026-07-15", earliestTableUpdate: "2026-07-15", datedTables: 10, lastScan: "2026-07-15T10:36:00", readOnly: true },
        metrics: { trusted: 11, pendingBindings: 0 },
        topics: [{ code: "PROD", name: "生产分析", description: "工单、产量与工序过程数据", color: "blue", tableCount: 3, metricCount: 2, metricNames: ["产量", "计划完成率"], mode: "metrics" }],
        suggestions: [{ id: "topic-PROD", title: "分析产量", prompt: "请分析当前数据源中产量的最近趋势", caption: "已验证指标：产量 / 计划完成率" }],
        recent: { total: 1, items: [{ id: "ses-real", title: "分析本月各工序良率并与目标值比较", updatedAt: "2026-07-16T10:32:00", status: "succeeded", runCount: 2 }] },
      });
    }
    if (u.includes("/tables/unregistered")) return okJson([]);
    if (u.includes("/api/resources/tables")) return okJson([]);
    if (u.includes("/api/knowledge/topics")) return okJson(businessTopics);
    if (u.includes("/api/knowledge/objects")) return okJson(businessObjects);
    if (u.includes("/api/knowledge/rules")) return okJson(businessRules);
    if (u.includes("/api/knowledge/metrics")) return okJson(metrics);
    return okJson([]);
  }));
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ForgeSight application routes", () => {
  test("loads sidebar analysis sessions from the backend and opens the selected session", async () => {
    render(<App />);

    const session = await screen.findByRole("button", { name: "制造业务问析" });
    fireEvent.click(session);

    await waitFor(() => expect(window.location.pathname).toBe("/workspace/analysis"));
    expect(window.sessionStorage.getItem("forgesight:analysis-session-id")).toBe("ses-real");
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("/api/analysis/sessions?userId=local-test-user"),
      expect.any(Object),
    );
  });

  test("deletes a sidebar conversation only after explicit confirmation", async () => {
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "会话操作：制造业务问析" }));
    fireEvent.click(screen.getByRole("button", { name: "删除对话" }));
    fireEvent.click(screen.getByRole("button", { name: "确认删除" }));

    await waitFor(() => expect(screen.queryByRole("button", { name: "制造业务问析" })).toBeNull());
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("/api/analysis/sessions/ses-real?userId=local-test-user"),
      expect.objectContaining({ method: "DELETE" }),
    );
  });

  test("renders the business overview without internal priority labels", async () => {
    const { container } = render(<App />);
    expect(await screen.findByRole("heading", { name: "分析总览" })).toBeTruthy();
    expect(await screen.findByRole("heading", { name: "华东制造数据底座" }, { timeout: 5000 })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "总览" }).getAttribute("aria-selected")).toBe("true");
    expect(screen.queryByRole("heading", { name: "当前数据源可以分析什么" })).toBeNull();
    fireEvent.click(screen.getByRole("tab", { name: "可分析内容" }));
    expect(screen.getByRole("heading", { name: "当前数据源可以分析什么" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "选择一个分析问题" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "用所选问题开始" })).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "最近分析" }));
    expect(screen.getByRole("heading", { name: "当前数据源的最近分析" })).toBeTruthy();
    expect(screen.getByText("分析本月各工序良率并与目标值比较")).toBeTruthy();
    expect(screen.queryByText(/阶段5|EXPLAIN|验收/)).toBeNull();
    expect(screen.queryByText(/%/)).toBeNull();
    expect(screen.queryByText("P0")).toBeNull();
    expect(screen.queryByText("赛题必须项")).toBeNull();
    expect(screen.queryByText("查看数据资源")).toBeNull();
    expect(screen.queryByText("待确认新表")).toBeNull();
    expect(screen.queryByText("表关系已识别")).toBeNull();
    expect(screen.getByAltText("ForgeSight logo").getAttribute("src")).toContain("forgesight-logo");
    expect(container.querySelector('[data-icon="analysis"]')).toBeTruthy();
    expect(container.querySelector('[data-icon="question"]')).toBeTruthy();
    expect(container.querySelector('[data-icon="spark"]')).toBeNull();
  });

  test("navigates to a directly addressable analysis page", async () => {
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "智能问析" }));
    await waitFor(() => expect(window.location.pathname).toBe("/workspace/analysis"));
  });

  test("renders analysis trace icons with the original node style instead of green span badges", async () => {
    window.history.replaceState({}, "", "/workspace/analysis");
    const { container } = render(<App />);

    await screen.findByText(/2,752/, {}, { timeout: 3000 });
    fireEvent.click((await screen.findAllByRole("button", { name: /过程/ }))[0]);

    expect(container.querySelector(".trace-step__node")).toBeTruthy();
  });

  test("slides the evidence panel shell between open and closed states", async () => {
    window.history.replaceState({}, "", "/workspace/analysis");
    render(<App />);

    await screen.findByText(/2,752/, {}, { timeout: 3000 });
    const toggle = await screen.findByRole("button", { name: "分析证据链" });
    const shell = await screen.findByTestId("analysis-evidence-shell");

    expect(shell.getAttribute("data-state")).toBe("open");

    fireEvent.click(toggle);
    expect(shell.getAttribute("data-state")).toBe("closed");
    expect(shell.style.getPropertyValue("--evidence-panel-width")).toBe("56px");
    expect(screen.getByRole("button", { name: "展开证据链" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "关闭证据链" })).toBeNull();

    fireEvent.click(toggle);
    expect(shell.getAttribute("data-state")).toBe("open");
    expect(shell.style.getPropertyValue("--evidence-panel-width")).toBe("320px");
  });

  test("keeps the sidebar toggle anchored to one slot before and after collapsing", async () => {
    render(<App />);

    const collapse = await screen.findByRole("button", { name: "折叠侧边栏" });
    expect(collapse.className).toContain("sidebar-shell__toggle");

    fireEvent.click(collapse);

    const expand = screen.getByRole("button", { name: "展开侧边栏" });
    expect(expand.className).toContain("sidebar-shell__toggle");
  });

  test("keeps the evidence panel toggle anchored to one slot before and after collapsing", async () => {
    window.history.replaceState({}, "", "/workspace/analysis");
    render(<App />);

    await screen.findByText(/2,752/, {}, { timeout: 3000 });
    const close = await screen.findByRole("button", { name: "关闭证据链" });
    expect(close.className).toContain("evidence-panel-shell__toggle");

    fireEvent.click(close);

    const open = screen.getByRole("button", { name: "展开证据链" });
    expect(open.className).toContain("evidence-panel-shell__toggle");
  });

  test("renders the analysis chart with the refined visual shell", async () => {
    window.history.replaceState({}, "", "/workspace/analysis");
    const { container } = render(<App />);

    await screen.findByLabelText("实际良率与工序标准值柱状图");

    expect(container.querySelector(".chart-frame.chart-frame--elevated")).toBeTruthy();
  });

  test("keeps the analysis bar and legend on the same series color", async () => {
    window.history.replaceState({}, "", "/workspace/analysis");
    const { container } = render(<App />);

    await screen.findByLabelText("实际良率与工序标准值柱状图");

    const chartFrame = container.querySelector("[data-chart-series-colors]");
    const legendMarker = container.querySelector("[data-chart-legend-color]");
    expect(chartFrame.getAttribute("data-chart-series-colors").split(",")[0]).toBe("var(--color-primary)");
    expect(legendMarker.getAttribute("data-chart-legend-color")).toBe(chartFrame.getAttribute("data-chart-series-colors").split(",")[0]);
  });

  test("switches between business and administrator workspaces", async () => {
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "管理" }));
    expect(await screen.findByRole("heading", { name: "治理总览" })).toBeTruthy();
    expect(screen.getByRole("navigation", { name: "管理员导航" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "数据治理" }).getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByRole("button", { name: "平台治理" }).getAttribute("aria-expanded")).toBe("true");
    expect(await screen.findByText("待处理事项")).toBeTruthy();
    expect(window.location.pathname).toBe("/admin/overview");
  });

  test("keeps the evidence chain available when an analysis has no result", async () => {
    window.history.replaceState({}, "", "/workspace/analysis");
    analysisRuns["run-real"] = {
      ...makeAnalysisSnapshot({ status: "failed" }),
      errorCode: "CONTEXT_NO_MATCH",
      error: {
        code: "CONTEXT_NO_MATCH",
        userSafeTitle: "当前数据暂时无法回答这个问题",
        userSafeMessage: "当前数据源中没有匹配的数据资源。",
        suggestion: "请调整产品名称或分析范围。",
        reason: "semantic_resource_missing",
        showCodeToUser: false,
      },
    };
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "查看证据链" }));
    expect(await screen.findByText("执行未完成，过程已保留")).toBeTruthy();
  });

  test("opens the trace graph and creates an adjusted analysis plan", async () => {
    window.history.replaceState({}, "", "/workspace/analysis");
    const { container } = render(<App />);
    await screen.findByText(/2,752/, {}, { timeout: 3000 });
    fireEvent.click(await screen.findByRole("button", { name: "追踪图" }));
    expect(screen.getByLabelText("Agent 分析追踪图")).toBeTruthy();
    expect(container.querySelector("[data-testid='analysis-trace-canvas']")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "调整条件" }));
    expect(await screen.findByRole("heading", { name: "调整分析条件" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "折线图" }));
    fireEvent.click(screen.getByRole("button", { name: "生成新方案" }));
    expect(await screen.findByText("已创建新分析方案")).toBeTruthy();
    expect(await screen.findByText("基于原分析的新方案")).toBeTruthy();
  });

  test("fits the trace graph canvas by applying a computed transform", async () => {
    window.history.replaceState({}, "", "/workspace/analysis");
    render(<App />);

    await screen.findByText(/2,752/, {}, { timeout: 3000 });
    fireEvent.click(await screen.findByRole("button", { name: "追踪图" }));
    const viewport = await screen.findByTestId("analysis-trace-viewport");
    expect(viewport.getAttribute("style") ?? "").not.toContain("scale(");

    fireEvent.click(screen.getByRole("button", { name: "适应画布" }));
    expect(viewport.getAttribute("style") ?? "").toContain("scale(");
  });

  test("shows the modeling workflow with verified dataset counts", async () => {
    window.history.replaceState({}, "", "/workspace/modeling");
    render(<App />);
    expect(await screen.findByRole("heading", { name: "智能建模" })).toBeTruthy();
    expect(await screen.findByText((_, element) => element?.tagName === "SMALL" && element.textContent.includes("2,752 个样本"))).toBeTruthy();
    expect(screen.getByText((_, element) => element?.tagName === "SMALL" && element.textContent.includes("高风险样本 851 个"))).toBeTruthy();
    expect(screen.queryByText("赛题必须项")).toBeNull();
  });

  test("submits a new question through the real analysis run contract", async () => {
    window.history.replaceState({}, "", "/workspace/analysis");
    render(<App />);
    await screen.findByText(/2,752/, {}, { timeout: 3000 });
    const textarea = screen.getByPlaceholderText(/用业务语言/);
    fireEvent.change(textarea, { target: { value: "找出最近一个月不良数量最高的产品" } });
    fireEvent.submit(textarea.closest("form"));
    expect(await screen.findByText("找出最近一个月不良数量最高的产品", { selector: "p" }, { timeout: 3000 })).toBeTruthy();
    expect(await screen.findAllByText(/模型调用/)).not.toHaveLength(0);
  });

  test("switches between recommended and manually selected modeling strategies", async () => {
    window.history.replaceState({}, "", "/workspace/modeling");
    render(<App />);
    await screen.findByRole("heading", { name: "智能建模" });
    fireEvent.click(screen.getByRole("button", { name: /建模方案/ }));
    expect(screen.getByText("候选模型")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /人工指定/ }));
    expect(screen.getByText("指定算法")).toBeTruthy();
  });

  test("opens model governance from the administrator workspace", async () => {
    window.history.replaceState({}, "", "/admin/models");
    render(<App />);
    expect(await screen.findByRole("heading", { name: "模型治理" })).toBeTruthy();
    expect(await screen.findByText("业务模型与版本")).toBeTruthy();
    expect(screen.getByRole("navigation", { name: "管理员导航" })).toBeTruthy();
  });

  test("renders the modeling goal section with a neutral surface banner", async () => {
    window.history.replaceState({}, "", "/workspace/modeling");
    render(<App />);

    await screen.findByRole("heading", { name: "智能建模" });
    const title = await screen.findByText("可以开始识别高风险生产记录");
    const section = title.closest("section");

    expect(section).toBeTruthy();
    expect(section.className).toContain("goal-banner--neutral");
    expect(section.className).not.toContain("bg-ink");
  });

  test("renders modeling steps with visible completion borders", async () => {
    window.history.replaceState({}, "", "/workspace/modeling");
    const { container } = render(<App />);

    await screen.findByRole("heading", { name: "智能建模" });
    fireEvent.click(screen.getByRole("button", { name: /训练过程/ }));
    const firstStep = await screen.findByText("理解建模目标");
    const item = firstStep.closest(".modeling-step");
    const badge = item?.querySelector("span:not(.modeling-step__connector)");

    expect(container).toBeTruthy();
    expect(badge).toBeTruthy();
    expect(badge.className).toContain("border-green/50");
  });

  test("renders modeling workflow connectors between steps", async () => {
    window.history.replaceState({}, "", "/workspace/modeling");
    render(<App />);

    await screen.findByRole("heading", { name: "智能建模" });
    fireEvent.click(screen.getByRole("button", { name: /训练过程/ }));
    const firstStep = await screen.findByText("理解建模目标");
    const step = firstStep.closest(".modeling-step");
    const connector = step?.querySelector(".modeling-step__connector");

    expect(step).toBeTruthy();
    expect(connector).toBeTruthy();
  });

  test("keeps the modeling connector separate from the step badge", async () => {
    window.history.replaceState({}, "", "/workspace/modeling");
    render(<App />);

    await screen.findByRole("heading", { name: "智能建模" });
    fireEvent.click(screen.getByRole("button", { name: /训练过程/ }));
    const firstStep = await screen.findByText("理解建模目标");
    const step = firstStep.closest(".modeling-step");
    const badge = step?.querySelector("span:not(.modeling-step__connector)");
    const connector = step?.querySelector(".modeling-step__connector");

    expect(step).toBeTruthy();
    expect(badge).toBeTruthy();
    expect(connector).toBeTruthy();
    expect(badge.className).not.toContain("modeling-step__connector");
  });

  test("renders the modeling connector as a dashed line", async () => {
    window.history.replaceState({}, "", "/workspace/modeling");
    render(<App />);

    await screen.findByRole("heading", { name: "智能建模" });
    fireEvent.click(screen.getByRole("button", { name: /训练过程/ }));
    const firstStep = await screen.findByText("理解建模目标");
    const step = firstStep.closest(".modeling-step");
    const connector = step?.querySelector(".modeling-step__connector");

    expect(connector).toBeTruthy();
    expect(connector.className).toContain("modeling-step__connector--dashed");
  });

  test("toggles dark mode from the sidebar user section", async () => {
    render(<App />);

    expect(document.documentElement.getAttribute("data-theme")).not.toBe("dark");

    const toggle = await screen.findByRole("button", { name: "暗夜模式" });
    fireEvent.click(toggle);
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");

    fireEvent.click(toggle);
    expect(document.documentElement.getAttribute("data-theme")).not.toBe("dark");
  });

  test("marks the new task button with its primary variant for dark theme styling", async () => {
    render(<App />);

    const action = await screen.findByRole("button", { name: "新建数据任务" });
    expect(action.getAttribute("data-variant")).toBe("primary");
  });

  test("keeps a dedicated class on the new task button for dark mode contrast fixes", async () => {
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "暗夜模式" }));
    const action = await screen.findByRole("button", { name: "新建数据任务" });
    expect(action.className).toContain("sidebar-create-task");
  });

  test("slides the sidebar shell between expanded and collapsed states", async () => {
    render(<App />);

    const shell = await screen.findByTestId("app-sidebar");
    expect(shell.className).toContain("app-sidebar-shell");
    expect(shell.getAttribute("data-collapsed")).toBe("false");

    fireEvent.click(await screen.findByRole("button", { name: "折叠侧边栏" }));
    expect(shell.getAttribute("data-collapsed")).toBe("true");
    expect(shell.style.width).toBe("82px");

    fireEvent.click(screen.getByRole("button", { name: "展开侧边栏" }));
    expect(shell.getAttribute("data-collapsed")).toBe("false");
    expect(shell.style.width).toBe("252px");
  });

  test("places the dark mode toggle on the right side of the sidebar footer", async () => {
    render(<App />);

    const toggle = await screen.findByRole("button", { name: "暗夜模式" });
    const footer = toggle.closest(".mt-auto");
    const children = Array.from(footer?.children ?? []);
    const toggleIndex = children.indexOf(toggle);

    expect(footer).toBeTruthy();
    expect(toggleIndex).toBe(children.length - 2);
  });

  test("connects a new MySQL/PostgreSQL data source from the administrator workspace", async () => {
    window.history.replaceState({}, "", "/admin/resources");
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "接入数据源" }));
    // 第一步：类型画廊；第二步：选中 MySQL 后出现配置表单
    expect(screen.getByRole("heading", { name: "选择数据源类型" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /^MySQL mysql/ }));
    expect(screen.getByRole("heading", { name: "配置 MySQL 连接" })).toBeTruthy();
    fireEvent.change(screen.getByLabelText("数据源名称"), { target: { value: "华南工厂数据底座" } });
    fireEvent.change(screen.getByLabelText("数据库名"), { target: { value: "manufacturing" } });
    fireEvent.change(screen.getByLabelText("只读账号"), { target: { value: "forgesight_reader" } });
    fireEvent.click(screen.getByRole("button", { name: "保存并测试连接" }));
    await waitFor(() => expect(screen.getByText("连接成功")).toBeTruthy(), { timeout: 2000 });
  });

  test("aligns knowledge metric row columns to the right for better balance", async () => {
    window.history.replaceState({}, "", "/admin/knowledge");
    render(<App />);

    await screen.findByText("业务知识库");
    const metricName = await screen.findByText("工序良率", { selector: "b" });
    const row = metricName.closest("button");

    expect(row).toBeTruthy();
    expect(row.querySelectorAll(".knowledge-metric-col--right").length).toBe(3);
  });

  test("renders the resource schema graph inside a shared responsive canvas", async () => {
    window.history.replaceState({}, "", "/admin/resources");
    const { container } = render(<App />);

    await screen.findByText("生产—质量—设备关联图");
    expect(container.querySelector("[data-testid='schema-graph-canvas']")).toBeTruthy();
  });

  test("clarifies ambiguous questions before executing", async () => {
    window.history.replaceState({}, "", "/workspace/analysis");
    render(<App />);
    await screen.findByText(/2,752/, {}, { timeout: 3000 });
    const textarea = screen.getByPlaceholderText(/用业务语言/);
    fireEvent.change(textarea, { target: { value: "看看最近良率怎么样" } });
    fireEvent.submit(textarea.closest("form"));
    expect(await screen.findByRole("button", { name: "本月至今 · 按工序统计" }, { timeout: 3000 })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "本月至今 · 按工序统计" }));
    expect(await screen.findByText(/已按你选择的口径/, {}, { timeout: 10000 })).toBeTruthy();
  }, 15000);

  test("blocks write operations before execution", async () => {
    window.history.replaceState({}, "", "/workspace/analysis");
    render(<App />);
    const textarea = await screen.findByPlaceholderText("用业务语言描述你想分析的问题…");
    await screen.findByText(/2,752/, {}, { timeout: 3000 });
    fireEvent.change(textarea, { target: { value: "删除上月库存数据" } });
    fireEvent.click(screen.getByRole("button", { name: "开始分析" }));
    expect(await screen.findByText("已拦截本次请求", {}, { timeout: 3000 })).toBeTruthy();
    expect(screen.getByText(/仅支持只读分析/)).toBeTruthy();
  }, 10000);
});
