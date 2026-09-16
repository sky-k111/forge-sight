import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { vi } from "vitest";
import App from "./App";
import {
  businessObjects,
  businessRules,
  businessTopics,
  metrics,
} from "./mocks/data";

const okJson = (data) =>
  Promise.resolve({ ok: true, json: () => Promise.resolve(data) });

const ResizeObserverStub = class ResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
};

function makeAnalysisSnapshot({
  id = "run-real",
  status = "succeeded",
  question = "分析 7 月各工序良率，并与各工序标准值比较",
} = {}) {
  const taskId = `${id}-task`;
  const task = {
    id: taskId,
    order: 1,
    title: "实际良率与工序标准值",
    question,
    mode: "single",
    analysisType: "comparison",
    status,
  };
  const stepDefs = [
    ["intent.guard", "检查只读分析边界"],
    ["intent.parse", "理解并拆解业务问题"],
    ["context.retrieve", "定位数据资源"],
    ["query.generate", "生成只读 SQL"],
    ["query.validate", "安全与语义校验"],
    ["query.execute", "执行并检查结果"],
    ["result.explain", "生成图表与结论"],
  ];
  const steps = stepDefs.map(([stepKey, label], index) => ({
    id: `${id}-step-${index}`,
    taskId: index < 2 ? null : taskId,
    stepKey,
    stepOrder: index,
    attempt: 1,
    status: "succeeded",
    label,
    detail: "已完成",
  }));
  const artifacts = [
    {
      id: `${id}-ctx`,
      taskId,
      type: "context",
      content: {
        coverage: { metricCount: 1, tableCount: 2 },
        metrics: [{ id: "M-001", name: "工序良率", version: "2.3" }],
        tables: [{ name: "mes_process_output", cnName: "工序产量表" }],
      },
    },
    {
      id: `${id}-sql`,
      taskId,
      type: "sql",
      content: {
        sql: "SELECT process_name, yield_rate, target FROM process_yield LIMIT 100",
      },
    },
    {
      id: `${id}-val`,
      taskId,
      type: "validation",
      content: { ok: true, checks: ["只读通过", "白名单通过"] },
    },
    {
      id: `${id}-result`,
      taskId,
      type: "result",
      content: {
        columns: ["process_name", "yield_rate", "target"],
        rows: [
          ["装配", 98.7, 98.0],
          ["焊接", 97.9, 97.5],
          ["功能测试", 95.83, 96.5],
        ],
        row_count: 3,
        duration_ms: 1400,
      },
    },
    {
      id: `${id}-conclusion`,
      taskId,
      type: "conclusion",
      content: { text: "功能测试良率低于标准 0.67 个百分点。" },
    },
  ];
  const checkpoints = steps.map((step, index) => ({
    id: `${id}-ckpt-${index}`,
    stepKey: step.stepKey,
    summary: { taskId: step.taskId },
    rollbackable: true,
  }));
  return {
    id,
    sessionId: "ses-real",
    sourceId: 1,
    status,
    question,
    tasks: [task],
    steps,
    artifacts,
    checkpoints,
    interaction: null,
    assistantMessage: {
      content: "已分析 2,752 条工序产量记录，功能测试良率低于标准。",
    },
    usage: {
      callCount: 2,
      totalTokens: 1800,
      estimatedCost: 0.012,
      currency: "CNY",
    },
    createdAt: "2026-07-16T10:32:00+08:00",
  };
}

let analysisRuns;
let llmSettings;

beforeEach(() => {
  vi.stubEnv("VITE_DEMO_MODE", "true");
  globalThis.ResizeObserver = ResizeObserverStub;
  window.history.replaceState({}, "", "/workspace/overview");
  window.sessionStorage.clear();
  window.localStorage.clear();
  analysisRuns = { "run-real": makeAnalysisSnapshot() };
  llmSettings = {
    configured: true,
    baseUrl: "https://api.deepseek.com",
    model: "deepseek-chat",
    maskedKey: "sk-••••391e",
    activeProfileId: "deepseek",
    profiles: [
      { id: "deepseek", name: "DeepSeek 生产", protocol: "openai_compatible", authMode: "auto", baseUrl: "https://api.deepseek.com", model: "deepseek-chat", configured: true, active: true, maskedKey: "sk-••••391e" },
      { id: "qwen", name: "Qwen 测试", protocol: "openai_compatible", authMode: "auto", baseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1", model: "qwen-plus", configured: true, active: false, maskedKey: "sk-••••8820" },
    ],
  };
  // 资源中心与安全审计已接真实后端，测试环境统一打桩 /api/*，避免依赖本机服务
  vi.stubGlobal(
    "fetch",
    vi.fn((url, options) => {
      const u = String(url);
      const method = options?.method ?? "GET";
      if (u.endsWith("/api/model-settings/llm/discover-models") && method === "POST") {
        return okJson({ models: ["deepseek-chat", "deepseek-reasoner"] });
      }
      if (u.endsWith("/api/model-settings/llm") && method === "PATCH") {
        const payload = JSON.parse(options?.body ?? "{}");
        if (payload.activate && payload.profileId) {
          llmSettings = {
            ...llmSettings,
            activeProfileId: payload.profileId,
            profiles: llmSettings.profiles.map((profile) => ({ ...profile, active: profile.id === payload.profileId })),
          };
        }
        return okJson(llmSettings);
      }
      if (u.endsWith("/api/model-settings/llm") && method === "GET") return okJson(llmSettings);
      if (u.endsWith("/api/analysis/sessions") && method === "POST") {
        return okJson({
          id: "ses-created",
          title: "未命名分析",
          sourceId: 1,
          sourceName: "华东制造数据底座",
        });
      }
      if (u.includes("/api/analysis/sessions?") && method === "GET") {
        return okJson([
          {
            id: "ses-real",
            title: "制造业务问析",
            sourceId: 1,
            sourceName: "华东制造数据底座",
          },
        ]);
      }
      if (u.endsWith("/api/analysis/sessions/ses-real") && method === "GET") {
        return okJson({
          id: "ses-real",
          runs: Object.values(analysisRuns).map((run) => ({ id: run.id })),
        });
      }
      if (u.endsWith("/api/analysis/sessions/ses-real") && method === "PATCH") {
        const payload = JSON.parse(options?.body ?? "{}");
        return okJson({ sessionId: "ses-real", title: payload.title, displayTitle: payload.title });
      }
      if (u.endsWith("/api/analysis/sessions/bulk-delete") && method === "POST") {
        const payload = JSON.parse(options?.body ?? "{}");
        return okJson({ deletedIds: payload.sessionIds, deletedCount: payload.sessionIds.length, retention: "audit" });
      }
      if (
        u.includes("/api/analysis/sessions/ses-real?userId=") &&
        method === "DELETE"
      ) {
        return okJson({
          sessionId: "ses-real",
          deleted: true,
          retention: "audit",
        });
      }
      if (
        u.includes("/api/analysis/sessions/") &&
        u.endsWith("/runs") &&
        method === "POST"
      ) {
        const question = JSON.parse(options?.body ?? "{}").question ?? "";
        const id = `run-test-${Object.keys(analysisRuns).length}`;
        let snapshot = makeAnalysisSnapshot({ id, question });
        if (/删除|写入|更新/.test(question))
          snapshot = {
            ...snapshot,
            status: "blocked",
            errorCode: "WRITE_INTENT_BLOCKED",
            tasks: [],
            steps: [],
            artifacts: [],
            checkpoints: [],
            assistantMessage: null,
          };
        if (/最近良率/.test(question))
          snapshot = {
            ...snapshot,
            status: "waiting_user",
            assistantMessage: null,
            interaction: {
              id: `${id}-interaction`,
              prompt: {
                question: "请选择本次良率统计口径。",
                options: [{ label: "本月至今 · 按工序统计", value: "M-001" }],
                allowCustom: true,
              },
            },
          };
        if (/识别高不良|预测|建模/.test(question))
          snapshot = {
            ...makeAnalysisSnapshot({
              id,
              question,
              status: "modeling-proposal",
            }),
            proposal: {
              title: "工序产量表·分类预测模型",
              taskType: "classification",
              taskTypeLabel: "分类预测",
              goal: question,
              dataset: "工序产量表",
              datasetSql:
                "SELECT `工序`, `产线`, `停机时长`, `计划数量`, `high_defect_risk` FROM `mes_process_output` LIMIT 10000",
              sampleCount: 2752,
              target: "high_defect_risk",
              targetLabel: "高风险标记",
              metric: "AUC / F1 / Precision / Recall",
              features: ["工序", "产线", "停机时长", "计划数量"],
              candidates: ["logistic", "tree", "forest"],
            },
          };
        analysisRuns[id] = snapshot;
        return okJson({ runId: id, sessionId: "ses-real", status: "queued" });
      }
      if (
        u.includes("/api/analysis/interactions/") &&
        u.endsWith("/responses") &&
        method === "POST"
      ) {
        const run = Object.values(analysisRuns).find(
          (item) => item.interaction && u.includes(item.interaction.id),
        );
        analysisRuns[run.id] = {
          ...makeAnalysisSnapshot({ id: run.id, question: run.question }),
          assistantMessage: {
            content: "已按你选择的口径完成分析，共核对 2,752 条记录。",
          },
        };
        return okJson({
          runId: run.id,
          sessionId: "ses-real",
          status: "queued",
          resumed: true,
        });
      }
      if (
        u.includes("/api/analysis/checkpoints/") &&
        u.endsWith("/branches") &&
        method === "POST"
      ) {
        const payload = JSON.parse(options?.body ?? "{}");
        const id = "run-branch";
        analysisRuns[id] = makeAnalysisSnapshot({
          id,
          question: payload.instruction,
        });
        return okJson({
          branchId: "branch-1",
          session: {
            id: "ses-branch",
            title: "分析调整方案",
            sourceId: 1,
            parentSessionId: "ses-real",
          },
          branchContext: {
            branchId: "branch-1",
            sourceSessionId: "ses-real",
            sourceRunId: "run-real",
            sourceQuestion: analysisRuns["run-real"].question,
            checkpoint: {
              id: "run-real-ckpt-6",
              stepKey: "result.explain",
              rollbackable: true,
            },
            adjustments: payload.adjustments,
            reuseArtifacts: payload.reuseArtifacts,
          },
          run: { runId: id, sessionId: "ses-branch", status: "queued" },
        });
      }
      const runMatch = u.match(/\/api\/analysis\/runs\/([^/?]+)$/);
      if (runMatch && method === "GET")
        return okJson(analysisRuns[runMatch[1]]);
      if (u.endsWith("/api/datasources") && method === "POST")
        return okJson({ id: 2 });
      if (u.includes("/api/datasources") && u.endsWith("/test"))
        return okJson({ ok: true, status: "连接正常", message: "连接正常" });
      if (u.includes("/api/datasources") && u.endsWith("/scan"))
        return okJson({ tables: 1, fields: 6, relations: 0 });
      if (u.endsWith("/api/datasources")) {
        return okJson([
          {
            id: 1,
            name: "华东制造数据底座",
            type: "mysql",
            typeLabel: "MySQL",
            host: "127.0.0.1",
            port: 3306,
            database: "forge_sight",
            username: "forgesight_reader",
            status: "连接正常",
            isDefault: true,
          },
        ]);
      }
      if (u.includes("/api/dashboard/query") && method === "POST") {
        const payload = JSON.parse(options?.body ?? "{}");
        if (payload.kind === "rank") {
          return okJson({
            state: "ready",
            metric: { id: "M-PROD", name: "产量", unit: "件" },
            columns: ["工序", "产量"],
            rows: [["PR01", 1480], ["PR02", 1360]],
            range: { start: "2026-07-09", anchor: "2026-07-15" },
          });
        }
        if (payload.kind === "trend") {
          return okJson({
            state: "ready",
            metric: { id: "M-PROD", name: "产量", unit: "件" },
            columns: ["日期", "产量"],
            rows: [["07-09", 1280], ["07-10", 1360], ["07-11", 1315]],
            range: { start: "2026-07-09", anchor: "2026-07-15" },
          });
        }
        return okJson({
          state: "ready",
          metric: { id: "M-PROD", name: "产量", unit: "件" },
          columns: ["产量"],
          rows: [[3955]],
          range: { start: "2026-07-09", anchor: "2026-07-15" },
        });
      }
      if (u.includes("/api/dashboard") && method === "GET") {
        const config = {
          version: 0,
          period: "last7",
          widgets: [
            { id: "kpi-M-PROD", kind: "kpi", metricId: "M-PROD", title: "产量", layout: { x: 0, y: 0, w: 3, h: 3, minW: 2, minH: 2 } },
            { id: "trend-M-PROD", kind: "trend", metricId: "M-PROD", title: "产量趋势", layout: { x: 0, y: 3, w: 9, h: 7, minW: 5, minH: 5 } },
            { id: "rank-M-PROD", kind: "rank", metricId: "M-PROD", dimension: "工序", title: "按工序对比", layout: { x: 0, y: 10, w: 4, h: 6, minW: 3, minH: 4 } },
            { id: "context", kind: "context", title: "调查与提醒", layout: { x: 9, y: 3, w: 3, h: 7, minW: 3, minH: 4 } },
            { id: "recent", kind: "recent", title: "近期调查", layout: { x: 0, y: 10, w: 8, h: 6, minW: 5, minH: 4 } },
            { id: "catalog", kind: "catalog", title: "可用指标", layout: { x: 8, y: 10, w: 4, h: 6, minW: 3, minH: 4 } },
          ],
        };
        return okJson({
          sourceId: 1,
          catalog: [{
            id: "M-PROD", name: "产量", definition: "指定周期内的合格数量与不良数量之和。", unit: "件",
            topic: "生产分析", version: "1.0", kinds: ["kpi", "trend", "rank"], dimensions: ["工序"],
          }, ...Array.from({ length: 10 }, (_, index) => ({
            id: `M-AUX-${index + 1}`, name: `辅助指标 ${index + 1}`, definition: "已发布辅助指标。", unit: "件",
            topic: "质量分析", version: "1.0", kinds: [], dimensions: [],
          }))],
          config,
          defaultConfig: config,
        });
      }
      if (u.includes("/api/dashboard") && method === "PUT") {
        const payload = JSON.parse(options?.body ?? "{}");
        return okJson({ ...payload, version: (payload.version ?? 0) + 1 });
      }
      if (u.includes("/api/overview/admin")) {
        return okJson({
          summary: {
            categoryCount: 3,
            detail: "3 个指标待审核 · 2 个字段待映射 · 1 条拦截记录待复核",
            baseline: "治理基线正常",
            lastCheck: "10:36",
          },
          health: [
            { icon: "knowledge", label: "业务语义", value: "93%", note: "77 个指标 · 3 个待审核", tone: "blue", page: "knowledge" },
            { icon: "database", label: "数据资源", value: "10", note: "10 张表 · 15 条关系", tone: "teal", page: "resources" },
            { icon: "model", label: "模型治理", value: "4", note: "2 个已发布 · 1 个待复核", tone: "violet", page: "model-governance" },
            { icon: "shield", label: "安全运行", value: "100%", note: "只读策略与审计已启用 · 拦截 1 次已记录", tone: "green", page: "security" },
          ],
          queue: [
            { index: "01", title: "审核“设备可用率”指标口径", note: "数据治理组 提交 · 设备 主题", status: "待审核", tone: "amber", page: "knowledge" },
            { index: "02", title: "确认数据字段映射", note: "2 个字段尚未补充业务中文名", status: "待映射", tone: "blue", page: "resources" },
            { index: "03", title: "复核危险 SQL 拦截记录", note: "DELETE FROM dim_process", status: "已拦截", tone: "red", page: "security" },
          ],
          todo: [
            { key: "metrics", label: "指标待审核", unit: "个", count: 3, caption: "质量、设备", page: "knowledge", tone: "amber" },
            { key: "fields", label: "字段待映射", unit: "个", count: 2, caption: "设备运行数据", page: "resources", tone: "blue" },
            { key: "intercepts", label: "拦截待复核", unit: "条", count: 1, caption: "今天 10:21", page: "security", tone: "red" },
          ],
          topics: [
            { name: "生产", coverage: 92 },
            { name: "质量", coverage: 96 },
            { name: "设备", coverage: 84 },
          ],
        });
      }
      if (u.includes("/api/overview")) {
        return okJson({
          source: {
            id: 1,
            name: "华东制造数据底座",
            type: "mysql",
            database: "forge_sight",
            status: "连接正常",
          },
          capability: {
            state: "ready",
            label: "可直接问析",
            detail: "已登记数据表并建立可执行的业务指标口径。",
            canAnalyze: true,
          },
          snapshot: {
            registeredTables: 10,
            records: 8019,
            latestData: "2026-07-15",
            earliestTableUpdate: "2026-07-15",
            datedTables: 10,
            lastScan: "2026-07-15T10:36:00",
            readOnly: true,
          },
          metrics: { trusted: 11, pendingBindings: 0 },
          topics: [
            {
              code: "PROD",
              name: "生产分析",
              description: "工单、产量与工序过程数据",
              color: "blue",
              tableCount: 3,
              metricCount: 2,
              metricNames: ["产量", "计划完成率"],
              mode: "metrics",
            },
          ],
          suggestions: [
            {
              id: "topic-PROD",
              title: "分析产量",
              prompt: "请分析当前数据源中产量的最近趋势",
              caption: "已验证指标：产量 / 计划完成率",
            },
          ],
          recent: {
            total: 1,
            items: [
              {
                id: "ses-real",
                title: "分析本月各工序良率并与目标值比较",
                updatedAt: "2026-07-16T10:32:00",
                status: "succeeded",
                runCount: 2,
              },
            ],
          },
        });
      }
      if (u.includes("/tables/unregistered")) return okJson([]);
      if (u.includes("/api/resources/tables")) return okJson([]);
      if (u.includes("/api/knowledge/topics")) return okJson(businessTopics);
      if (u.includes("/api/knowledge/objects")) return okJson(businessObjects);
      if (u.includes("/api/knowledge/rules")) return okJson(businessRules);
      if (u.includes("/api/knowledge/metrics")) return okJson(metrics);
      // 智能建模与模型治理已接真实后端，测试统一打桩 /api/v1/modeling/*
      if (u.includes("/api/v1/modeling/algorithms")) {
        return okJson({
          items: [
            {
              id: "linear",
              name: "线性回归",
              type: "数值预测",
              scene: "预测产量、停机时长",
              accent: "blue",
              family: "regression",
            },
            {
              id: "tree",
              name: "决策树",
              type: "分类/回归",
              scene: "可解释规则与阈值分析",
              accent: "teal",
              family: "tree",
            },
            {
              id: "forest",
              name: "随机森林",
              type: "分类/回归",
              scene: "稳健的集成预测与重要性",
              accent: "violet",
              family: "forest",
            },
            {
              id: "logistic",
              name: "逻辑回归",
              type: "二分类",
              scene: "风险识别、是否判定",
              accent: "amber",
              family: "logistic",
            },
            {
              id: "kmeans",
              name: "KMeans 聚类",
              type: "聚类",
              scene: "样本分组与模式发现",
              accent: "rose",
              family: "clustering",
            },
            {
              id: "isolation",
              name: "Isolation Forest",
              type: "异常检测",
              scene: "离群点与异常识别",
              accent: "indigo",
              family: "anomaly",
            },
          ],
        });
      }
      if (/\/api\/v1\/modeling\/tasks\/[^/]+\/diagnostics$/.test(u) && method === "POST") {
        return okJson({
          status: "ready",
          score: 96,
          sampleCount: 2752,
          usableSampleCount: 2752,
          featureCount: 4,
          blockers: [],
          warnings: [],
          checks: [
            { key: "sample_count", label: "样本规模", status: "pass", message: "共读取 2,752 条训练记录" },
            { key: "target", label: "预测目标", status: "pass", message: "目标字段完整" },
            { key: "leakage", label: "结果泄漏", status: "pass", message: "未发现明显标签泄漏信号" },
            { key: "split", label: "验证方式", status: "pass", message: "按业务实体分组留出" },
          ],
          recommendedSplit: "group",
          recommendedSplitLabel: "按业务实体分组留出",
        });
      }
      if (/\/api\/v1\/modeling\/tasks\/[^/]+\/versions$/.test(u) && method === "GET") {
        return okJson({ task: { id: "mt-demo" }, models: [], evaluations: [] });
      }
      if (u.includes("/api/v1/modeling/tasks") && method === "POST") {
        const body = JSON.parse(options?.body ?? "{}");
        return okJson({
          id: "mt-proposal",
          title: body.title ?? "建模任务",
          source: "agent",
          sourceLabel: "Agent 创建",
          owner: body.ownerId ?? "local-test-user",
          taskType: body.taskType ?? "classification",
          taskTypeLabel: "分类预测",
          status: "draft",
          statusLabel: "待训练",
          tone: "gray",
          updatedAt: "2026-07-16T10:32:00",
          goal: body.goal ?? "",
          dataset: body.datasetName ?? "",
          datasetLabel: body.datasetName ?? "",
          sampleCount: null,
          sampleSummary: "",
          targetLabel: body.targetLabel ?? "",
          targetField: body.targetField ?? "",
          features: body.features ?? [],
          candidates: body.candidates ?? [],
          selectedModel: null,
          selectedModelName: null,
          metric: null,
          runId: body.runId ?? null,
        });
      }
      if (
        /\/api\/v1\/modeling\/tasks\/[^/]+\/evaluation$/.test(u) &&
        method === "GET"
      ) {
        return okJson({
          models: [
            {
              algorithm: "logistic",
              name: "逻辑回归",
              family: "classification",
              metrics: { auc: 0.958, f1: 0.872, precision: 82.9, recall: 91.8 },
              featureImportance: [],
            },
            {
              algorithm: "tree",
              name: "决策树",
              family: "classification",
              metrics: { auc: 0.931, f1: 0.85, precision: 80.2, recall: 89.4 },
              featureImportance: [
                { name: "停机时长", importance: 42.3 },
                { name: "工序", importance: 31.1 },
              ],
            },
            {
              algorithm: "forest",
              name: "随机森林",
              family: "classification",
              metrics: { auc: 0.986, f1: 0.892, precision: 82.9, recall: 96.5 },
              featureImportance: [
                { name: "工序", importance: 38.0 },
                { name: "停机时长", importance: 35.2 },
                { name: "产线", importance: 26.8 },
              ],
            },
          ],
          recommendedModel: "forest",
          data: {
            sampleCount: 2752,
            trainCount: 2200,
            testCount: 552,
            features: ["工序", "产线", "停机时长", "计划数量"],
            targetField: "high_defect_risk",
            missingPolicy: "中位数填充",
            encodedFeatures: ["工序", "产线"],
          },
        });
      }
      if (u.includes("/api/v1/modeling/tasks") && method === "GET") {
        return okJson({
          items: [
            {
              id: "mt-demo",
              title: "高风险生产记录识别",
              source: "agent",
              sourceLabel: "Agent 创建",
              owner: "local-test-user",
              taskType: "classification",
              taskTypeLabel: "分类预测",
              status: "trained",
              statusLabel: "训练完成",
              tone: "green",
              updatedAt: "2026-07-16T10:32:00",
              goal: "识别高不良风险的生产记录",
              dataset: "工序产量表",
              datasetLabel: "工序产量表",
              sampleCount: 2752,
              sampleSummary: "高风险样本 851 个",
              targetLabel: "高风险标记",
              targetField: "high_defect_risk",
              features: ["工序", "产线", "停机时长", "计划数量"],
              candidates: ["logistic", "tree", "forest"],
              selectedModel: "forest",
              selectedModelName: "随机森林",
              metric: "AUC 0.986",
              runId: null,
            },
          ],
        });
      }
      if (u.includes("/api/v1/model-governance/models")) {
        return okJson({
          items: [
            {
              id: "mdl-1",
              taskId: "mt-demo",
              name: "高风险生产记录识别模型",
              version: "1",
              algorithm: "forest",
              algorithmName: "随机森林",
              owner: "local-test-user",
              dataset: "工序产量表",
              metric: "AUC 0.986",
              metrics: { auc: 0.986 },
              status: "pending",
              statusLabel: "待复核",
              tone: "gray",
              calls: 0,
              updatedAt: "2026-07-16T10:32:00",
            },
          ],
        });
      }
      if (u.endsWith("/api/auth/login") && method === "POST") {
        const body = JSON.parse(options?.body ?? "{}");
        const isAdmin = body.username === "admin";
        return okJson({
          token: "token-demo",
          user: {
            id: isAdmin ? "u-admin" : "u-1",
            username: body.username,
            displayName: isAdmin ? "林涛" : "张三",
            role: isAdmin ? "admin" : "business",
            title: isAdmin ? "系统管理员" : "业务人员",
            emailStatus: "verified",
          },
        });
      }
      if (u.endsWith("/api/auth/register") && method === "POST") {
        const body = JSON.parse(options?.body ?? "{}");
        return okJson({
          token: "token-demo",
          user: {
            id: "u-reg",
            username: body.username,
            displayName: body.displayName || body.username,
            role: "business",
            title: "业务人员",
            emailStatus: "pending_verification",
            externalIdentityRequired: true,
            externalIdentityBound: false,
          },
        });
      }
      if (u.endsWith("/api/auth/me") && method === "GET") {
        const cached = window.localStorage.getItem("forgesight:auth-user");
        if (cached) return okJson({ user: JSON.parse(cached) });
        return Promise.resolve({
          ok: false,
          status: 401,
          json: () => Promise.resolve({ detail: "请先登录" }),
        });
      }
      if (u.endsWith("/api/auth/logout") && method === "POST")
        return okJson({ ok: true });
      return okJson([]);
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  globalThis.ResizeObserver = ResizeObserverStub;
});

describe("ForgeSight application routes", () => {
  test("loads sidebar analysis sessions from the backend and opens the selected session", async () => {
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "发起分析" }));
    const session = await screen.findByRole("button", { name: "制造业务问析" });
    fireEvent.click(session);

    await waitFor(() =>
      expect(window.location.pathname).toBe("/workspace/analysis"),
    );
    expect(
      window.sessionStorage.getItem("forgesight:analysis-session-id"),
    ).toBe("ses-real");
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("/api/analysis/sessions?userId=local-test-user"),
      expect.any(Object),
    );
  });

  test("deletes a sidebar conversation only after explicit confirmation", { timeout: 15000 }, async () => {
    render(<App />);

    const main = await screen.findByRole("main");
    fireEvent.click(await within(main).findByRole("button", { name: "发起分析" }, { timeout: 5000 }));
    await waitFor(() => expect(window.location.pathname).toBe("/workspace/analysis"));
    await screen.findByRole("heading", { name: "分析证据链" }, { timeout: 5000 });
    fireEvent.click(
      await screen.findByRole("button", { name: "会话操作：制造业务问析" }, { timeout: 5000 }),
    );
    fireEvent.click(screen.getByRole("button", { name: "删除" }));
    fireEvent.click(screen.getByRole("button", { name: "删除" }));

    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "制造业务问析" })).toBeNull(),
    );
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining(
        "/api/analysis/sessions/ses-real?userId=local-test-user",
      ),
      expect.objectContaining({ method: "DELETE" }),
    );
  });

  test("renders the governed business dashboard without placeholder metrics", async () => {
    const { container } = render(<App />);
    expect(
      await screen.findByRole("heading", { name: "业务看板" }),
    ).toBeTruthy();
    expect(
      await screen.findByText("华东制造数据底座", {}, { timeout: 5000 }),
    ).toBeTruthy();
    expect(screen.getByRole("button", { name: "近 7 天" }).getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByText("MySQL · forge_sight")).toBeTruthy();
    expect(screen.getByText("企业已发布口径")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "产量趋势" })).toBeTruthy();
    expect(await screen.findByRole("heading", { name: "产量 · 按工序对比" })).toBeTruthy();
    expect(screen.getByText("对比维度：工序")).toBeTruthy();
    expect(screen.getByText("单位：件")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "调查与提醒" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "近期调查" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "可用指标" })).toBeTruthy();
    expect(screen.getByText("辅助指标 10")).toBeTruthy();
    expect(screen.getByText("已显示全部 11 项")).toBeTruthy();
    expect(screen.getByText("分析本月各工序良率并与目标值比较")).toBeTruthy();
    expect(screen.queryByText(/阶段5|EXPLAIN|验收/)).toBeNull();
    expect(screen.queryByRole("heading", { name: "今天的工作队列" })).toBeNull();
    expect(screen.queryByRole("heading", { name: "从已有业务口径发起分析" })).toBeNull();
    expect(screen.queryByText("P0")).toBeNull();
    expect(screen.queryByText("赛题必须项")).toBeNull();
    expect(screen.queryByText("查看数据资源")).toBeNull();
    expect(screen.queryByText("待确认新表")).toBeNull();
    expect(screen.queryByText("表关系已识别")).toBeNull();
    expect(
      screen.getByAltText("ForgeSight logo").getAttribute("src"),
    ).toContain("forgesight-logo");
    expect(container.querySelector('[data-icon="analysis"]')).toBeTruthy();
    expect(container.querySelector('[data-icon="database"]')).toBeTruthy();
    expect(container.querySelector('[data-icon="question"]')).toBeNull();
    expect(container.querySelector('[data-icon="spark"]')).toBeNull();
  });

  test("stages personal dashboard edits and saves them explicitly", async () => {
    render(<App />);
    await screen.findByRole("heading", { name: "业务看板" });
    const toolbar = screen.getByRole("region", { name: "看板工具栏" });

    fireEvent.click(within(toolbar).getByRole("button", { name: "编辑布局" }));
    fireEvent.click(screen.getByRole("button", { name: "恢复默认" }));
    expect(await screen.findByText("已恢复默认布局，保存后生效")).toBeTruthy();
    expect(fetch.mock.calls.some(([url, options]) => String(url).includes("/api/dashboard") && options?.method === "DELETE")).toBe(false);

    fireEvent.keyDown(screen.getByRole("button", { name: "移动或调整产量趋势组件" }), { key: "ArrowRight" });
    fireEvent.click(within(toolbar).getByRole("button", { name: "保存布局" }));
    await waitFor(() => expect(fetch.mock.calls.some(([url, options]) => (
      String(url).includes("/api/dashboard") && options?.method === "PUT"
    ))).toBe(true));
    expect(screen.queryByRole("region", { name: "布局编辑操作" })).toBeNull();
  });

  test("restores a removed workspace widget from the add component dialog", async () => {
    render(<App />);
    await screen.findByRole("heading", { name: "业务看板" });
    await waitFor(() => expect(document.querySelector(".dashboard-spinner")).toBeNull());
    const toolbar = screen.getByRole("region", { name: "看板工具栏" });

    fireEvent.click(within(toolbar).getByRole("button", { name: "编辑布局" }));
    fireEvent.click(screen.getByRole("button", { name: "移除近期调查组件" }));
    expect(screen.queryByRole("heading", { name: "近期调查" })).toBeNull();

    fireEvent.click(within(screen.getByRole("region", { name: "布局编辑操作" })).getByRole("button", { name: "添加组件" }));
    const dialog = screen.getByRole("dialog", { name: "添加看板组件" });
    fireEvent.click(within(dialog).getByRole("button", { name: "工作区组件" }));
    fireEvent.click(within(dialog).getByRole("button", { name: /近期调查.*可添加/ }));
    expect(within(dialog).getByRole("button", { name: /调查与提醒.*已添加/ }).disabled).toBe(true);
    expect(within(dialog).getByRole("button", { name: /可用指标.*已添加/ }).disabled).toBe(true);
    fireEvent.click(within(dialog).getByRole("button", { name: "添加组件" }));

    expect(screen.getByRole("heading", { name: "近期调查" })).toBeTruthy();
    expect(screen.queryByRole("dialog", { name: "添加看板组件" })).toBeNull();
  });

  test("stages a restrained widget accent and size preset with the dashboard layout", async () => {
    render(<App />);
    await screen.findByRole("heading", { name: "业务看板" });
    await waitFor(() => expect(document.querySelector(".dashboard-spinner")).toBeNull());
    const toolbar = screen.getByRole("region", { name: "看板工具栏" });

    fireEvent.click(within(toolbar).getByRole("button", { name: "编辑布局" }));
    fireEvent.click(screen.getByRole("button", { name: "设置产量趋势组件外观" }));
    const dialog = screen.getByRole("dialog", { name: "组件外观与尺寸" });
    fireEvent.click(within(dialog).getByRole("button", { name: /图表类型：面积图/ }));
    fireEvent.click(within(dialog).getByRole("button", { name: "强调色：紫灰" }));
    fireEvent.click(within(dialog).getByRole("button", { name: /重点.*扩大为主要内容/ }));
    fireEvent.click(within(dialog).getByRole("button", { name: "完成" }));

    const trendWidget = screen.getByRole("heading", { name: "产量趋势" }).closest("article");
    expect(trendWidget.getAttribute("data-accent")).toBe("violet");
    fireEvent.click(within(toolbar).getByRole("button", { name: "保存布局" }));
    await waitFor(() => {
      const put = fetch.mock.calls.findLast(([url, options]) => String(url).includes("/api/dashboard") && options?.method === "PUT");
      const savedTrend = JSON.parse(put[1].body).widgets.find((item) => item.id === "trend-M-PROD");
      expect(savedTrend.appearance).toEqual({ accent: "violet", chartType: "area" });
      expect(savedTrend.layout).toMatchObject({ w: 12, h: 8 });
    });
  });

  test("opens the personal profile from the account avatar", async () => {
    render(<App />);
    await screen.findByRole("heading", { name: "业务看板" });

    expect(screen.queryByRole("button", { name: "我的偏好与记忆" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "打开个人主页" }));

    await waitFor(() => expect(window.location.pathname).toBe("/workspace/profile"));
    expect(await screen.findByRole("heading", { name: "个人主页" }, { timeout: 5000 })).toBeTruthy();
    expect(await screen.findByRole("heading", { name: "绑定方式" }, { timeout: 5000 })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "个人记忆" })).toBeTruthy();
    expect(screen.getByText("钉钉")).toBeTruthy();
    expect(screen.getByText("飞书")).toBeTruthy();
    expect(screen.getByText("邮箱")).toBeTruthy();
    expect(document.querySelector('[data-provider="dingtalk"] svg')).toBeTruthy();
    expect(document.querySelector('[data-provider="feishu"] img')).toBeTruthy();
    expect(document.querySelector('[data-provider="dingtalk"]')?.textContent).toBe("");
    expect(document.querySelector('[data-provider="feishu"]')?.textContent).toBe("");
    expect(screen.getByRole("heading", { name: "模型配置" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /管理配置/ }));
    const dialog = screen.getByRole("dialog", { name: "管理模型配置" });
    expect(dialog).toBeTruthy();
    expect(dialog.querySelector('[data-icon="cpu"]')).toBeNull();
    expect(screen.getByText(/LLM_API_KEY=请填入你的服务商密钥/)).toBeTruthy();

    fireEvent.click(dialog.parentElement);
    expect(screen.getByRole("dialog", { name: "管理模型配置" })).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "检测模型" }));
    const detectedModels = await screen.findByRole("listbox", { name: "检测到的模型" });
    fireEvent.click(within(detectedModels).getByRole("option", { name: "deepseek-reasoner" }));
    expect(screen.getByLabelText("模型名称").value).toBe("deepseek-reasoner");

    const protocolSelect = screen.getByLabelText("API 协议");
    fireEvent.change(protocolSelect, { target: { value: "anthropic_messages" } });
    await waitFor(() => expect(protocolSelect.value).toBe("anthropic_messages"));
    expect(await screen.findByLabelText("认证方式")).toBeTruthy();
    expect(screen.getByText(/直连 Anthropic 时自动使用 x-api-key/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /Qwen 测试/ }));
    await waitFor(() => expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("/api/model-settings/llm"),
      expect.objectContaining({ method: "PATCH", body: expect.stringContaining('"profileId":"qwen"') }),
    ));
    expect(screen.getByRole("dialog", { name: "管理模型配置" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "切换为当前" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "完成" }));
    expect(screen.queryByRole("dialog", { name: "管理模型配置" })).toBeNull();
  });

  test("navigates to a directly addressable analysis page", async () => {
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "发起分析" }));
    await waitFor(() =>
      expect(window.location.pathname).toBe("/workspace/analysis"),
    );
  });

  test("renames a conversation and supports compact bulk management", async () => {
    render(<App />);
    const main = await screen.findByRole("main");
    fireEvent.click(await within(main).findByRole("button", { name: "发起分析" }, { timeout: 5000 }));
    await screen.findByRole("button", { name: "制造业务问析" });

    fireEvent.click(screen.getByRole("button", { name: "会话操作：制造业务问析" }));
    fireEvent.click(screen.getByRole("button", { name: "重命名" }));
    const nameInput = screen.getByLabelText("会话名称");
    fireEvent.change(nameInput, { target: { value: "六月质量复盘" } });
    fireEvent.click(screen.getByRole("button", { name: "保存会话名称" }));
    expect(await screen.findByRole("button", { name: "六月质量复盘" })).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "整理" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "选择会话：六月质量复盘" }));
    expect(screen.getByText("已选 1 项")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "批量删除" }));
    fireEvent.click(screen.getByRole("button", { name: "确认删除" }));

    await waitFor(() => expect(screen.queryByRole("button", { name: "六月质量复盘" })).toBeNull());
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("/api/analysis/sessions/bulk-delete"),
      expect.objectContaining({ method: "POST", body: JSON.stringify({ sessionIds: ["ses-real"] }) }),
    );
  });

  test("switches the per-run model directly from the analysis composer", async () => {
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "发起分析" }));
    await screen.findByText("DeepSeek 生产");

    const trigger = screen.getByRole("button", { name: "选择本次分析模型" });
    expect(trigger.querySelector('[data-icon="cpu"]')).toBeNull();
    fireEvent.click(trigger);

    const picker = screen.getByRole("listbox", { name: "选择本次分析模型" });
    fireEvent.click(within(picker).getByRole("option", { name: /Qwen 测试/ }));
    expect(window.sessionStorage.getItem("forgesight:active-model-profile-id")).toBe("qwen");
    expect(screen.queryByRole("listbox", { name: "选择本次分析模型" })).toBeNull();
    expect(screen.getByText("Qwen 测试")).toBeTruthy();
  });

  test("opens smart analysis without creating an empty recent investigation", async () => {
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "智能分析" }));
    await waitFor(() => expect(window.location.pathname).toBe("/workspace/analysis"));
    expect(
      await screen.findByRole("heading", { name: "从一个问题开始，进入你的数据现场" }),
    ).toBeTruthy();
    expect(screen.queryByRole("button", { name: "分析证据链" })).toBeNull();

    const emptySessionCreates = fetch.mock.calls.filter(([url, options]) => (
      String(url).endsWith("/api/analysis/sessions")
      && (options?.method ?? "GET") === "POST"
    ));
    expect(emptySessionCreates).toHaveLength(0);
  });

  test("opens the data scope selector from the analysis composer", async () => {
    window.history.replaceState({}, "", "/workspace/analysis");
    render(<App />);

    await screen.findByPlaceholderText("用业务语言描述你想分析的问题…", {}, { timeout: 5000 });
    fireEvent.click(screen.getByRole("button", { name: "选择分析数据范围" }));

    expect(screen.getByRole("listbox", { name: "选择分析数据范围" })).toBeTruthy();
    expect(screen.getByRole("option", { name: "华东制造数据底座" })).toBeTruthy();
  });

  test("renders analysis trace icons with the original node style instead of green span badges", async () => {
    window.history.replaceState({}, "", "/workspace/analysis");
    const { container } = render(<App />);

    await screen.findByText(/2,752/, {}, { timeout: 3000 });
    fireEvent.click(
      (await screen.findAllByRole("button", { name: /过程/ }))[0],
    );

    // Process groups are collapsed by default; expand the first one to reveal step nodes.
    const groupTrigger = await waitFor(() => {
      const trigger = container.querySelector(".evidence-step-group__trigger");
      expect(trigger).toBeTruthy();
      return trigger;
    });
    fireEvent.click(groupTrigger);

    await waitFor(() => expect(container.querySelector(".trace-step__node")).toBeTruthy());
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
    expect(shell.style.getPropertyValue("--evidence-panel-width")).toBe(
      "320px",
    );
  });

  test("keeps the sidebar toggle anchored to one slot before and after collapsing", async () => {
    render(<App />);

    await screen.findByRole("heading", { name: "产量趋势" });
    await waitFor(() => expect(document.querySelector(".dashboard-spinner")).toBeNull());
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

    expect(
      container.querySelector(".chart-frame.chart-frame--elevated"),
    ).toBeTruthy();
    expect(screen.getByText("执行记录已保存")).toBeTruthy();
    expect(screen.queryByText(/TRACE \/ run-real/)).toBeNull();
  });

  test("keeps the analysis bar and legend on the same series color", async () => {
    window.history.replaceState({}, "", "/workspace/analysis");
    const { container } = render(<App />);

    await screen.findByLabelText("实际良率与工序标准值柱状图");

    const chartFrame = container.querySelector("[data-chart-series-colors]");
    const legendMarker = container.querySelector("[data-chart-legend-color]");
    expect(
      chartFrame.getAttribute("data-chart-series-colors").split(",")[0],
    ).toBe("var(--color-primary)");
    expect(legendMarker.getAttribute("data-chart-legend-color")).toBe(
      chartFrame.getAttribute("data-chart-series-colors").split(",")[0],
    );
  });

  test("renders the administrator workspace without an identity switcher", async () => {
    window.history.replaceState({}, "", "/admin/overview");
    render(<App />);
    expect(
      await screen.findByRole("heading", { name: "治理工作台" }),
    ).toBeTruthy();
    expect(screen.getByRole("navigation", { name: "管理员导航" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "业务" })).toBeNull();
    expect(screen.queryByRole("button", { name: "管理" })).toBeNull();
    expect(
      screen
        .getByRole("button", { name: "数据与语义" })
        .getAttribute("aria-expanded"),
    ).toBe("true");
    expect(
      screen
        .getByRole("button", { name: "平台运维" })
        .getAttribute("aria-expanded"),
    ).toBe("false");
    expect(await screen.findByText("治理队列")).toBeTruthy();
    expect(window.location.pathname).toBe("/admin/overview");
    // 侧栏治理待办来自 /api/overview/admin 的真实聚合，而非写死文案
    const metricsTodo = await screen.findByText("3 个指标待审核");
    expect(metricsTodo.closest("button")).toBeTruthy();
    fireEvent.click(screen.getByText("1 条拦截待复核").closest("button"));
    expect(window.location.pathname).toBe("/admin/security");
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
    expect(
      container.querySelector("[data-testid='analysis-trace-canvas']"),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "调整条件" }));
    expect(
      await screen.findByRole("heading", { name: "调整分析条件" }),
    ).toBeTruthy();
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
    expect(
      await screen.findByRole("heading", { name: "智能建模" }),
    ).toBeTruthy();
    await screen.findByText("训练样本");
    expect(
      await screen.findByText(
        (_, element) =>
          element?.tagName === "SMALL" &&
          element.textContent.includes("2,752 个样本"),
      ),
    ).toBeTruthy();
    expect(
      screen.getByText(
        (_, element) =>
          element?.tagName === "SMALL" &&
          element.textContent.includes("高风险样本 851 个"),
      ),
    ).toBeTruthy();
    expect(screen.queryByText("2026-07-16T10:32:00")).toBeNull();
    expect(screen.queryByText(/TASK \/ mt-demo/)).toBeNull();
    expect(screen.queryByText("赛题必须项")).toBeNull();
  });

  test("submits a new question through the real analysis run contract", async () => {
    window.history.replaceState({}, "", "/workspace/analysis");
    render(<App />);
    await screen.findByText(/2,752/, {}, { timeout: 3000 });
    const textarea = screen.getByPlaceholderText(/用业务语言/);
    fireEvent.change(textarea, {
      target: { value: "找出最近一个月不良数量最高的产品" },
    });
    fireEvent.submit(textarea.closest("form"));
    expect(
      await screen.findByText(
        "找出最近一个月不良数量最高的产品",
        { selector: "p" },
        { timeout: 3000 },
      ),
    ).toBeTruthy();
    expect(await screen.findAllByText(/模型调用/)).not.toHaveLength(0);
  });

  test("switches between recommended and manually selected modeling strategies", async () => {
    window.history.replaceState({}, "", "/workspace/modeling");
    render(<App />);
    await screen.findByRole("heading", { name: "智能建模" });
    await screen.findByText("训练样本");
    fireEvent.click(await screen.findByRole("button", { name: /建模方案/ }));
    expect(screen.getByText("候选模型")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /人工指定/ }));
    expect(screen.getByText("指定算法")).toBeTruthy();
  });

  test("creates a modeling task from the agent proposal and stores the workspace draft", async () => {
    window.history.replaceState({}, "", "/workspace/analysis");
    render(<App />);
    await screen.findByText(/2,752/, {}, { timeout: 3000 });
    const textarea = screen.getByPlaceholderText(/用业务语言/);
    fireEvent.change(textarea, {
      target: { value: "识别高不良风险的生产记录" },
    });
    fireEvent.submit(textarea.closest("form"));
    expect(
      await screen.findByText("AGENT 建模方案", {}, { timeout: 3000 }),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "确认并创建任务" }));
    expect(
      await screen.findByText("已进入任务台", {}, { timeout: 3000 }),
    ).toBeTruthy();
    const draft = JSON.parse(
      window.sessionStorage.getItem("forgesight:modeling-draft") ?? "null",
    );
    expect(draft?.taskId).toBe("mt-proposal");
  });

  test("opens model governance from the administrator workspace", async () => {
    window.history.replaceState({}, "", "/admin/models");
    render(<App />);
    expect(
      await screen.findByRole("heading", { name: "复核与发布" }),
    ).toBeTruthy();
    expect(await screen.findByText("业务模型与版本")).toBeTruthy();
    expect(screen.getByRole("navigation", { name: "管理员导航" })).toBeTruthy();
  });

  test("renders the modeling goal section with a neutral surface banner", async () => {
    window.history.replaceState({}, "", "/workspace/modeling");
    render(<App />);

    await screen.findByRole("heading", { name: "智能建模" });
    const title = await screen.findByText("训练与隔离评估已完成");
    // 风格基线：结论行位于整页唯一的工作区容器内（单容器 + 分隔线，不叠卡片）
    const workspace = title.closest('[class*="rounded-panel"]');

    expect(workspace).toBeTruthy();
    expect(workspace.className).toContain("border-line");
    // 页面顶层只允许一个带边框工作区容器，不得再堆叠兄弟卡片
    const main = document.querySelector("main");
    const topPanels = [...main.querySelectorAll('[class*="rounded-panel"]')]
      .filter((node) => node.parentElement?.closest('[class*="rounded-panel"]') == null);
    expect(topPanels.length).toBeLessThanOrEqual(1);
  });

  test("renders modeling steps with visible completion borders", async () => {
    window.history.replaceState({}, "", "/workspace/modeling");
    const { container } = render(<App />);

    await screen.findByRole("heading", { name: "智能建模" });
    fireEvent.click(screen.getByRole("button", { name: /训练过程/ }));
    const firstStep = (await screen.findAllByText("训练数据体检")).find((node) => node.closest(".modeling-step"));
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
    const firstStep = (await screen.findAllByText("训练数据体检")).find((node) => node.closest(".modeling-step"));
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
    const firstStep = (await screen.findAllByText("训练数据体检")).find((node) => node.closest(".modeling-step"));
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
    const firstStep = (await screen.findAllByText("训练数据体检")).find((node) => node.closest(".modeling-step"));
    const step = firstStep.closest(".modeling-step");
    const connector = step?.querySelector(".modeling-step__connector");

    expect(connector).toBeTruthy();
    expect(connector.className).toContain("modeling-step__connector--dashed");
  });

  test("toggles dark mode from the sidebar user section", async () => {
    render(<App />);

    await screen.findByRole("heading", { name: "产量趋势" });
    await waitFor(() => expect(document.querySelector(".dashboard-spinner")).toBeNull());
    expect(document.documentElement.getAttribute("data-theme")).not.toBe(
      "dark",
    );

    const toggle = await screen.findByRole("button", { name: "暗夜模式" });
    fireEvent.click(toggle);
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");

    fireEvent.click(toggle);
    expect(document.documentElement.getAttribute("data-theme")).not.toBe(
      "dark",
    );
  });

  test("marks the new task button with its primary variant for dark theme styling", async () => {
    window.history.replaceState({}, "", "/workspace/analysis");
    render(<App />);

    const action = (await screen.findAllByRole("button", { name: "发起分析" })).find((item) => item.className.includes("sidebar-create-task"));
    expect(action.getAttribute("data-variant")).toBe("primary");
  });

  test("keeps a dedicated class on the new task button for dark mode contrast fixes", async () => {
    window.history.replaceState({}, "", "/workspace/analysis");
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "暗夜模式" }));
    const action = (await screen.findAllByRole("button", { name: "发起分析" })).find((item) => item.className.includes("sidebar-create-task"));
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
    expect(toggleIndex).toBe(children.length - 1);
  });

  test("connects a new MySQL/PostgreSQL data source from the administrator workspace", async () => {
    window.history.replaceState({}, "", "/admin/resources");
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "接入数据源" }));
    // 第一步：类型画廊；第二步：选中 MySQL 后出现配置表单
    expect(
      screen.getByRole("heading", { name: "选择数据源类型" }),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /^MySQL mysql/ }));
    expect(
      screen.getByRole("heading", { name: "配置 MySQL 连接" }),
    ).toBeTruthy();
    fireEvent.change(screen.getByLabelText("数据源名称"), {
      target: { value: "华南工厂数据底座" },
    });
    fireEvent.change(screen.getByLabelText("数据库名"), {
      target: { value: "manufacturing" },
    });
    fireEvent.change(screen.getByLabelText("只读账号"), {
      target: { value: "forgesight_reader" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存并测试连接" }));
    await waitFor(() => expect(screen.getByText("连接成功")).toBeTruthy(), {
      timeout: 2000,
    });
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
    expect(
      container.querySelector("[data-testid='schema-graph-canvas']"),
    ).toBeTruthy();
  });

  test("clarifies ambiguous questions before executing", async () => {
    window.history.replaceState({}, "", "/workspace/analysis");
    render(<App />);
    await screen.findByText(/2,752/, {}, { timeout: 3000 });
    const textarea = screen.getByPlaceholderText(/用业务语言/);
    fireEvent.change(textarea, { target: { value: "看看最近良率怎么样" } });
    fireEvent.submit(textarea.closest("form"));
    expect(
      await screen.findByRole(
        "button",
        { name: "本月至今 · 按工序统计" },
        { timeout: 3000 },
      ),
    ).toBeTruthy();
    fireEvent.click(
      screen.getByRole("button", { name: "本月至今 · 按工序统计" }),
    );
    expect(
      await screen.findByText(/已按你选择的口径/, {}, { timeout: 10000 }),
    ).toBeTruthy();
  }, 15000);

  test("blocks write operations before execution", async () => {
    window.history.replaceState({}, "", "/workspace/analysis");
    render(<App />);
    const textarea =
      await screen.findByPlaceholderText("用业务语言描述你想分析的问题…");
    await screen.findByText(/2,752/, {}, { timeout: 3000 });
    fireEvent.change(textarea, { target: { value: "删除上月库存数据" } });
    fireEvent.click(screen.getByRole("button", { name: "开始分析" }));
    expect(
      await screen.findByText("已拦截本次请求", {}, { timeout: 3000 }),
    ).toBeTruthy();
    expect(screen.getByText(/仅支持只读分析/)).toBeTruthy();
  }, 10000);
});

describe("用户注册登录（渐进式认证）", () => {
  test("renders the login page on /login", async () => {
    window.history.replaceState({}, "", "/login");
    render(<App />);

    expect(
      await screen.findByRole("heading", { name: "ForgeSight 企业数据底座" }),
    ).toBeTruthy();
    expect(screen.getByRole("tab", { name: "登录" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "登录" })).toBeTruthy();
    expect(screen.getByPlaceholderText(/3-30 位字母/)).toBeTruthy();
    expect(screen.queryByText("第三方账号绑定")).toBeNull();
    expect(window.localStorage.getItem("forgesight:auth-token")).toBeNull();
  });

  test("logs in, persists token, and lands on the business workspace", async () => {
    window.history.replaceState({}, "", "/login");
    render(<App />);

    fireEvent.change(await screen.findByPlaceholderText(/3-30 位字母/), {
      target: { value: "zhangsan" },
    });
    fireEvent.change(screen.getByPlaceholderText("至少 6 位"), {
      target: { value: "pass1234" },
    });
    fireEvent.click(screen.getByRole("button", { name: "登录" }));

    await waitFor(() =>
      expect(window.location.pathname).toBe("/workspace/overview"),
    );
    expect(window.localStorage.getItem("forgesight:auth-token")).toBe(
      "token-demo",
    );
    expect(
      JSON.parse(window.localStorage.getItem("forgesight:auth-user")).username,
    ).toBe("zhangsan");
    // 等待业务工作台真正渲染（AppShell 挂载），而非 LoginPage 上的「当前已登录」提示文本
    expect(
      await screen.findByRole("heading", { name: "业务看板" }),
    ).toBeTruthy();
    await waitFor(() => {
      expect(fetch).toHaveBeenCalledWith(
        expect.stringContaining("/api/analysis/sessions?userId=zhangsan"),
        expect.any(Object),
      );
    });
  });

  test("requires a third-party identity before completing business registration", async () => {
    window.history.replaceState({}, "", "/login");
    render(<App />);

    fireEvent.click(await screen.findByRole("tab", { name: "注册" }));
    fireEvent.change(await screen.findByPlaceholderText(/3-30 位字母/), {
      target: { value: "lisi" },
    });
    fireEvent.change(screen.getByPlaceholderText(/用于接收公告/), {
      target: { value: "lisi@example.com" },
    });
    fireEvent.change(screen.getByPlaceholderText("至少 6 位"), {
      target: { value: "pass1234" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "创建账号并继续绑定" }),
    );

    expect((await screen.findByRole("alert")).textContent).toContain(
      "注册前请选择并绑定钉钉或飞书账号",
    );
    expect(window.localStorage.getItem("forgesight:auth-user")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "钉钉账号绑定" }));
    fireEvent.click(
      screen.getByRole("button", { name: "创建账号并继续绑定" }),
    );

    expect(
      await screen.findByRole("heading", { name: "完成第三方账号绑定" }),
    ).toBeTruthy();
    expect(
      JSON.parse(window.localStorage.getItem("forgesight:auth-user")).role,
    ).toBe("business");
    expect(screen.getByRole("button", { name: "完成注册" }).disabled).toBe(
      true,
    );
    expect(
      window.localStorage.getItem("forgesight:registration-binding-provider"),
    ).toBe("dingtalk");
    expect(window.location.pathname).toBe("/login");
  });

  test("blocks a business user from /admin/* and redirects to the workspace", async () => {
    window.localStorage.setItem(
      "forgesight:auth-user",
      JSON.stringify({
        id: "u-1",
        username: "zhangsan",
        displayName: "张三",
        role: "business",
        title: "业务人员",
        emailStatus: "verified",
      }),
    );
    window.localStorage.setItem("forgesight:auth-token", "token-demo");
    window.history.replaceState({}, "", "/admin/overview");
    render(<App />);

    await waitFor(() =>
      expect(window.location.pathname).toBe("/workspace/overview"),
    );
    expect(
      await screen.findByRole("heading", { name: "业务看板" }),
    ).toBeTruthy();
    expect(screen.queryByRole("button", { name: "业务" })).toBeNull();
    expect(screen.queryByRole("button", { name: "管理" })).toBeNull();
  });

  test("lets an admin into the management workspace and shows the real identity", async () => {
    window.localStorage.setItem(
      "forgesight:auth-user",
      JSON.stringify({
        id: "u-admin",
        username: "admin",
        displayName: "林涛",
        role: "admin",
        title: "系统管理员",
        emailStatus: "verified",
      }),
    );
    window.localStorage.setItem("forgesight:auth-token", "token-demo");
    window.history.replaceState({}, "", "/admin/overview");
    render(<App />);

    expect(
      await screen.findByRole("heading", { name: "治理工作台" }),
    ).toBeTruthy();
    expect(await screen.findByText("林涛")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "业务" })).toBeNull();
    expect(screen.queryByRole("button", { name: "管理" })).toBeNull();
  });

  test("logs out, clears the token, and returns to the login page", async () => {
    window.localStorage.setItem(
      "forgesight:auth-user",
      JSON.stringify({
        id: "u-1",
        username: "zhangsan",
        displayName: "张三",
        role: "business",
        title: "业务人员",
        emailStatus: "verified",
      }),
    );
    window.localStorage.setItem("forgesight:auth-token", "token-demo");
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "退出登录" }));
    await waitFor(() => expect(window.location.pathname).toBe("/login"));
    expect(window.localStorage.getItem("forgesight:auth-token")).toBeNull();
    expect(window.localStorage.getItem("forgesight:auth-user")).toBeNull();
  });
});

describe("调查记录", () => {
  const inMain = () => within(screen.getByRole("main"));

  test("renders the session history page with filters and session rows", async () => {
    window.history.replaceState({}, "", "/workspace/sessions");
    render(<App />);

    expect(
      await screen.findByRole("heading", { name: "调查记录" }),
    ).toBeTruthy();
    // mock 返回的会话缺少 displayTitle/runCount/latestRunStatus，页面应容错展示
    const row = await inMain().findByRole("button", { name: /制造业务问析/ });
    expect(within(row).getByText("华东制造数据底座")).toBeTruthy(); // 数据源名同时出现在筛选下拉里，需限定在会话行内
    expect(within(row).getByText("未开始")).toBeTruthy();
    expect(within(row).getByText("0 次分析")).toBeTruthy();
    expect(inMain().getByText("0 次分析")).toBeTruthy();
    expect(inMain().getByLabelText("数据源")).toBeTruthy();
    expect(inMain().getByLabelText("状态")).toBeTruthy();
  });

  test("opens a historical session and resumes it in the analysis page", async () => {
    window.history.replaceState({}, "", "/workspace/sessions");
    render(<App />);

    fireEvent.click(
      await inMain().findByRole("button", { name: /制造业务问析/ }),
    );
    await waitFor(() =>
      expect(window.location.pathname).toBe("/workspace/analysis"),
    );
    // AnalysisPage 为 lazy 加载，输入框出现在异步挂载完成后，需用 findBy 等待
    expect(
      await screen.findByPlaceholderText("用业务语言描述你想分析的问题…"),
    ).toBeTruthy();
    // 恢复机制生效：load() 已读取并消费会话 id（移除即证明恢复流程已启动），且按该 id 请求了会话详情
    expect(
      window.sessionStorage.getItem("forgesight:analysis-session-id"),
    ).toBeNull();
    await waitFor(() => {
      expect(fetch).toHaveBeenCalledWith(
        expect.stringContaining("/api/analysis/sessions/ses-real"),
        expect.any(Object),
      );
    });
  });

  test("filters sessions by status", async () => {
    window.history.replaceState({}, "", "/workspace/sessions");
    render(<App />);

    await inMain().findByText("制造业务问析");
    fireEvent.change(inMain().getByLabelText("状态"), {
      target: { value: "succeeded" },
    });
    expect(inMain().queryByText("制造业务问析")).toBeNull();
    expect(inMain().getByText(/没有符合条件的会话/)).toBeTruthy();
  });
});
