import { describe, expect, test } from "vitest";

import { adaptAnalysisRun, parseAnalysisNarrative } from "./runViewAdapter";

function snapshotWithResult({ question, analysisType = "query", requestedChart = null, columns, rows }) {
  return {
    id: "run-chart",
    status: "succeeded",
    question,
    tasks: [{
      id: "task-1",
      order: 1,
      title: question,
      normalizedQuestion: question,
      analysisType,
      intent: { requestedChart },
    }],
    steps: [],
    checkpoints: [],
    artifacts: [{ id: "result-1", taskId: "task-1", type: "result", content: { columns, rows, row_count: rows.length } }],
    assistantMessage: { content: "分析完成。" },
  };
}

describe("analysis narrative adapter", () => {
  test("turns greeting and markdown bullets into a concise summary and findings", () => {
    const result = parseAnalysisNarrative("你好！根据查询结果，**没有找到合格数量的数据**，仅返回了不良数量信息。以下是基于现有数据的简要结论： - **不良数量最高的记录为**：2026-07-15，产品P0007，不良数量636。 - 产品P0014在多个日期中出现较高不良数量。");

    expect(result.summary).toBe("根据查询结果，没有找到合格数量的数据，仅返回了不良数量信息。");
    expect(result.highlights).toEqual([
      { title: "不良数量最高的记录为", text: "2026-07-15，产品P0007，不良数量636。" },
      { title: "发现 2", text: "产品P0014在多个日期中出现较高不良数量。" },
    ]);
  });

  test("uses a line chart and compact month-day labels for date dimensions", () => {
    const run = adaptAnalysisRun(snapshotWithResult({
      question: "按统计日期查看不良数量分布",
      columns: ["stat_date", "defect_qty"],
      rows: [["2026-06-22", 120], ["2026-06-23", 150], ["2026-06-24", 132]],
    }));

    expect(run.chart.type).toBe("line");
    expect(run.chart.xTickFormatter("2026-06-22")).toBe("06-22");
  });

  test("honors an explicitly requested line chart instead of falling back to bars", () => {
    const run = adaptAnalysisRun(snapshotWithResult({
      question: "使用折线图展示这个月的不良品数量",
      requestedChart: "line",
      columns: ["统计日", "不良品数量"],
      rows: [["07-01", 120], ["07-02", 150], ["07-03", 132]],
    }));

    expect(run.chart.type).toBe("line");
  });

  test("downgrades historical period comparisons that only returned one month", () => {
    const snapshot = snapshotWithResult({
      question: "这个月和上个月的良率比较",
      analysisType: "comparison",
      columns: ["month", "yield_rate"],
      rows: [["2026-06", 97.57]],
    });
    snapshot.artifacts.push({
      id: "conclusion-1",
      taskId: "task-1",
      type: "conclusion",
      content: {
        text: "仅获取到 2026 年 6 月的工序良率。",
        confidence: {
          score: 98,
          level: "high",
          label: "高可信度",
          components: { resultCompleteness: 100 },
          reasons: ["查询已成功执行。"],
        },
      },
    });

    const run = adaptAnalysisRun(snapshot);

    expect(run.conclusion.confidence.score).toBe(60);
    expect(run.conclusion.confidence.level).toBe("low");
    expect(run.conclusion.confidence.components.resultCompleteness).toBe(35);
    expect(run.conclusion.confidence.completenessIssues[0]).toContain("仅覆盖 1 个周期");
  });

  test("uses a focused automatic y-axis for narrow percentage changes", () => {
    const run = adaptAnalysisRun(snapshotWithResult({
      question: "这个月和上个月的良率比较",
      analysisType: "comparison",
      columns: ["month", "yield_rate"],
      rows: [["2026-06", 97.57], ["2026-07", 97.54]],
    }));

    expect(run.chart.type).toBe("line");
    expect(run.chart.yDomain[0]).toBeGreaterThan(97);
    expect(run.chart.yDomain[1]).toBeLessThan(98);
    expect(run.chart.yDomain[0]).toBeLessThanOrEqual(97.54);
    expect(run.chart.yDomain[1]).toBeGreaterThanOrEqual(97.57);
    expect(run.chart.unit).toBe("%");
  });

  test("uses a donut chart for small categorical share results", () => {
    const run = adaptAnalysisRun(snapshotWithResult({
      question: "查看各缺陷类型占比",
      columns: ["defect_type", "defect_qty"],
      rows: [["划伤", 12], ["裂纹", 8], ["尺寸偏差", 5]],
    }));

    expect(run.chart.type).toBe("donut");
    expect(run.chart.legend).toHaveLength(3);
  });

  test("uses a scatter chart for correlation analysis", () => {
    const run = adaptAnalysisRun(snapshotWithResult({
      question: "分析设备停机时长和不良率是否相关",
      analysisType: "correlation",
      columns: ["device_name", "downtime_minutes", "defect_rate"],
      rows: [["EQ-01", 30, 1.2], ["EQ-02", 65, 2.4], ["EQ-03", 18, 0.9]],
    }));

    expect(run.chart.type).toBe("scatter");
    expect(run.chart.xNumericKey).toBe("downtime_minutes");
    expect(run.chart.yNumericKey).toBe("defect_rate");
  });

  test("uses the business-safe no-match explanation and hides internal error codes", () => {
    const run = adaptAnalysisRun({
      id: "run-no-match",
      status: "failed",
      errorCode: "CONTEXT_NO_MATCH",
      error: {
        code: "CONTEXT_NO_MATCH",
        userSafeTitle: "当前数据暂时无法回答这个问题",
        userSafeMessage: "当前数据源未接入销售/出库数据；同时，产品主数据中未找到“泰罗奥特曼”。",
        suggestion: "请确认产品名称；如果想查询生产数量，可改为“生产了多少”。",
        reason: "capability_and_entity_missing",
        showCodeToUser: false,
      },
      tasks: [],
      steps: [],
      checkpoints: [],
      artifacts: [],
    });

    expect(run.error.title).toBe("当前数据暂时无法回答这个问题");
    expect(run.error.message).toContain("未找到“泰罗奥特曼”");
    expect(run.error.showCodeToUser).toBe(false);
  });

  test("turns task constraint failures into a business-safe explanation", () => {
    const run = adaptAnalysisRun({
      id: "run-task-mismatch",
      status: "failed",
      errorCode: "TASK_CONSTRAINT_MISMATCH",
      error: {
        code: "TASK_CONSTRAINT_MISMATCH",
        message: "月度比较必须按月份分组并分别返回本月与上月",
        retryable: true,
      },
      tasks: [],
      steps: [],
      checkpoints: [],
      artifacts: [],
    });

    expect(run.error.title).toBe("分析条件未能完整落实");
    expect(run.error.message).toContain("停止执行");
    expect(run.error.suggestion).toContain("证据链");
  });

  test("explains a failed MySQL execution plan in both the error card and evidence node", () => {
    const run = adaptAnalysisRun({
      id: "run-dialect-failure",
      status: "failed",
      errorCode: "QUERY_EXPLAIN_FAILED",
      error: {
        code: "QUERY_EXPLAIN_FAILED",
        message: "无法取得数据库执行计划：ProgrammingError",
      },
      tasks: [{ id: "task-1", order: 1, title: "月度良率比较" }],
      steps: [{
        id: "step-validation",
        taskId: "task-1",
        stepKey: "query.validate",
        stepOrder: 5,
        attempt: 1,
        status: "failed",
        label: "安全与语义校验",
        detail: "无法取得数据库执行计划：ProgrammingError",
        error: { code: "QUERY_EXPLAIN_FAILED", message: "无法取得数据库执行计划：ProgrammingError" },
      }],
      checkpoints: [],
      artifacts: [
        { id: "context-1", taskId: "task-1", type: "context", content: { source: { type: "mysql" } } },
        { id: "sql-1", taskId: "task-1", type: "sql", content: { sql: "SELECT * FROM a FULL OUTER JOIN b ON a.id = b.id" } },
        {
          id: "validation-1",
          taskId: "task-1",
          type: "validation",
          content: {
            ok: true,
            costGate: { ok: false, allowed: false, reason: "无法取得数据库执行计划：ProgrammingError" },
          },
        },
      ],
    });

    expect(run.error.title).toBe("SQL 与当前数据库方言不兼容");
    expect(run.error.message).toContain("MySQL 不支持的 FULL OUTER JOIN");
    expect(run.plan[0].status).toBe("failed");
    expect(run.planDetails["step-validation"].body).toContain("数据库因此无法生成执行计划");
    expect(run.planDetails["step-validation"].tags).toContain("原因：数据库方言不兼容");
  });

  test("uses a dual-axis combo chart for absolute and rate metrics", () => {
    const run = adaptAnalysisRun(snapshotWithResult({
      question: "用双轴图比较各产线产量和不良率",
      requestedChart: "combo",
      columns: ["产线", "产量", "不良率"],
      rows: [["一号线", 1200, 2.4], ["二号线", 980, 3.1], ["三号线", 1320, 1.9]],
    }));

    expect(run.chart.type).toBe("combo");
    expect(run.chart.bars[0].key).toBe("产量");
    expect(run.chart.lines[0].key).toBe("不良率");
    expect(run.chart.rightUnit).toBe("%");
    expect(run.result.kpis).toHaveLength(3);
  });

  test("uses a heatmap for two categorical dimensions and one measure", () => {
    const run = adaptAnalysisRun(snapshotWithResult({
      question: "用热力图展示工序和缺陷类型的分布",
      requestedChart: "heatmap",
      columns: ["工序", "缺陷类型", "不良数量"],
      rows: [["焊接", "划伤", 12], ["焊接", "裂纹", 8], ["装配", "划伤", 5]],
    }));

    expect(run.chart.type).toBe("heatmap");
    expect(run.chart.rows).toEqual(["焊接", "装配"]);
    expect(run.chart.columns).toEqual(["划伤", "裂纹"]);
    expect(run.chart.cells).toHaveLength(3);
  });

  test("keeps explicit table requests as structured results without a chart", () => {
    const run = adaptAnalysisRun(snapshotWithResult({
      question: "用表格展示各产品的不良数量",
      requestedChart: "table",
      columns: ["产品", "不良数量"],
      rows: [["控制器01", 12], ["传感器02", 8]],
    }));

    expect(run.chart).toBeNull();
    expect(run.execution.rows).toHaveLength(2);
    expect(run.result.kpis).toHaveLength(3);
  });
});
