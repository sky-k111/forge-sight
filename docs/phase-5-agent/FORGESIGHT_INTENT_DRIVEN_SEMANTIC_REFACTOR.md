---
title: ForgeSight 语义重构方案：Intent-Driven
date: 2026-09-09
status: 设计稿
---

# ForgeSight Agent 语义重构：Intent-Driven（语义归 Agent，安全归确定性）

> 一句话：把「用户想做什么」从一堆正则手里收回来，交给一次**结构化 LLM 意图解析**；
> 确定性代码只留下**校验、白名单、只读、预算、口径、审计**这些它本来就该干的事。
> 这样既根治「每来一种新说法就堆一条规则」的死代码问题，也让报告/监控/推送/建模等模块**以注册能力的方式低耦合接入**。

---

## 0. 结论（TL;DR）

- 现状：用户语义被 **3 层互不信任的启发式**重复解释——`intent.py`（时间/维度/图表/分析类型正则）、`planner.py`（建模/报告/规划触发正则）、`graph.py` 与 `query_compiler.py`（建模类型/周期对比/检索问题/轴粒度/维度的再推断）。它们是「活得死代码」：会执行，但语义脆弱，是「5月/6月」「工序」两个 bug 的根因。
- 目标：**一个 Agent 语义源**（`AgentIntent`），由 LLM 产出、Pydantic 强校验；**确定性层只做两件事**——（1）用元数据/Schema 校验并放行，（2）用固定受治理子图安全执行。任何「模型猜不出」的语义空缺，一律走**澄清**，而不是再加一条正则。
- 与其它模块的衔接：**新模块 = 注册一个 `CapabilityDescriptor`**（已在 `capability_registry.py` 定义）。`delegated=True` 的能力走自己的受治理子图；`effectLevel=EXTERNAL` 的能力（飞书/钉钉/邮件推送）强制走 `approvalPolicy` 确认。规划器只看到 `allowedCapabilities`，无需为新模块改任何路由正则。
- 迁移是**渐进**的：先并行（shadow）产出 `AgentIntent` 落库对比，再逐层把正则降级为兜底/校验，最后替换 `graph.py`/`query_compiler` 的语义再推断。全程保持现有 493 个后端测试与前端 105 个测试为绿。

---

## 1. 问题：语义的多源决策与规则堆砌

### 1.1 语义被拆成 3 层互不信任的启发式

下面这些函数**都不该**在「理解用户」：真正的理解应该由 Agent 一次性给出。

| 层 | 文件:函数 | 它现在负责的语义判断 | 造成的脆弱点 |
|---|---|---|---|
| 意图 | `intent.py:_time_constraint` (513) | 解析本月/上月/最近N天/裸「6月」 | **5月/6月 bug 唯一根因**：正则只命中第一个「5月」，返回 `specific_month=5`，`build_intent_plan` 与确定性 `build_fallback_plan` 照抄，两段 SQL 都是 `2026-05-01~06-01` |
| 意图 | `intent.py:_group_dimensions` (600) | 解析「按/各/每道 工序」 | **工序未入维度**：原话「差异最大的两个工序再下钻」无此前缀，`groupBy` 只剩 `['产线']` |
| 意图 | `intent.py:_time_breakdown_dimensions` (595) | 解析「按月/按周/逐月」 | 表达一变（「各月份」vs「按月展示」）就漏 |
| 意图 | `intent.py:_requested_chart` (~470) | 识别柱/线/饼/热力/组合 | 需要为「柱状图以及趋势折线图」等组合单独写规则 |
| 意图 | `intent.py:classify_request_kind` (163)、`is_deterministic_conversation` (133)、`_is_general_knowledge`、`_is_known_conversation`、`_is_pure_system_utility`、`_is_report_request`、`_is_turn_switch`、`_multi_chart_views`、`_clarification_fields` | 判断闲聊/报告/范围外/是否追问/是否澄清 | 一个长 if 链；「换一种图」「它/其」「最近」都靠词表，漏一个说法就误路由 |
| 规划 | `planner.py:_looks_like_modeling` (16)、`should_use_planner` (46)、`PLANNING_TERMS`、`REPORT_TERMS`、`_GROUPING_CLASSIFY_RE` | 判断是否建模/报告/规划 | 用「分类/风险」等词区分建模与分组，需不停补负向规则 |
| 图/执行 | `graph.py:_detect_modeling_task_type` (263)、`_target_match_score` (293)、`_inferred_query` (628)、`_requires_period_comparison` (661)、`_build_retrieval_question` (808)、`_requires_history_context` (842)、`_time_field_candidates` (853) | 在图内再来一次语义推断 | 与 intent 结论不一致时，下游会「重新发明」语义 |
| 编译 | `query_compiler.py:_pick_metric`、`_resolve_dimension`、`_dim_time_grain`、`_time_group`、`_is_time_dim` 以及 rank/trend/share 分支 | 靠 keyword 推断维度/粒度/排序/趋势 | 一旦输入语义不完整，就会基于关键词猜测轴与粒度 |

### 1.2 单源事实源被破坏

下游有一个明确的「轴语义唯一事实源」目标（`query_compiler` 编译时已解析粒度，`interpretation.py` 注释也写了这一点），但**这个事实源并没有独一**：

- 决定维度的可能来自 `intent.py`（正则）、`planner.py`（正则）、`graph.py`（再推断）、`query_compiler`（keyword）。
- 决定时间粒度的可能来自 `intent.py:_time_constraint`、`query_compiler:_dim_time_grain`、`graph.py:_time_field_candidates`。
- 决定「这个维度取哪个字段」可能是 `_resolve_dimension`（元数据映射，OK）或 `_DIM_KEYWORDS`（语义词表，需要与意图一致）。

这就是为什么修一个案例往往连带引出另一个：每一层都在用自己的词表「重新理解」用户。

---

## 2. 设计原则

### P1 语义单源归 Agent
用户问「什么指标、什么维度、什么时间、什么图、要不要比较、是不是建模/报告/闲聊」——**只由一次 LLM 意图解析决定**，产出唯一的 `AgentIntent`。任何其它层不得用关键词再推断语义。

### P2 确定性只做校验/安全/执行
确定性代码（LangGraph 骨架、`capability_executor`、`query_compiler` 的 SQL 渲染、`_safe_identifier`、budget、scope）负责：**读只读、白名单能力、预算成本、方言安全、字段/标识符安全、口径披露、可审计、渲染不可绕过的 SQL**。这些是红线，由代码硬保证，绝不因 LLM 输出而改变。

### P3 未知即澄清，不堆规则
凡语义空缺（维度不在该数据源、时间粒度无登记时间字段、指标无物理公式、对象指代不清），**确定性层直接触发澄清**（复用现有 `clarification` 交互/`interrupt()`），而不是「再加一个词表条目」。杜绝 rule-pile。

### P4 轴/粒度/维度的唯一事实源
`query_compiler` 只**消费** `AgentIntent.time`、`AgentIntent.dimensions`（含已解析的 dim code/grain）来做 SQL 渲染；它不应再依据自由文本关键词判断「这个是不是趋势/要不要按周」。维度→物理列的映射用元数据（`fact.fields` 的 `semanticName`/`name`），是对的，保留。

### P5 能力即工具，新模块=注册
新增功能（周报、监控简报、飞书/钉钉推送、建模、调查、知识库）一律登记为 `CapabilityDescriptor`。规划器（LLM）从 `allowedCapabilities` 选能力；`delegated` 的走受治理子图；`EXTERNAL` 的走确认步骤。**不为新模块改任何路由正则或其他模块代码。**

---

## 3. 目标架构（数据流）

```
用户输入 + 多轮上下文
        │
        ▼
┌──────────────────────────────────────────────────────────────┐
│ ① 窄化路由门（确定性，只处理「固定响应」的路径）                │
│    ▪ 纯 utility（系统信息/时间等，响应是固定的） → 直接回        │
│    ▪ 纯 canned conversation（你好/谢谢/范围外/常识） → 直接回   │
│    ▪ 其它 → 进入②                                               │
│  ⚠️ 路由门必须「克制」：只要输入含任一数据实体词/指代/时间词，      │
│     一律交给②，不得由路由门下结论。                              │
└───────────────────────────────┬──────────────────────────────┘
                                ▼
┌──────────────────────────────────────────────────────────────┐
│ ② 意图解析（LLM，唯一语义源） → AgentIntent                    │
│    kind / metrics / dimensions / time / chart / comparison     │
│    / modeling / report / clarify / confidence / reasons        │
│    ▪ 结构化输出 + schema 强校验                                 │
│    ▪ 无法产出合法 AgentIntent → 走澄清或低置信兜底               │
└───────────────────────────────┬──────────────────────────────┘
                                ▼
┌──────────────────────────────────────────────────────────────┐
│ ③ 确定性校验层 validators.py（非语义）                         │
│    ▪ 能力白名单 / role / scope / budget / effectLevel          │
│    ▪ 维度/指标/时间字段必须在当前数据源元数据中存在              │
│    ▪ 图类型 / 粒度 枚举合法                                     │
│    ▪ 空缺 → 澄清；非法 → 拒绝                                   │
└───────────────────────────────┬──────────────────────────────┘
                                ▼
┌──────────────────────────────────────────────────────────────┐
│ ④ 规划器（LLM，选择能力 DAG；简单路径可确定性直达）             │
│    AgentPlan.steps[i] = {capability, arguments, dependsOn}     │
│    arguments 内嵌该步所需子 AgentIntent                         │
└───────────────────────────────┬──────────────────────────────┘
                                ▼
┌──────────────────────────────────────────────────────────────┐
│ ⑤ Capability 执行/受治理子图                                  │
│    ▪ delegated=True → 固定 LangGraph 子图（只读 SQL、口径、证据）│
│    ▪ PURE 能力 → capability_executor 直接跑（质量/可视化）       │
│    ▪ EXTERNAL 能力 → approval 确认 + 受控推送                    │
└───────────────────────────────┬──────────────────────────────┘
                                ▼
   结果 / 图表 / 结论 / 证据链 / 可审计事件流
```

## 4. 语义契约：`AgentIntent`

新增 `app/agent/intent_contract.py`，全部用 Pydantic，`extra=forbid`。这是全系统**唯一**的语义契约，`intent.py`、`planner.py`、`graph.py`、`query_compiler.py`、`interpretation.py` 都只消费它。

```python
Grain = Literal["day","week","month","quarter","year"]

class IntentKind(str, Enum):
    ANALYSIS = "analysis"          # 数据查询/分析
    MODELING = "modeling"          # 建模/预测/训练
    REPORT = "report"              # 生成报告（周报/月报/专题）
    INVESTIGATION = "investigation"# 下钻调查/根因
    UTILITY = "utility"            # 系统信息/时间
    CONVERSATION = "conversation"  # 闲聊/范围外（一般无数据）
    CLARIFY = "clarify"            # 需要向用户确认

class TimeMode(str, Enum):
    ABSOLUTE="absolute"; RELATIVE="relative"; CURRENT="current"
    RECENT="recent"; COMPARISON="comparison"; NONE="none"

class TimeRange(BaseModel):
    mode: TimeMode
    grain: Grain | None = None          # day/week/month/quarter/year
    start: date | datetime | None = None
    end: date | datetime | None = None
    amount: int | None = None
    unit: str | None = None             # 天/周/月/季度/年
    reference: Literal["now","data_horizon","prev_period"] = "data_horizon"
    comparisons: list["TimeRange"] = [] # 对比周期，如 [5月, 6月]
    label: str = ""                     # 人类可读 label，供结论/证据披露

class Dimension(BaseModel):
    semantic: str                        # 产线/产品/工序/车间/班次/缺陷类型/状态/时间
    code: str | None = None              # 元数据解析后的物理字段，由校验层回填
    grain: Grain | None = None           # 时间维度才需要
    label: str = ""

class Metric(BaseModel):
    semantic: str                        # 产量/良率/不良数/缺陷率/产能利用率…
    id: str | None = None                # 元数据解析后回填
    aggregation: Literal["sum","avg","count","max","min","rate","share","rank"] = "sum"
    alias: str = ""

class Chart(BaseModel):
    type: Literal["auto","bar","line","donut","scatter","heatmap","table","combo"]
    x: str | None = None                 # 横轴维度名（semantic）
    y: str | None = None                 # 主指标 alias
    series: str | None = None            # 分组/序列
    orient: Literal["auto","vertical","horizontal"] = "auto"
    secondary: str | None = None         # combo 次轴

class Comparison(BaseModel):
    basis: str = ""                      # 对比基准描述
    periods: list[TimeRange] = []
    top_diff_k: int | None = None        # 例如「差异最大的两个工序」
    by: Dimension | None = None          # 对比后下钻维度

class ModelingSpec(BaseModel):
    task: str                            # 回归/二分类/多分类/聚类/异常检测
    target: str | None = None
    features: list[str] = []
    horizon: str | None = None

class ReportSpec(BaseModel):
    name: str                            # 周报/月报/专题
    period: str | None = None
    audience: str | None = None
    channel: str | None = None           # feishu/dingtalk/email

class ClarifyField(BaseModel):
    id: str; label: str; question: str
    options: list[str] = []
    allow_custom: bool = True

class StepIntent(BaseModel):             # 能力步骤内嵌的、已裁剪的子语义
    kind: IntentKind
    metrics: list[Metric] = []
    dimensions: list[Dimension] = []
    time: TimeRange | None = None
    chart: Chart | None = None
    comparison: Comparison | None = None

class AgentIntent(BaseModel):
    kind: IntentKind
    question: str                        # 原始输入（含上一轮上下文已解析文本）
    metrics: list[Metric] = []
    dimensions: list[Dimension] = []
    time: TimeRange | None = None
    chart: Chart | None = None
    comparison: Comparison | None = None
    modeling: ModelingSpec | None = None
    report: ReportSpec | None = None
    clarify: list[ClarifyField] = []
    confidence: float = 0.0              # [0,1]
    producer: Literal["llm","fallback"] = "llm"
    reasons: list[str] = []              # 供审计展示「为什么这样理解」
    steps_hint: list[StepIntent] = []    # 复合/多步问题拆出的子语义

    model_config = ConfigDict(extra="forbid", populate_by_name=True)
```

> 上报/落库时给 `intent.py` 现有 `AnalysisTask.intent_json` 增加一个兼容字段 `intent`，**不变更 DB 结构**，只是把 `team/timeConstraint/groupBy` 作为 `AgentIntent` 的**派生视图**保留，前端与历史数据不受影响。

---

## 5. 各模块变更清单

| 模块 | 现状 | 目标 | 建议动作 |
|---|---|---|---|
| `intent_contract.py`（新增） | 无 | 定义 `AgentIntent` 与全部枚举 | 新增，作为唯一契约 |
| `app/agent/llm_intent.py`（新增） | 无 | LLM 意图解析器：`resolve_intent(question, context, metadata) -> AgentIntent` | 新增；结构化输出 + schema 校验 + 失败兜底 |
| `app/agent/intent.py` | 语义生产者（正则堆砌） | **降级为低置信 fallback 意图生产者** + 兼容任务视图 | `_time_constraint`/`_group_dimensions`/`_time_breakdown_dimensions`/`_requested_chart`/`_is_chart_only_directive`/`_is_turn_switch`/`_multi_chart_views` 只负责在 LLM 不可用时生成**同一 shape** 的 `AgentIntent`（`producer=fallback`）；`classify_request_kind`/`is_deterministic_conversation` 等改为「窄化路由门」的量词 |
| `app/agent/planner.py` | 正则触发规划 | 规划器只读 `AgentIntent`，不再正则推断 | `_looks_like_modeling`/`should_use_planner` 改为读 `intent.kind`；`build_fallback_plan` 用 fallback `AgentIntent`；`build_plan` 的 prompt 注入 `AgentIntent` + `allowedCapabilities` + `capability.input_schema` |
| `app/agent/graph.py` | 大量语义再推断 | 图骨架只做编排/校验/执行 | 删除/改造 `_detect_modeling_task_type`、`_inferred_query`、`_target_match_score`、`_requires_period_comparison`、`_build_retrieval_question`、`_requires_history_context`、`_time_field_candidates`；保留 `_safe_identifier`、`_validate_for_dialect`、`_sql_literal`、`_result_facts_for_explain`、`_published_metric_resolution`、`_task_constraint_validation`（这些是口径/校验/审计） |
| `app/agent/query_compiler.py` | 靠关键词推断轴/粒度/趋势 | 只消费 `AgentIntent` 的显式枚举 | 去掉 `_pick_metric`/rank/trend/share 里基于自由文本的 fallback 推断；保留 `_resolve_dimension`（元数据映射）、`_time_group`（方言渲染）、`_period_filter`；`_DIM_KEYWORDS`/`_dim_time_grain` 只作为 canonical 映射与 fallback，不再作为「用户想什么」的判据 |
| `app/agent/interpretation.py` | 自行选取值/维度列 | 消费 `AgentIntent.chart` 与 `query_compiler` 的 axis | 保留 `decide_chart`/`decide_charts` 作为**契约校验与默认**；`_pick_value_column`/`_pick_dimension_column` 优先用 intent 指定的 metric/dimension |
| `app/agent/runner.py` | 已走 plan+capability+governed graph | 路由门与意图解析接入 state | 在 `state` 增加 `agent_intent`；`intent_guard`/`plan_tasks` 改为消费 `AgentIntent`；`execute_inline_*` 复用同一 intent |
| `app/agent/state.py` | 字段有限 | 增加 `agent_intent`、`routing_decision` | 增量 |
| `app/agent/capability_registry.py`/`capabilities/*` | 已具备 | 增强 `input_schema`，并可声明 `resolves_kind` | 新能力声明 `inputSchema`（JSON Schema）供 planner 参考；为 report/monitoring/delivery/modeling 注册能力 |
| `app/agent/service.py` | utility 直回 | 走窄化路由门 | `respond_to_interaction` 用统一 `resolve_intent` |
| `app/agent/validators.py`（新增） | 无 | 确定性语义/安全校验 | 见 §6 |

---

## 6. 确定性校验层：`validators.py`

它不是语义判断，是**保证**。三条规则：空缺→澄清、非法→拒绝、合法→回填物理字段。

```python
def validate_intent(intent: AgentIntent, metadata: SemanticContext) -> ValidationResult:
    # 1) 能级/权限：能力白名单、role、scope、effectLevel、approvalPolicy
    # 2) 维度存在性：intent.dimensions[].semantic 必须能在 fact.fields 解析出列，否则给 clarify
    # 3) 指标存在性：intent.metrics[].semantic 必须能解析到 published metric / physicalFormula，否则 clarify
    # 4) 时间字段存在性：intent.time 需要的 timeField 必须登记，否则 clarify（而不是静默退化为聚合）
    # 5) 图类型/粒度枚举合法
    # 6) 回填：把 Dimension.code / Metric.id 回填（元数据解析），供 query_compiler 使用
```

关键行为变化：当「6月」指不出具体年份（数据地平线允许时）→ 明确锚定 `data_horizon`；当某维度当前数据源没有 → 澄清，而不是猜。这样就不会再有「5月/6月」或「工序」这种需要打补丁的坑。

---

## 7. `query_compiler` 改造

现状 docstring 已经写对了方向（「LLM 不写时间过滤与分组，由代码渲染」），缺的是**输入端的语义已经由 intent 决定**。改三点：

1. `compile_query(intent_metric, intent_dims, intent_time, dialect, metadata)`：入参不再是 `task` dict，而是 `AgentIntent`。维度用 `Dimension.code`（已回填），时间用 `TimeRange.periods`（已含具体 start/end），粒度用 `TimeRange.grain` / `Dimension.grain`。
2. 删除基于自由文本的推断：`_pick_metric(metrics, q)` 的 keyword 打分、`analysisType` 驱动的 rank/trend fallback、`"占比"/"前 N"` 这类 `q` 关键词判断。这些语义由 intent 给。
3. 保留的仍是「映射」而非「理解」：`_resolve_dimension`（dim→列，依赖元数据）、`_time_group`（grain→方言 SQL）、`_period_filter`（period→WHERE）、`_validate_for_dialect`。`_DIM_KEYWORDS` 与 `_dim_time_grain` 仅作为 canonical 别名与 fallback 保留。

---

## 8. 能力拓展协议（其它模块）

### 8.1 新模块注册一个能力即可

以「周报 + 飞书推送」为例（这类模块是 EXTERNAL 写入，必须走确认）：

```python
CapabilityDescriptor(
  name="report.generate_weekly",
  inputSchema={"$ref": "WeeklyReportParams"},   # planner 可据此填参数
  effectLevel=EffectLevel.EXTERNAL,             # 写外部/强副作用
  allowedRoles=["business","admin"],
  requiredScopes=["reports:generate"],
  approvalPolicy="user_confirm",                # 必须让用户确认后才推送
  costClass="llm_and_outbound",
  idempotent=True,
  delegated=True,                               # 走自己受治理子图
)
```
`report_capabilities()`、`delivery_capabilities()`、`monitoring_capabilities()` 在 `capabilities/registry.py` 的 `build_default_registry()` 里注册即可，规划器自动可见。

### 8.2 三类能力走法

| 类型 | effectLevel | 执行方式 | 例子 |
|---|---|---|---|
| 只读分析 | READ_ONLY / PURE | 固定受治理子图（只读、白名单、预算、证据） | `analysis.query_data`、`analysis.build_visualization` |
| 长任务 | LONG_RUNNING | 异步 + budget 上限 | `modeling.train`、`monitoring.scan` |
| 外部写入 | EXTERNAL | `approvalPolicy` 确认 + 受控频控 | `report.push.feishu`、`report.push.dingtalk`、`delivery.email` |

### 8.3 与 Agent 记忆/「越用越好用」机制的关系

记忆系统只负责**注入上下文**（历史偏好、已解析口径、常用维度别名），不负责**当前语义判定**。它给 `resolve_intent` 提供背景与别名，但最终语义由 LLM 在当前 `AgentIntent` 里拍板——两者不冲突，记忆是「输入增强」，意图是「输出契约」。

---

## 9. 迁移路线（渐进，测试保持绿）

### Phase A：并行产出（shadow），不改行为
- 新增 `intent_contract.py` + `llm_intent.py`，在 `runner` 里**旁路**解析出 `agent_intent` 落库与事件流。
- 与现有 `intent.py` 结果**对拍**：收集「两版语义不一致」的样本，修正 prompt/契约。
- 回归样例必须通过：①「对比5月与6月各产线良率，找出差异最大的两个工序再下钻」→ 对比 5/6 月 + 维度[产线,工序] + 下钻 top-2 diff；②「输出2026年总产量」→ 单年 bar；③「用柱状图展示」（上一轮业务问题）→ 继承上轮维度/时间/指标、只改图；④「分别按产线以及周数分类」→ **不是建模**，是 groupBy[产线, 周]。

### Phase B：治理层切到意图
- `planner.py` 读 `intent.kind`；`build_plan` 注入 `AgentIntent` 与各能力 `inputSchema`；`build_fallback_plan` 用 `producer=fallback` 的 `AgentIntent`。
- `intent.py` 的各语义函数标记为 `fallback` 生产者。
- 后端、前端、插桩测试全绿。

### Phase C：执行/编译层消费意图
- `graph.py` 删/改 semantic 再推断；`query_compiler` 改为消费 `AgentIntent`。
- `validators.py` 接入 `intent_guard` 与 `plan_tasks`。

### Phase D：收窄路由门
- `classify_request_kind`/`is_deterministic_conversation` 收窄成「纯 utility/纯 canned」判定；数据实体/指代/时间词一律交给 LLM 意图。
- 修复历史上因路由正则导致的误吞（参考 `_is_chart_only_directive` 打补丁的那种 case 迁到意图层）。

### Phase E：能力层++ 与其它模块
- 为 report/delivery/monitoring/modeling 注册能力；`build_plan` 的 `allowedCapabilities` 自然包含它们。
- 打通 EXTERNAL 确认步骤（已有 interrupt/`waiting_user`）。

---

## 10. 测试与验收

1. **存量回归**：`pytest tests/`（493）+ 前端 `npm test`（105）保持绿。
2. **意图契约 golden tests**：同一条业务问法（不同说法）映射到同一 canonical `AgentIntent`（归一化）。这正是消灭「每来一种新说法就补一条正则」的验收标准。
3. **回归样例集**：§9 四个样例 + 历史测试集（`docs/testsets/ForgeSight_自然语言问析测试集.xlsx`、多轮剧本）。
4. **一致性**：同一问题只产生一对轴/粒度/图；`query_compiler` 的 `axis` 与 `interpretation` 的 `chart.x/y` 必须同源。
5. **安全回归**：任何 `validators` 放行的能力都满足只读/白名单/预算；任何 `EXTERNAL` 都必须先 `approval`。

---

## 11. 风险与回退

- **LLM 输出不稳**：`AgentIntent` 走 Pydantic `extra=forbid` + 枚举校验；非法即 `producer=fallback`，降级到确定性兜底，**安全优先级不变**。
- **成本**：只有非 trivial 走 LLM 意图解析；纯 utility/canned 走固定路径，零 LLM。
- **兼容旧数据**：`AnalysisTask.intent_json` 保留现有字段，另加 `intent`，前端/历史不受影响。
- **灰度开关**：`should_use_planner` 的 `enabled` + 新增 `agent_intent_enabled`，可 A/B 对比开关前后。
- **回退**：任一路径（LLM 意图解析/校验/编译）崩溃，都落回现有确定性 `build_fallback_plan`。

---

## 12. 需要拍板的点

1. `intent.py` 的正则是**整体删除**，还是**保留为 fallback 生产者**？我建议保留（离线/降级/cost 敏感时需要），但只作为 `producer=fallback` 的低置信路径。
2. 是否允许 LLM 意图解析在「不确定有无数据」时自行决定，还是强制交给 `validators` 用元数据确认？我建议后者（避免凭空指向不存在的数据）。
3. `query_compiler` 是否接受「多周期对比 + 下钻 top-k」的 SQL 形态，还是保持单周期、把对比交给多步？建议保持单周期、多步（复用现有 dependent/parallel task 能力），这样安全面最小。

---

## 13. 实现状态与验收（2026-09-09）

### 已完成

- **Phase A（shadow）**：`app/agent/intent_contract.py`（`AgentIntent` + 枚举，`extra=forbid`、`populate_by_name`）、`app/agent/llm_intent.py`（`resolve_intent` + `build_fallback_intent` + `_extract_json`）；`runner.execute_run` 在开关开启且非恢复时旁路产出 `agent_intent` 注入 state 并落 `intent.agent_resolved` 事件，失败必降级。
- **Phase B（规划消费）**：`planner.should_use_planner` 新增 intent 分支（`INTENT_MULTI_TASK`/`INTENT_SIMPLE_FAST_PATH`/`INTENT_COMPLEX_ANALYSIS`/`INTENT_CROSS_CAPABILITY`）；`build_plan` 向 LLM 注入 `agentIntent` + `capabilitySchemas`；`build_fallback_plan` 消费 `intent_to_tasks`；`graph.route_request` 按 `intent.kind` 覆盖路由；`graph.decide_planning_path`/`build_business_plan` 传递意图。
- **Phase C（执行/编译消费 + 校验）**：`query_compiler.compile_intent`（经 `intent_to_tasks` 复用 `compile_query`，不改 `compile_query` 签名，避免影响 `monitoring.worker`）；`graph.plan_tasks` 快速路径在存在 `agent_intent` 且可解析时直接产出意图任务（修复「5月/6月只取第一个月」「工序未入维度」两个根因）；`graph.retrieve_context` 在拿到元数据后调用 `validate_intent` 做校验/回填并写 `intent_validation`/`intent_degraded`（只标记，不猜语义）。
- **死代码清理（已确认无引用）**：删除 `plan_questions`、`categorical_cols`、`RunNotFound`、`UsageRecord`。

### 新增文件

- `app/agent/intent_contract.py`、`app/agent/llm_intent.py`、`app/agent/intent_adapter.py`、`app/agent/validators.py`、`app/agent/query_compiler.py`（`compile_intent`）。
- `tests/test_agent_intent_contract.py`、`tests/test_agent_intent_driven.py`。

### 开关与灰度

- `config.agent_intent_enabled`（`.env`：`AGENT_INTENT_ENABLED`），默认 `false`；开启后旁路/规划/编译语义来自 LLM `AgentIntent`，任何解析失败回退到确定性 `build_fallback_plan`（`producer=fallback`），安全优先级不变。建议真机 A/B 对比开关前后再决定常态化开启。

### 测试

- 后端 `pytest backend/tests/`：**506 passed, 5 skipped**。新增 13 个 intent 契约/驱动用例（contract round-trip、LLM 路径、坏 JSON 回退、`intent_to_tasks` 拆分对比+保留工序、`validate_intent` 解析维度/指标、无法解析 → `ask_clarify`、`compile_intent` 渲染 SQL）。

### 已完成（本轮收尾）

- **Phase D（路由门收窄）**：`classify_request_kind`/`is_deterministic_conversation` 增加 `intent_enabled` 开关与 `_has_data_signal` 弱信号兜底；开启意图时，携带强业务实体/维度/图表词的问询即使句式像常识问答，也会被送往 LLM 意图，而不是被闲聊路径吞掉。纯闲聊/范围外仍走固定路径（零 LLM）。`service.create_run` 已接入该开关。
- **Phase E（能力注册 + EXTERNAL 确认）**：新增 `delivery.push`（`EXTERNAL_EFFECT` + `approvalPolicy=always`）与 `monitoring.scan`/`monitoring.list_rules` 能力并注册到 `build_default_registry`；`BUSINESS_CAPABILITY_SCOPES` 与 graph 默认范围补齐 `report:send`/`report:publish`/`modeling:train`/`investigation:write`/`delivery:send`/`monitoring:read`/`monitoring:scan`，使 `allowedCapabilities` 自动包含它们；`authorize_capability` 的 EXTERNAL 中断确认已打通。
- **AgentIntent 仅信任 LLM 产出**：`producer == "llm"` 才会驱动 `route_request`/`should_use_planner`/`plan_tasks`/`build_fallback_plan`；`producer == "fallback"` 一律走确定性 `build_intent_plan`，与未开启意图时行为一致，避免 fallback 意图误判建模/复合问题。
- **写意图前置拦截**：`runner.execute_run` 在意图解析之前先检查 `WRITE_TERMS`，删除/更新等写入问法不进 LLM，由 `intent_guard` 一键阻断（保持「写意图在 LLM 前拦截」的安全性质）。
- **恢复不重放 LLM**：`execute_run` 先探测 `has_checkpoint`，恢复场景跳过意图解析，避免把已完成 Run 当作新输入重放 `intent.parse`。

### 环境问题修复

- **worker 卡死/事件序号冲突**：`event_store.append_event` 改为先 `flush` 再 `SELECT ... FOR UPDATE` 锁定 run 行，并以数据库 `MAX(seq)+1` 作为权威 seq。根治 `usage.start_call/finish_call` 跨 Session 追加 `llm.*` 事件时 `run.last_event_seq` 陈旧导致的 `uq_analysis_event_seq` 冲突与 `idle in transaction` 死锁（已加 `idle_in_transaction_session_timeout=120s` 兜底）。
- **LLM 慢/回退**：`.env` 改用 `deepseek-chat`，直连/代理均实测 ~0.5-1.1s；修复 `intent_contract.py` 对 `comparison.by` 字符串与 `clarify:null` 的强校验拒绝，避免合法意图被误判为非法而降级。

### 尚未完成（低优先级，需拍板）

- 后端仍残留 21 个仅周边模块引用、生产链路无引用的符号（reports/monitoring/delivery/knowledge），多为 WIP，**不建议**在未确认前删除。
- `AGENT_INTENT_ENABLED` 仍为 `.env` 显式开启（默认 `false`），是否纳入仓库默认值待定。
