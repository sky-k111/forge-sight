import { processYield, traceDetails, traceSteps } from "./data";

export const yieldRun = {
  id: "run-yield-20260716",
  sessionId: "session-yield",
  question: "分析 7 月各工序良率，并与各工序标准值比较",
  status: "succeeded",
  plan: traceSteps,
  planDetails: traceDetails,
  result: {
    kpis: [
      { label: "总体良率", value: "97.56%", caption: "3,187,009 件投入" },
      { label: "低于标准", value: "7 道", caption: "按各工序独立标准" },
      { label: "最大差距", value: "-0.67pp", caption: "功能测试", tone: "red" },
    ],
  },
  chart: {
    type: "bar",
    kicker: "工序良率 · 2026 年 7 月",
    title: "实际良率与工序标准值",
    data: processYield,
    xKey: "name",
    bars: [{ key: "value", name: "实际良率" }],
    lines: [{ key: "target", name: "工序标准", color: "var(--color-amber)" }],
    yDomain: [95, 100],
    unit: "%",
  },
  conclusion: {
    text: "已按工序主数据中的标准良率完成比较。统计 2026-07-01 至 2026-07-15 的 2,752 条工序产量记录，覆盖 8 道工序。",
    highlights: [
      { title: "功能测试偏差最大", text: "实际良率 95.83%，比 96.5% 标准低 0.67 个百分点。" },
      { title: "老化测试同样需要关注", text: "实际良率 96.20%，比 96.8% 标准低 0.60 个百分点。" },
      { title: "AOI 检测达到标准", text: "实际良率 99.23%，高于 99.0% 标准 0.23 个百分点。" },
    ],
    followUp: "分析功能测试的不良类型和产品分布",
  },
  execution: { engine: "PostgreSQL", durationMs: 1400, rowCount: 8 },
  sources: [
    { icon: "knowledge", title: "工序良率 v2.3", caption: "质量部 · 已发布" },
    { icon: "database", title: "mes_process_output", caption: "数据更新至 2026-07-15" },
  ],
  createdAt: "2026-07-16T10:32:00+08:00",
};

export const trendRun = {
  id: "run-trend",
  question: "统计每条产线最近 7 天的产量趋势",
  status: "succeeded",
  plan: [
    { id: "intent", label: "理解业务问题", caption: "产量趋势 · 按产线 · 最近 7 天", tone: "blue", duration: "0.6s" },
    { id: "semantic", label: "匹配业务口径", caption: "日产量 · 已发布 v1.4", tone: "teal", duration: "0.4s" },
    { id: "resource", label: "定位数据资源", caption: "mes_process_output · line_id", tone: "violet", duration: "0.7s" },
    { id: "query", label: "生成分析代码", caption: "PostgreSQL · 只读查询", tone: "blue", duration: "1.0s" },
    { id: "validate", label: "安全与语义校验", caption: "7 项检查全部通过", tone: "green", duration: "0.3s" },
    { id: "execute", label: "执行并检查结果", caption: "返回 21 行 · 已补齐日期断点", tone: "amber", duration: "1.2s" },
    { id: "explain", label: "生成图表与结论", caption: "折线图 · 2 条业务发现", tone: "teal", duration: "0.8s" },
  ],
  planDetails: {
    intent: { title: "业务意图", body: "统计最近 7 天各产线的日产量趋势，识别增长、下降与异常波动。", tags: ["指标：日产量", "维度：产线、日期", "时间：2026-07-09 至 2026-07-15"] },
    semantic: { title: "指标口径 · 日产量 v1.4", body: "日产量 = 良品数量 + 不良数量，按产线和统计日期聚合。", tags: ["SUM(good_qty + defect_qty)", "stat_date", "line_id"] },
    resource: { title: "数据资源匹配", body: "工序产量表提供产线、日期与产出数量，无需跨表关联。", tags: ["mes_process_output"] },
    query: {
      title: "实际执行 SQL",
      body: "SQL 基于已登记元数据生成，包含时间范围、聚合口径和返回行数限制。",
      code: "SELECT o.stat_date,\n  o.line_id,\n  SUM(o.good_qty + o.defect_qty) AS output_qty\nFROM mes_process_output o\nWHERE o.stat_date BETWEEN DATE '2026-07-09' AND DATE '2026-07-15'\nGROUP BY o.stat_date, o.line_id\nORDER BY o.stat_date, o.line_id\nLIMIT 100;",
    },
    validate: { title: "校验结果", body: "语句只读；表字段真实存在；包含时间范围和行数限制；聚合与分组匹配。", tags: ["只读通过", "字段通过", "口径通过", "资源限制通过"] },
    execute: { title: "执行摘要", body: "查询执行完成，耗时 1.2 秒，返回 21 行（3 条产线 × 7 天），日期序列无缺失。", tags: ["返回 21 行", "数据更新至 2026-07-15"] },
    explain: { title: "结论生成依据", body: "趋势判断基于各产线首末日产量对比与逐日变化方向，数值均来自查询结果。", tags: ["数值可追溯", "结论已校验"] },
  },
  result: {
    kpis: [
      { label: "7 日总产量", value: "107,198", caption: "3 条产线合计" },
      { label: "最高产线", value: "L01", caption: "37,778 件 · 稳步上升" },
      { label: "异常产线", value: "L03", caption: "-18.2% · 周内持续下滑", tone: "red" },
    ],
  },
  chart: {
    type: "line",
    kicker: "日产量 · 2026-07-09 至 07-15",
    title: "各产线最近 7 天产量趋势",
    data: [
      { date: "07-09", L01: 5321, L03: 5124, L05: 4988 },
      { date: "07-10", L01: 5288, L03: 5090, L05: 5061 },
      { date: "07-11", L01: 5402, L03: 5015, L05: 5147 },
      { date: "07-12", L01: 5376, L03: 4677, L05: 5230 },
      { date: "07-13", L01: 5433, L03: 4462, L05: 5308 },
      { date: "07-14", L01: 5451, L03: 4308, L05: 5372 },
      { date: "07-15", L01: 5507, L03: 4189, L05: 5449 },
    ],
    xKey: "date",
    series: [{ key: "L01", name: "L01 产线" }, { key: "L03", name: "L03 产线" }, { key: "L05", name: "L05 产线" }],
    unit: " 件",
  },
  conclusion: {
    text: "统计 2026-07-09 至 2026-07-15 三条产线共 107,198 件产量，L01、L05 平稳上升，L03 出现持续下滑。",
    highlights: [
      { title: "L03 产线持续下滑", text: "周内从 5,124 件降至 4,189 件，累计降幅 18.2%；其中 07-12 起下滑明显加速（单日 -6.7%），与 E0009 设备停机时长上升的时间吻合。" },
      { title: "L01、L05 平稳增长", text: "L01 7 日累计 37,778 件保持最高；L05 从 4,988 件升至 5,449 件，增幅 9.2%。" },
    ],
    followUp: "分析 L03 产线的设备停机与质量数据",
  },
  execution: { engine: "PostgreSQL", durationMs: 1200, rowCount: 21 },
  sources: [
    { icon: "knowledge", title: "日产量 v1.4", caption: "生产部 · 已发布" },
    { icon: "database", title: "mes_process_output", caption: "数据更新至 2026-07-15" },
  ],
};

export const defectRankRun = {
  id: "run-defect-rank",
  question: "找出不良数量最高的产品",
  status: "succeeded",
  plan: [
    { id: "intent", label: "理解业务问题", caption: "不良数量排行 · 按产品 · 最近 30 天", tone: "blue", duration: "0.6s" },
    { id: "semantic", label: "匹配业务口径", caption: "不良数量 · 已发布 v2.0", tone: "teal", duration: "0.4s" },
    { id: "resource", label: "定位数据资源", caption: "3 张表 · inspection_id 关联", tone: "violet", duration: "0.8s" },
    { id: "query", label: "生成分析代码", caption: "PostgreSQL · 只读查询", tone: "blue", duration: "1.0s" },
    { id: "validate", label: "安全与语义校验", caption: "7 项检查全部通过", tone: "green", duration: "0.3s" },
    { id: "execute", label: "执行并检查结果", caption: "返回 5 行 · 无异常", tone: "amber", duration: "1.1s" },
    { id: "explain", label: "生成图表与结论", caption: "柱状图 · 3 条业务发现", tone: "teal", duration: "0.8s" },
  ],
  planDetails: {
    intent: { title: "业务意图", body: "统计最近 30 天各产品的不良数量并排序，定位不良最集中的产品。", tags: ["指标：不良数量", "维度：产品", "时间：最近 30 天", "输出：Top 5"] },
    semantic: { title: "指标口径 · 不良数量 v2.0", body: "不良数量按缺陷明细逐条累加，关联检验记录获取产品信息。", tags: ["SUM(defect_qty)", "qms_defect_detail"] },
    resource: { title: "数据资源匹配", body: "缺陷明细提供不良数量，检验记录提供产品关联，产品主数据提供产品名称。", tags: ["qms_defect_detail", "qms_inspection", "dim_product"] },
    query: {
      title: "实际执行 SQL",
      body: "SQL 使用已登记关联路径（inspection_id、product_id），并限制返回行数。",
      code: "SELECT p.product_name,\n  SUM(d.defect_qty) AS defect_total\nFROM qms_defect_detail d\nJOIN qms_inspection i ON d.inspection_id = i.inspection_id\nJOIN dim_product p ON i.product_id = p.product_id\nWHERE i.inspection_date >= DATE '2026-06-16'\nGROUP BY p.product_name\nORDER BY defect_total DESC\nLIMIT 5;",
    },
    validate: { title: "校验结果", body: "语句只读；关联路径已登记；无笛卡尔积风险；包含行数限制。", tags: ["只读通过", "关联通过", "口径通过"] },
    execute: { title: "执行摘要", body: "查询执行完成，耗时 1.1 秒，扫描 2,115 条缺陷明细，返回 Top 5 产品。", tags: ["2,115 条明细", "返回 5 行"] },
    explain: { title: "结论生成依据", body: "排行与占比均来自查询结果；缺陷类型构成来自补充下钻查询。", tags: ["数值可追溯", "结论已校验"] },
  },
  result: {
    kpis: [
      { label: "不良总数", value: "1,127", caption: "最近 30 天 · 全部产品" },
      { label: "最高产品", value: "P017", caption: "智能控制模块 · 占 19.0%", tone: "red" },
      { label: "主要缺陷", value: "划伤", caption: "P017 不良中占 43%" },
    ],
  },
  chart: {
    type: "bar",
    kicker: "不良数量 · 最近 30 天",
    title: "不良数量最高的 5 个产品",
    data: [
      { name: "P017 控制模块", value: 214 },
      { name: "P003 驱动板", value: 189 },
      { name: "P008 电源模块", value: 152 },
      { name: "P011 传感器组件", value: 118 },
      { name: "P015 显示单元", value: 96 },
    ],
    xKey: "name",
    bars: [{ key: "value", name: "不良数量" }],
    unit: " 件",
  },
  conclusion: {
    text: "最近 30 天累计不良 1,127 件，P017 智能控制模块以 214 件居首，占全部不良的 19.0%。",
    highlights: [
      { title: "P017 划伤缺陷集中增加", text: "214 件不良中划伤占 43%，且集中出现在最近两周，建议核查包装与搬运环节。" },
      { title: "前三产品占比过半", text: "P017、P003、P008 合计 555 件，占全部不良的 49.2%。" },
      { title: "其余产品保持平稳", text: "Top 5 之外的产品不良数量均低于 90 件，未见异常波动。" },
    ],
    followUp: "查看 P017 划伤缺陷的时间分布和责任工序",
  },
  execution: { engine: "PostgreSQL", durationMs: 1100, rowCount: 5 },
  sources: [
    { icon: "knowledge", title: "不良数量 v2.0", caption: "质量部 · 已发布" },
    { icon: "database", title: "qms_defect_detail", caption: "数据更新至 2026-07-15" },
    { icon: "database", title: "dim_product", caption: "产品主数据" },
  ],
};

export const downtimeRun = {
  id: "run-downtime",
  question: "比较各产线的非计划停机时长",
  status: "succeeded",
  plan: [
    { id: "intent", label: "理解业务问题", caption: "非计划停机对比 · 按产线 · 7 月", tone: "blue", duration: "0.6s" },
    { id: "semantic", label: "匹配业务口径", caption: "非计划停机时长 · 待审核 v0.9", tone: "teal", duration: "0.4s" },
    { id: "resource", label: "定位数据资源", caption: "eqp_downtime_record · is_planned", tone: "violet", duration: "0.7s" },
    { id: "query", label: "生成分析代码", caption: "PostgreSQL · 只读查询", tone: "blue", duration: "0.9s" },
    { id: "validate", label: "安全与语义校验", caption: "7 项检查全部通过", tone: "green", duration: "0.3s" },
    { id: "execute", label: "执行并检查结果", caption: "返回 5 行 · 无异常", tone: "amber", duration: "1.0s" },
    { id: "explain", label: "生成图表与结论", caption: "柱状图 · 2 条业务发现", tone: "teal", duration: "0.7s" },
  ],
  planDetails: {
    intent: { title: "业务意图", body: "汇总 7 月各产线的非计划停机时长并对比，识别停机最严重的产线。", tags: ["指标：非计划停机时长", "维度：产线", "时间：2026-07-01 至今"] },
    semantic: { title: "指标口径 · 非计划停机时长 v0.9", body: "仅统计 is_planned = false 的停机记录，按产线累加停机分钟数。该口径当前为待审核状态。", tags: ["SUM(downtime_minutes)", "is_planned = false"] },
    resource: { title: "数据资源匹配", body: "设备停机记录表提供产线、停机时长与是否计划停机标记。", tags: ["eqp_downtime_record"] },
    query: {
      title: "实际执行 SQL",
      body: "SQL 基于已登记元数据生成，过滤计划内停机。",
      code: "SELECT r.line_id,\n  SUM(r.downtime_minutes) AS unplanned_minutes\nFROM eqp_downtime_record r\nWHERE r.is_planned = false\n  AND r.start_time >= DATE '2026-07-01'\nGROUP BY r.line_id\nORDER BY unplanned_minutes DESC\nLIMIT 100;",
    },
    validate: { title: "校验结果", body: "语句只读；表字段真实存在；口径为待审核版本，已在结论中提示。", tags: ["只读通过", "字段通过", "口径待审核提示"] },
    execute: { title: "执行摘要", body: "查询执行完成，耗时 1.0 秒，扫描 336 条停机记录，返回 5 条产线。", tags: ["336 条记录", "返回 5 行"] },
    explain: { title: "结论生成依据", body: "占比与排序来自查询结果；与产量趋势的关联提示基于既有分析记录。", tags: ["数值可追溯", "结论已校验"] },
  },
  result: {
    kpis: [
      { label: "非计划停机", value: "8,225 min", caption: "7 月累计 · 占全部 52.7%" },
      { label: "最高产线", value: "L03", caption: "2,647 min · 占 32.2%", tone: "red" },
      { label: "涉及设备", value: "9 台", caption: "E0009 贡献最大" },
    ],
  },
  chart: {
    type: "bar",
    kicker: "非计划停机 · 2026 年 7 月",
    title: "各产线非计划停机时长对比",
    data: [
      { name: "L03", value: 2647 },
      { name: "L01", value: 1830 },
      { name: "L05", value: 1451 },
      { name: "L02", value: 1215 },
      { name: "L04", value: 1082 },
    ],
    xKey: "name",
    bars: [{ key: "value", name: "非计划停机 (min)" }],
    unit: " min",
  },
  conclusion: {
    text: "7 月非计划停机累计 8,225 分钟，L03 产线以 2,647 分钟居首，占全部非计划停机的 32.2%。",
    highlights: [
      { title: "L03 停机与产量下滑吻合", text: "L03 的停机集中在 07-12 之后，与该产线同期产量连续下降的时间段一致，E0009 设备贡献了主要停机时长。" },
      { title: "口径提示", text: "「非计划停机时长」口径当前为待审核版本（v0.9），正式发布前数值可能调整。" },
    ],
    followUp: "分析 E0009 设备的停机原因构成",
  },
  execution: { engine: "PostgreSQL", durationMs: 1000, rowCount: 5 },
  sources: [
    { icon: "knowledge", title: "非计划停机时长 v0.9", caption: "设备部 · 待审核" },
    { icon: "database", title: "eqp_downtime_record", caption: "数据更新至 2026-07-15" },
  ],
};

export const scenarioRuns = [yieldRun, trendRun, defectRankRun, downtimeRun];
