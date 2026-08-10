export const sessions = [
  { id: "session-yield", title: "本月各工序良率", path: "/workspace/analysis", updatedAt: "2026-07-17" },
  { id: "session-downtime", title: "设备停机异常分析", path: "/workspace/analysis", updatedAt: "2026-07-16" },
  { id: "session-weekly", title: "第 28 周质量报告", path: "/workspace/reports", updatedAt: "2026-07-15" },
];

export const overviewAlerts = [
  { id: "alert-yield", level: "warning", code: "QUAL / P-06", title: "功能测试良率偏离标准", value: "95.83%", delta: "较工序标准低 0.67 个百分点", action: "分析缺陷构成" },
  { id: "alert-downtime", level: "danger", code: "EQPT / JUL", title: "非计划停机需要关注", value: "8,225 min", delta: "占全部停机时长的 52.7%", action: "定位停机分布" },
  { id: "alert-defect", level: "info", code: "QMS / 45D", title: "老化异常出现最频繁", value: "719", delta: "45 天缺陷明细统计", action: "查看产品分布" },
];

export const overviewStats = [
  { label: "业务数据表", value: "10" },
  { label: "有效记录", value: "8,019" },
  { label: "关系完整", value: "100%" },
];

export const recentTasks = [
  { id: "task-yield", title: "7 月各工序良率分析", detail: "2,752 条工序产量记录 · 8 道工序", type: "指标分析", owner: "王敏", duration: "4.8s", status: "已完成", tone: "green" },
  { id: "task-corr", title: "产线日停机与不良率关联分析", detail: "223 个产线日样本 · 相关系数 -0.001", type: "关联分析", owner: "李工", duration: "7.2s", status: "已完成", tone: "green" },
  { id: "task-report", title: "第 28 周质量分析报告", detail: "质量检验与缺陷明细联合分析", type: "报告生成", owner: "周雪", duration: "31s", status: "待复核", tone: "blue" },
];

export const recordTrend = [
  { date: "07-02", production: 168, quality: 84 },
  { date: "07-03", production: 175, quality: 88 },
  { date: "07-04", production: 158, quality: 79 },
  { date: "07-05", production: 150, quality: 75 },
  { date: "07-06", production: 182, quality: 91 },
  { date: "07-07", production: 190, quality: 95 },
  { date: "07-08", production: 187, quality: 93 },
  { date: "07-09", production: 172, quality: 86 },
  { date: "07-10", production: 161, quality: 80 },
  { date: "07-11", production: 196, quality: 98 },
  { date: "07-12", production: 204, quality: 102 },
  { date: "07-13", production: 198, quality: 99 },
  { date: "07-14", production: 214, quality: 107 },
  { date: "07-15", production: 226, quality: 112 },
];

export const businessTopics = [
  {
    name: "生产分析",
    code: "PROD",
    color: "blue",
    description: "工单、产线、工序和产量趋势",
    metrics: ["计划完成率", "日产量", "工序良率", "生产节拍"],
    coverage: 92,
  },
  {
    name: "质量分析",
    code: "QUAL",
    color: "teal",
    description: "检验、缺陷、不良品与质量趋势",
    metrics: ["良率", "不良率", "缺陷占比", "一次通过率"],
    coverage: 96,
  },
  {
    name: "设备分析",
    code: "EQPT",
    color: "amber",
    description: "停机、故障、报警与运行效率",
    metrics: ["停机时长", "设备可用率", "故障次数", "MTTR"],
    coverage: 84,
  },
];

export const metrics = [
  { id: "M-001", name: "工序良率", topic: "质量分析", formula: "良品数量 / 投入数量 × 100%", source: "mes_process_output", status: "已发布", owner: "质量部", updated: "今天 09:42", definition: "衡量指定工序在统计周期内产出合格品的能力，用于比较工序质量表现并发现薄弱环节。", synonyms: ["工序合格率", "制程良率", "工序通过率"], dimensions: ["工序", "产品", "产线", "班次"], timeField: "stat_date", precision: "百分比 · 2 位小数" },
  { id: "M-002", name: "抽检不良率", topic: "质量分析", formula: "不良数量 / 抽检数量 × 100%", source: "qms_inspection", status: "已发布", owner: "质量部", updated: "昨天 16:20", definition: "衡量抽检样本中不良品的占比，用于监控产品出厂质量水平。", synonyms: ["不良率", "抽检不合格率"], dimensions: ["产品", "工序", "日期"], timeField: "inspection_date", precision: "百分比 · 2 位小数" },
  { id: "M-003", name: "计划完成率", topic: "生产分析", formula: "实际产量 / 计划数量 × 100%", source: "mes_work_order + mes_process_output", status: "已发布", owner: "生产部", updated: "07-14 11:08", definition: "衡量工单实际产量对计划数量的达成程度，用于评估排产执行情况。", synonyms: ["达成率", "完工率"], dimensions: ["工单", "产线", "日期"], timeField: "stat_date", precision: "百分比 · 1 位小数" },
  { id: "M-004", name: "设备停机时长", topic: "设备分析", formula: "SUM(停机分钟数)", source: "eqp_downtime_record", status: "已发布", owner: "设备部", updated: "07-13 15:31", definition: "统计设备在周期内的停机总分钟数，可区分计划内与非计划停机。", synonyms: ["停机时间"], dimensions: ["设备", "产线", "停机原因"], timeField: "start_time", precision: "分钟 · 整数" },
  { id: "M-005", name: "非计划停机占比", topic: "设备分析", formula: "非计划停机分钟数 / 全部停机分钟数 × 100%", source: "eqp_downtime_record", status: "待审核", owner: "设备部", updated: "07-12 10:15", definition: "非计划停机时长占全部停机时长的比例，反映设备突发故障水平。", synonyms: ["故障停机占比"], dimensions: ["设备", "产线"], timeField: "start_time", precision: "百分比 · 1 位小数" },
  { id: "M-006", name: "检验通过率", topic: "质量分析", formula: "通过检验记录数 / 检验记录总数 × 100%", source: "qms_inspection", status: "已发布", owner: "质量部", updated: "07-10 14:07", definition: "通过检验的记录数占全部检验记录的比例，按检验批次口径统计。", synonyms: ["批次合格率"], dimensions: ["产品", "工序", "日期"], timeField: "inspection_date", precision: "百分比 · 2 位小数" },
];

export const dataTables = [
  { name: "mes_work_order", cn: "生产工单表", topic: "生产", rows: "344", fields: 8, freshness: "2026-07-15", quality: 100, key: "work_order_id" },
  { name: "mes_process_output", cn: "工序产量表", topic: "生产", rows: "2,752", fields: 11, freshness: "2026-07-15", quality: 100, key: "output_id" },
  { name: "qms_inspection", cn: "质量检验表", topic: "质量", rows: "1,376", fields: 9, freshness: "2026-07-15", quality: 100, key: "inspection_id" },
  { name: "qms_defect_detail", cn: "缺陷明细表", topic: "质量", rows: "2,115", fields: 7, freshness: "2026-07-15", quality: 100, key: "defect_id" },
  { name: "eqp_downtime_record", cn: "设备停机记录表", topic: "设备", rows: "336", fields: 8, freshness: "2026-07-15", quality: 100, key: "downtime_id" },
  { name: "inv_inventory_snapshot", cn: "库存快照表", topic: "库存", rows: "1,004", fields: 7, freshness: "2026-07-15", quality: 100, key: "snapshot_id" },
];

export const schemaFields = [
  { name: "output_id", cn: "工序产出记录编号", type: "varchar(20)", rule: "主键 · 非空", sample: "OUT00000001" },
  { name: "work_order_id", cn: "生产工单编号", type: "varchar(20)", rule: "外键 → mes_work_order", sample: "WO000001" },
  { name: "product_id", cn: "产品编号", type: "varchar(20)", rule: "外键 → dim_product", sample: "P003" },
  { name: "process_id", cn: "工序编号", type: "varchar(20)", rule: "外键 → dim_process", sample: "PR01" },
  { name: "line_id", cn: "产线编号", type: "varchar(20)", rule: "外键 → dim_production_line", sample: "L05" },
  { name: "stat_date", cn: "统计日期", type: "date", rule: "默认时间字段", sample: "2026-06-01" },
  { name: "input_qty", cn: "投入数量", type: "integer", rule: "≥ 0", sample: "1186" },
  { name: "good_qty", cn: "良品数量", type: "integer", rule: "≥ 0", sample: "1165" },
  { name: "defect_qty", cn: "不良数量", type: "integer", rule: "≥ 0", sample: "24" },
  { name: "rework_qty", cn: "返工数量", type: "integer", rule: "≥ 0", sample: "6" },
  { name: "shift_code", cn: "班次编码", type: "varchar(8)", rule: "D / N", sample: "D" },
];

export const traceSteps = [
  { id: "intent", label: "理解业务问题", caption: "识别指标、维度与时间", tone: "blue", duration: "0.7s" },
  { id: "semantic", label: "匹配业务口径", caption: "工序良率 · 已发布 v2.3", tone: "teal", duration: "0.4s" },
  { id: "resource", label: "定位数据资源", caption: "2 张表 · process_id 关联", tone: "violet", duration: "0.8s" },
  { id: "query", label: "生成分析代码", caption: "PostgreSQL · 只读查询", tone: "blue", duration: "1.1s" },
  { id: "validate", label: "安全与语义校验", caption: "7 项检查全部通过", tone: "green", duration: "0.3s" },
  { id: "execute", label: "执行并检查结果", caption: "返回 8 行 · 无异常", tone: "amber", duration: "1.4s" },
  { id: "explain", label: "生成图表与结论", caption: "柱状图 · 3 条业务发现", tone: "teal", duration: "0.9s" },
];

export const traceDetails = {
  intent: { title: "业务意图", body: "分析 7 月不同工序的良率表现，并与每道工序各自的标准良率进行比较。", tags: ["指标：工序良率", "维度：工序", "时间：2026-07-01 至 2026-07-15", "比较基准：工序标准"] },
  semantic: { title: "指标口径 · 工序良率 v2.3", body: "采用质量部门已发布口径，按投入数量加权计算，并读取工序主数据中的标准良率。", tags: ["SUM(good_qty)", "SUM(input_qty)", "standard_yield_rate"] },
  resource: { title: "数据资源匹配", body: "工序产量表提供投入量和良品量，工序主数据提供工序名称与标准良率，通过 process_id 关联。", tags: ["mes_process_output", "dim_process", "process_id"] },
  query: { title: "实际执行 SQL", body: "SQL 已基于已登记元数据生成，包含时间范围、聚合口径和返回行数限制。", code: "SELECT p.process_name,\n  ROUND(SUM(o.good_qty) * 100.0 /\n  NULLIF(SUM(o.input_qty), 0), 2) AS yield_rate,\n  p.standard_yield_rate\nFROM mes_process_output o\nJOIN dim_process p ON o.process_id = p.process_id\nWHERE o.stat_date BETWEEN DATE '2026-07-01' AND DATE '2026-07-15'\nGROUP BY p.process_name, p.standard_yield_rate\nORDER BY p.process_name\nLIMIT 100;" },
  validate: { title: "校验结果", body: "语句只读；表字段真实存在；关联路径已登记；指标公式与发布口径一致；包含时间范围和行数限制。", tags: ["只读通过", "字段通过", "关联通过", "口径通过", "资源限制通过"] },
  execute: { title: "执行摘要", body: "查询在华东制造数据底座执行完成，耗时 1.4 秒，扫描 2,752 条工序产量记录，返回 8 道工序。", tags: ["2,752 条记录", "返回 8 行", "数据更新至 2026-07-15"] },
  explain: { title: "结论生成依据", body: "结论中的工序名称、良率数值和目标差距均来自实际查询结果；未对相关性作因果解释。", tags: ["数值可追溯", "无外部知识补写", "结论已校验"] },
};

export const processYield = [
  { name: "SMT贴片", value: 98.32, target: 98.5 },
  { name: "回流焊", value: 97.9, target: 98.2 },
  { name: "AOI检测", value: 99.23, target: 99.0 },
  { name: "插件装配", value: 97.11, target: 97.5 },
  { name: "波峰焊", value: 96.53, target: 97.0 },
  { name: "功能测试", value: 95.83, target: 96.5 },
  { name: "老化测试", value: 96.2, target: 96.8 },
  { name: "包装入库", value: 99.19, target: 99.2 },
];

export const modelCatalog = [
  { id: "linear", name: "线性回归", type: "数值预测", scene: "预测产量、停机时长", accent: "blue" },
  { id: "tree", name: "决策树", type: "分类 / 回归", scene: "识别质量风险规则", accent: "teal" },
  { id: "forest", name: "随机森林", type: "分类 / 回归", scene: "预测产品不良风险", accent: "violet" },
  { id: "logistic", name: "逻辑回归", type: "分类", scene: "判断是否发生不良", accent: "blue" },
  { id: "kmeans", name: "KMeans", type: "聚类", scene: "划分设备运行模式", accent: "amber" },
  { id: "isolation", name: "Isolation Forest", type: "异常检测", scene: "发现异常设备与批次", accent: "red" },
];

export const modelingTasks = [
  {
    id: "M-20260716-021",
    title: "质量风险识别",
    source: "agent",
    sourceLabel: "Agent 创建",
    owner: "王敏",
    taskType: "classification",
    taskTypeLabel: "二分类",
    status: "done",
    statusLabel: "训练完成",
    tone: "green",
    updatedAt: "今天 10:21",
    goal: "根据工序、产线、班次、产品类别和停机信息，识别不良率超过 4% 的高风险生产记录。",
    dataset: "生产质量风险视图",
    sampleCount: 2752,
    sampleSummary: "高风险样本 851 个（30.9%）",
    targetLabel: "不良率是否 ≥ 4%",
    targetField: "high_defect_risk",
    features: ["工序", "产线", "班次", "产品类别", "停机时长", "计划数量"],
    candidates: ["logistic", "tree", "forest"],
    selectedModel: "forest",
    selectedModelName: "随机森林",
    metric: "AUC 0.986",
  },
  {
    id: "M-20260716-018",
    title: "设备运行异常检测",
    source: "manual",
    sourceLabel: "人工创建",
    owner: "李工",
    taskType: "anomaly",
    taskTypeLabel: "异常检测",
    status: "training",
    statusLabel: "训练中",
    tone: "blue",
    updatedAt: "今天 10:14",
    goal: "结合停机时长、故障类型和设备运行记录，识别偏离常态的设备与时段。",
    dataset: "设备运行分析视图",
    sampleCount: 336,
    sampleSummary: "已完成缺失值检查",
    targetLabel: "无监督异常评分",
    targetField: "anomaly_score",
    features: ["设备", "产线", "停机时长", "故障类型", "班次"],
    candidates: ["isolation"],
    selectedModel: "isolation",
    selectedModelName: "Isolation Forest",
    metric: "异常率 6.8%",
  },
  {
    id: "M-20260715-011",
    title: "日产量预测",
    source: "agent",
    sourceLabel: "Agent 创建",
    owner: "周雪",
    taskType: "regression",
    taskTypeLabel: "回归预测",
    status: "review",
    statusLabel: "待确认",
    tone: "amber",
    updatedAt: "昨天 16:42",
    goal: "根据工单、产线和历史产出预测未来 7 天日产量。",
    dataset: "生产计划与产出视图",
    sampleCount: 2408,
    sampleSummary: "时间跨度 90 天",
    targetLabel: "下一日实际产量",
    targetField: "next_day_output_qty",
    features: ["产线", "产品", "计划数量", "历史产量", "星期"],
    candidates: ["linear", "tree", "forest"],
    selectedModel: null,
    selectedModelName: "待确认",
    metric: "尚未训练",
  },
];

export const governedModels = [
  { id: "MDL-021", name: "质量风险识别", version: "v1.3", taskId: "M-20260716-021", owner: "王敏 · 生产部", source: "Agent 创建", algorithm: "随机森林", dataset: "生产质量风险视图", metric: "AUC 0.986", status: "已发布", tone: "green", calls: 128, updatedAt: "今天 10:21" },
  { id: "MDL-018", name: "设备运行异常检测", version: "v0.8", taskId: "M-20260716-018", owner: "李工 · 设备部", source: "人工创建", algorithm: "Isolation Forest", dataset: "设备运行分析视图", metric: "异常率 6.8%", status: "待复核", tone: "amber", calls: 0, updatedAt: "今天 10:14" },
  { id: "MDL-014", name: "产线日产量预测", version: "v2.1", taskId: "M-20260710-014", owner: "周雪 · 生产部", source: "Agent 创建", algorithm: "随机森林", dataset: "生产计划与产出视图", metric: "R² 0.912", status: "已发布", tone: "green", calls: 342, updatedAt: "07-15 16:42" },
  { id: "MDL-009", name: "设备运行模式聚类", version: "v1.0", taskId: "M-20260702-009", owner: "林涛 · 数据治理组", source: "人工创建", algorithm: "KMeans", dataset: "设备运行分析视图", metric: "轮廓系数 0.61", status: "已下线", tone: "red", calls: 57, updatedAt: "07-12 09:30" },
];

export const weeklyQualitySections = [
  { id: "s1", title: "质量概览", summary: "本周完成检验 316 批次，总体良率 97.56%，环比上周下降 0.31 个百分点，仍处于受控范围。", evidence: { source: "qms_inspection", range: "2026-07-06 至 2026-07-12", detail: "316 条检验记录" } },
  { id: "s2", title: "良率趋势", summary: "周内良率前稳后降，07-10 起功能测试工序良率跌破标准值，是本周主要拖累项。", evidence: { source: "mes_process_output + dim_process", range: "2026-07-06 至 2026-07-12", detail: "按日聚合 · 7 个数据点" } },
  { id: "s3", title: "缺陷分布", summary: "划伤与老化异常合计占本周全部缺陷的 58%，其中划伤集中出现在 P017 智能控制模块。", evidence: { source: "qms_defect_detail", range: "2026-07-06 至 2026-07-12", detail: "486 条缺陷明细" } },
  { id: "s4", title: "重点产品", summary: "P017 本周不良 76 件居首，建议对其包装与搬运环节发起专项核查。", evidence: { source: "qms_defect_detail + dim_product", range: "2026-07-06 至 2026-07-12", detail: "Top 5 排行" } },
  { id: "s5", title: "异常与建议", summary: "L03 产线停机时长与良率下滑在时间上高度吻合，建议优先排查 E0009 设备并复核排产计划。", evidence: { source: "eqp_downtime_record + mes_process_output", range: "2026-07-06 至 2026-07-12", detail: "关联分析 · 相关系数 -0.62" } },
];

export const reports = [
  { id: "R-001", title: "第 28 周质量分析报告", topic: "质量", date: "今天 10:18", author: "Agent 自动生成", status: "已复核", pages: 8, accent: "teal", sections: weeklyQualitySections },
  { id: "R-002", title: "6 月生产运营月报", topic: "生产", date: "07-01 09:30", author: "王敏 · 生产部", status: "已发布", pages: 14, accent: "blue" },
  { id: "R-003", title: "设备停机专题分析", topic: "设备", date: "06-28 16:42", author: "Agent + 李工", status: "待复核", pages: 6, accent: "amber" },
  { id: "R-004", title: "A17 产品不良原因追踪", topic: "质量", date: "06-25 11:07", author: "周雪 · 质量部", status: "已发布", pages: 9, accent: "violet" },
];

export const auditRows = [
  { time: "10:32:18", user: "王敏", role: "业务人员", action: "查询工序良率", resource: "mes_process_output", result: "允许", tone: "green" },
  { time: "10:29:41", user: "李工", role: "业务人员", action: "训练异常检测模型", resource: "eqp_downtime_record", result: "允许", tone: "green" },
  { time: "10:21:07", user: "测试账号", role: "业务人员", action: "DELETE FROM inventory", resource: "inventory", result: "已拦截", tone: "red" },
  { time: "09:58:33", user: "管理员", role: "管理员", action: "更新指标口径", resource: "M-001 工序良率", result: "已记录", tone: "blue" },
  { time: "09:43:10", user: "周雪", role: "业务人员", action: "生成质量周报", resource: "3 个业务主题", result: "允许", tone: "green" },
];

export const businessObjects = [
  { id: "Product", name: "产品", topic: "全局", relationCount: 8, description: "制造活动的最终或中间产出" },
  { id: "WorkOrder", name: "生产工单", topic: "生产分析", relationCount: 12, description: "规定产品、数量和计划时间的生产任务" },
  { id: "Process", name: "工序", topic: "生产分析", relationCount: 9, description: "生产过程中可独立度量的作业环节" },
  { id: "ProductionLine", name: "产线", topic: "生产分析", relationCount: 7, description: "承载连续生产活动的设备与工位集合" },
  { id: "Equipment", name: "设备", topic: "设备分析", relationCount: 11, description: "参与生产并产生运行数据的资产" },
  { id: "Inspection", name: "质量检验", topic: "质量分析", relationCount: 10, description: "依据标准对产品质量进行判定的活动" },
];

export const businessRules = [
  { id: "R-012", name: "投入数量为 0 时不计算工序良率", type: "指标规则", scope: "质量分析" },
  { id: "R-018", name: "停机结束时间不得早于开始时间", type: "数据校验", scope: "设备分析" },
  { id: "R-023", name: "工单完工量不得超过计划量 120%", type: "异常规则", scope: "生产分析" },
  { id: "R-027", name: "跨表统计必须使用已登记关联路径", type: "查询规则", scope: "全局" },
];

export const adminHealth = [
  { icon: "knowledge", label: "业务语义", value: "93%", note: "77 个知识项 · 3 个待审核", tone: "blue", page: "knowledge", trend: [88, 90, 89, 91, 92, 92, 93], delta: "近 7 天 +5" },
  { icon: "database", label: "数据资源", value: "100", note: "10 张表 · 15 条关系完整", tone: "teal", page: "resources", trend: [96, 97, 98, 98, 99, 100, 100], delta: "近 7 天 +4" },
  { icon: "model", label: "模型治理", value: "4", note: "2 个已发布 · 1 个待复核", tone: "violet", page: "model-governance", trend: [2, 2, 3, 3, 3, 4, 4], delta: "近 7 天 +2" },
  { icon: "shield", label: "安全运行", value: "100%", note: "只读策略与审计均已启用", tone: "green", page: "security", trend: [100, 100, 99, 100, 100, 100, 100], delta: "拦截 1 次已复核" },
];

export const governanceQueue = [
  { index: "01", title: "审核“设备可用率”指标口径", note: "设备部提交 · 公式和默认时间字段已自动检查", status: "待审核", tone: "amber", page: "knowledge" },
  { index: "02", title: "确认设备运行字段映射", note: "2 个新字段尚未关联业务对象", status: "待映射", tone: "blue", page: "resources" },
  { index: "03", title: "复核危险 SQL 拦截记录", note: "请求包含 DELETE，已在执行前阻断", status: "已拦截", tone: "red", page: "security" },
];

export const pendingTableSuggestion = {
  name: "eqp_sensor_reading",
  cn: "设备传感器读数表",
  topic: "设备",
  confidence: 0.87,
  reason: "字段包含 equipment_id、reading_time 与多个数值型读数，与设备主题高度相关；reading_id 唯一非空，疑似主键。",
  fieldSuggestions: [
    { name: "reading_id", cn: "读数记录编号", note: "疑似主键 · 唯一非空" },
    { name: "equipment_id", cn: "设备编号", note: "疑似外键 → dim_equipment" },
    { name: "reading_time", cn: "采集时间", note: "时间字段 · 分钟级" },
    { name: "temperature", cn: "运行温度", note: "数值 · ℃" },
    { name: "vibration", cn: "振动幅值", note: "数值 · mm/s" },
    { name: "power_kw", cn: "实时功率", note: "数值 · kW" },
  ],
};

export const tablePreviews = {
  mes_work_order: { columns: ["work_order_id", "work_order_no", "product_id", "line_id", "plan_qty", "start_date", "end_date", "order_status"], rows: [["WO000001", "MO-20260601-0001", "P003", "L05", "1200", "2026-06-01", "2026-06-03", "completed"], ["WO000002", "MO-20260601-0002", "P015", "L01", "2000", "2026-06-01", "2026-06-03", "closed"], ["WO000003", "MO-20260601-0003", "P017", "L01", "1500", "2026-06-01", "2026-06-01", "completed"]] },
  mes_process_output: { columns: ["output_id", "work_order_id", "product_id", "process_id", "line_id", "stat_date", "input_qty", "good_qty", "defect_qty", "rework_qty", "shift_code"], rows: [["OUT00000001", "WO000001", "P003", "PR01", "L05", "2026-06-01", "1186", "1165", "24", "6", "D"], ["OUT00000002", "WO000001", "P003", "PR02", "L05", "2026-06-01", "1165", "1145", "25", "10", "N"], ["OUT00000003", "WO000001", "P003", "PR03", "L05", "2026-06-01", "1136", "1132", "5", "2", "D"]] },
  qms_inspection: { columns: ["inspection_id", "inspection_no", "work_order_id", "product_id", "process_id", "inspection_date", "sample_qty", "defect_qty", "inspection_result"], rows: [["INS00000001", "QI-20260601-00001", "WO000001", "P003", "PR03", "2026-06-01", "150", "1", "pass"], ["INS00000002", "QI-20260602-00002", "WO000001", "P003", "PR06", "2026-06-02", "120", "7", "fail"], ["INS00000003", "QI-20260603-00003", "WO000001", "P003", "PR07", "2026-06-03", "80", "4", "fail"]] },
  qms_defect_detail: { columns: ["defect_id", "inspection_id", "defect_type", "defect_code", "defect_qty", "severity_level", "responsible_process_id"], rows: [["DEF00000001", "INS00000001", "老化异常", "D07", "1", "major", "PR03"], ["DEF00000002", "INS00000002", "焊点虚焊", "D01", "1", "major", "PR06"], ["DEF00000003", "INS00000002", "偏位", "D03", "6", "major", "PR06"]] },
  eqp_downtime_record: { columns: ["downtime_id", "equipment_id", "line_id", "start_time", "end_time", "downtime_minutes", "downtime_reason", "is_planned"], rows: [["DT00000001", "E0004", "L01", "2026-06-01 19:20", "2026-06-01 20:50", "90", "程序调试", "false"], ["DT00000002", "E0004", "L01", "2026-06-01 07:50", "2026-06-01 08:50", "60", "程序调试", "false"], ["DT00000003", "E0009", "L03", "2026-06-01 07:30", "2026-06-01 08:15", "45", "轴承过热", "false"]] },
  inv_inventory_snapshot: { columns: ["snapshot_id", "snapshot_date", "product_id", "warehouse_code", "available_qty", "frozen_qty", "safety_stock_qty"], rows: [["INV00000001", "2026-06-01", "P001", "WH-QA", "1826", "120", "1200"], ["INV00000002", "2026-06-01", "P002", "WH-A", "2109", "0", "800"], ["INV00000003", "2026-06-01", "P003", "WH-QA", "2333", "80", "1000"]] },
  eqp_sensor_reading: { columns: ["reading_id", "equipment_id", "reading_time", "temperature", "vibration", "power_kw"], rows: [["SR00000001", "E0004", "2026-07-17 09:00", "63.2", "2.8", "11.6"], ["SR00000002", "E0004", "2026-07-17 09:01", "63.5", "2.9", "11.7"], ["SR00000003", "E0009", "2026-07-17 09:00", "71.8", "4.6", "13.2"]] },
};

export const fieldLabels = { output_id: "工序产出记录", work_order_id: "生产工单", work_order_no: "生产工单号", product_id: "产品", process_id: "工序", line_id: "产线", stat_date: "统计日期", input_qty: "投入数量", good_qty: "良品数量", defect_qty: "不良数量", rework_qty: "返工数量", shift_code: "班次", inspection_id: "检验记录", inspection_no: "检验单号", inspection_date: "检验日期", sample_qty: "抽检数量", inspection_result: "检验结论", defect_id: "缺陷明细", defect_type: "缺陷类型", defect_code: "缺陷编码", severity_level: "严重程度", responsible_process_id: "责任工序", downtime_id: "停机记录", equipment_id: "设备", start_time: "停机开始时间", end_time: "停机结束时间", downtime_minutes: "停机分钟数", downtime_reason: "停机原因", is_planned: "是否计划停机", snapshot_id: "库存快照", snapshot_date: "快照日期", warehouse_code: "仓库编码", available_qty: "可用库存", frozen_qty: "冻结库存", safety_stock_qty: "安全库存", plan_qty: "计划数量", start_date: "开始日期", end_date: "结束日期", order_status: "工单状态", reading_id: "读数记录编号", reading_time: "采集时间", temperature: "运行温度", vibration: "振动幅值", power_kw: "实时功率" };
