import { describe, expect, test } from "vitest";

import { adaptAnalysisRun, parseAnalysisNarrative } from "./runViewAdapter";

function snapshotWithResult({ question, analysisType = "query", requestedChart = null, columns, rows, axis = null }) {
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
      axis,
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

  test("summarizes multi-task markdown without leaking table syntax into the result header", () => {
    const result = parseAnalysisNarrative(`1. ### 各工序良率趋势

| 工序 | 良率 |
|------|------|
| AOI检测 | 99.21% |

**结论：**
- 工序良率整体稳定，包装入库需重点关注。

2. ### 产品抽检异常

| 产品 | 不良率 |
|------|--------|
| P026 | 3.91% |

**结论：** 未识别出异常产品，建议继续关注 P026。`);

    expect(result.summary).toBe("已完成 2 项分析，关键结论如下。");
    expect(result.summary).not.toContain("|");
    expect(result.highlights).toEqual([
      { title: "各工序良率趋势", text: "工序良率整体稳定，包装入库需重点关注。" },
      { title: "产品抽检异常", text: "未识别出异常产品，建议继续关注 P026。" },
    ]);
  });

  test("does not treat a nested numbered recommendation list as additional analysis tasks", () => {
    const result = parseAnalysisNarrative(`1. 第一项分析没有返回停机记录。

**结论**：指定周期内无设备停机记录。

2. 第二项分析没有返回非计划停机记录。

建议检查：
1. 该周期内是否确实无停机事件；
2. 数据源中的记录是否完整。

如需进一步排查，可调整时间范围。`);

    expect(result.summary).toBe("已完成 2 项分析，关键结论如下。");
    expect(result.highlights).toHaveLength(2);
    expect(result.highlights[1].text).toContain("第二项分析");
  });

  test("keeps multi-task finding titles concise when task instructions contain report constraints", () => {
    const snapshot = snapshotWithResult({
      question: "生成设备周报",
      columns: ["设备", "停机分钟"],
      rows: [["EQ-01", 42]],
    });
    snapshot.tasks = [
      { id: "task-1", order: 1, title: "1. 汇总周期内设备停机时长并与上一周期比较。统计范围固定为 2026-08-05 至 2026-08-12。" },
      { id: "task-2", order: 2, title: "2. 按设备和产线分析周期内非计划停机排名与变化。统计范围固定为 2026-08-05 至 2026-08-12。" },
    ];
    snapshot.assistantMessage.content = "1. 停机时长比较。\n\n**结论**：当前周期无停机记录。\n\n2. 非计划停机排名。\n\n**结论**：当前周期无非计划停机记录。";

    const run = adaptAnalysisRun(snapshot);

    expect(run.conclusion.highlights.map((item) => item.title)).toEqual([
      "汇总周期内设备停机时长并与上一周期比较",
      "按设备和产线分析周期内非计划停机排名与变化",
    ]);
  });

  test("uses the explicit conclusion as the summary for a single markdown result table", () => {
    const result = parseAnalysisNarrative(`根据查询结果，当前周期没有停机记录。

结果表如下：

| 当前周期 | 上一周期 |
|---|---|
| 无数据 | 无数据 |

**结论**：指定周期内无设备停机记录，无法进行环比比较。`);

    expect(result.summary).toBe("指定周期内无设备停机记录，无法进行环比比较。");
    expect(result.summary).not.toContain("结果表");
  });

  test("preserves negative values in prose instead of turning them into findings", () => {
    const result = parseAnalysisNarrative("本月良率环比 - 2.4%，仍高于企业最低标准。");

    expect(result.summary).toBe("本月良率环比 - 2.4%，仍高于企业最低标准。");
    expect(result.highlights).toEqual([]);
  });

  test("preserves a negative value that follows punctuation", () => {
    const result = parseAnalysisNarrative("本月良率变化： - 2.4%，需要关注。");

    expect(result.summary).toBe("本月良率变化： - 2.4%，需要关注。");
    expect(result.highlights).toEqual([]);
  });

  test("keeps a recommendation-only numbered list inside one conclusion", () => {
    const result = parseAnalysisNarrative(`建议检查：
1. 核对统计时间范围；
2. 确认源表数据是否完整。`);

    expect(result.summary).toBe("建议检查： 1. 核对统计时间范围； 2. 确认源表数据是否完整。");
    expect(result.highlights).toEqual([]);
  });

  test("uses authoritative task titles for the backend's plain numbered conclusions", () => {
    const result = parseAnalysisNarrative(`1. 当前周期无设备停机记录。

2. 当前周期无非计划停机记录。`, {
      sectionTitles: ["停机时长比较", "非计划停机排名"],
    });

    expect(result.summary).toBe("已完成 2 项分析，关键结论如下。");
    expect(result.highlights).toEqual([
      { title: "停机时长比较", text: "当前周期无设备停机记录。" },
      { title: "非计划停机排名", text: "当前周期无非计划停机记录。" },
    ]);
  });

  test("removes markdown table blocks that omit outer pipe characters", () => {
    const result = parseAnalysisNarrative(`当前工序良率如下：

工序 | 良率
--- | ---
AOI检测 | 99.21%`);

    expect(result.summary).toBe("当前工序良率如下：");
    expect(result.summary).not.toContain("AOI检测");
  });

  test("uses a line chart and compact month-day labels for date dimensions", () => {
    const run = adaptAnalysisRun(snapshotWithResult({
      question: "按统计日期查看不良数量分布",
      columns: ["stat_date", "defect_qty"],
      axis: { key: "stat_date", kind: "time", grain: "day" },
      rows: [["2026-06-22", 120], ["2026-06-23", 150], ["2026-06-24", 132]],
    }));

    expect(run.chart.type).toBe("line");
    expect(run.chart.xTickFormatter("2026-06-22")).toBe("06-22");
  });

  test("renders ISO week keys as 第N周 on week axes", () => {
    const run = adaptAnalysisRun(snapshotWithResult({
      question: "查看6月份各产线的良率，按周展示",
      analysisType: "trend",
      columns: ["周", "良率"],
      axis: { key: "周", kind: "time", grain: "week" },
      rows: [["202622", 97.95], ["202623", 97.89], ["202624", 98.03]],
    }));

    expect(run.chart.type).toBe("line");
    expect(run.chart.xTickFormatter("202622")).toBe("第22周");
  });

  test("does not treat year-month keys (e.g. 202606) as ISO weeks", () => {
    const run = adaptAnalysisRun(snapshotWithResult({
      question: "查看6月份产量",
      analysisType: "trend",
      columns: ["月份", "产量"],
      axis: { key: "月份", kind: "time", grain: "month" },
      rows: [["202606", 120], ["202607", 150]],
    }));

    expect(run.chart.xTickFormatter("202606")).not.toBe("第6周");
    expect(run.chart.xTickFormatter("202606")).toBe("6月");
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
      axis: { key: "month", kind: "time", grain: "month" },
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
