# ForgeSight 六项用户价值优化方案调研

> 版本：2026-08-22  
> 范围：A07 企业数据底座智能问析 Agent；面向业务人员端的产品深化，不包含机器学习模块  
> 上游文档：[ForgeSight 制造业用户需求深度调研](./ForgeSight_制造业用户需求深度调研.md)

## Executive Summary（执行摘要）

这次调研不是继续给 ForgeSight 增加“更多页面”和“更多 AI 能力”，而是回答六个更接近真实制造现场的问题：用户登录后先看什么，异常怎样主动到人，问题怎样连续调查，分析怎样变成有人负责的行动，班次之间怎样交接，以及业务人员凭什么相信回答。

调研结论是：ForgeSight 已经具备自然语言问析、语义检索、只读 SQL、安全校验、图表、报告、证据链和 LangGraph 持久化等基础，不适合再横向堆叠一套 IoT、流程、数据治理或 LLMOps 大平台。更合理的做法是吸收成熟系统的领域模型：借鉴 ThingsBoard 的告警去重、严重度、确认和清除生命周期；借鉴对象中心流程挖掘将工单、产品、批次、工序、设备、物料和班次组织为同一调查；借鉴 LangGraph 的中断恢复和 Camunda 的用户任务语义实现人在回路；借鉴 Oracle 的生产交接报告；借鉴 OpenMetadata、OpenLineage 与 Phoenix，把技术证据翻译为业务可理解的可信回答卡。[2][5][8][11][13][14][16]

六项能力中，最值得在比赛版本形成完整演示的是“异常收件箱 → 调查工作区 → 行动草稿与人工确认 → 自动复查 → 交接摘要 → 可信证据”。它比单独展示 ChatSQL 更贴近生产主管、质量主管和设备主管的真实工作，也能把赛题要求的 Agent 闭环、业务价值、工程可用性和可信执行放在一条故事线上。

新技术可以成为加分项，但必须有边界。对象中心流程挖掘（OCEL/OCPM）适合制造业多对象追溯；AAS 适合作为设备语义互操作的可选适配层；持久化 Human-in-the-loop 和 durable agent 适合跨小时、跨班次的任务；OpenTelemetry/Phoenix 适合 Agent 可观测；LangMem、Langfuse/Argilla 和 DSPy GEPA 适合构建受审核的持续改进流水线。它们都不应被包装成“AI 自动学会一切”，更不应在没有回归评测和人工批准时在线修改生产口径或提示词。[7][9][10][16][17][18][19][20]

## Introduction（范围、方法与假设）

本文将上游调研中的六项 P0 建议逐项展开：角色化今日工作台、异常与变化收件箱、制造调查工作区、人在回路的行动闭环、班次交接与日会摘要、可解释的可信回答卡。每项均按“官方数据实例—用户任务—成熟方案—开源/新技术—ForgeSight 裁剪实现—比赛演示—风险”展开。

实例中的数值来自赛题方提供的 2026-06-01 至 2026-07-15 模拟数据。它们是真实存在于附件中的演示事实，但不是某家真实工厂的运营结论。[1] 外部方案优先使用官方文档、标准和项目仓库；GitHub 活跃度和版本信息具有时间性，只用于判断当前可借鉴性，不等于对项目长期维护的保证。

## Main Analysis：六项优化方案

### 建议一：角色化“今日工作台”

#### 1.1 实际例子

假设登录人是三车间生产主管。2026-07-15 当天，三车间 3 号线投入 46,080 件、合格 44,690 件，按赛题口径计算良率 96.98%，是当天五条有产量产线中最低的一条；三车间 6 号线在主数据中处于维护状态。[1] 用户早上只有十分钟准备班前会，他真正需要的不是先选数据库，再打开“智能问析”输入问题，而是直接看到：

1. 我的责任范围：三车间、3 号线和 6 号线；
2. 今天最值得关注：3 号线良率处于当日产线末位；6 号线维护；
3. 数据边界：数据最新到 2026-07-15，并非实时采集；
4. 可执行入口：调查 3 号线良率、查看维护影响、发起交接。

当前“一个总览服务所有业务人员”的问题，是生产主管、质量主管与设备主管看到相同卡片，却要自己把卡片翻译成岗位任务。成熟制造系统更强调工作中心、工单、异常和岗位权限。Oracle 的生产主管工作台把工单、异常和报告组织在同一工作空间；ERPNext 的制造对象也围绕 Work Order、Job Card、工位、检验与实际作业时间组织，而不是围绕数据库表组织。[4][21]

#### 1.2 可借鉴方案与取舍

| 方案 | 可借鉴内容 | 不建议直接照搬的原因 |
|---|---|---|
| Oracle Production Supervisor Workbench | 按工作中心和班次组织任务、异常、报告 | 商业套件，不适合作为比赛依赖 |
| ERPNext Manufacturing | Work Order、Job Card、检验、工位等业务对象结构 | ForgeSight 不是 ERP/MES，不能复制其交易流程 |
| ThingsBoard Asset hierarchy | 设备—产线—车间的资产关系和告警向上可见 | 赛题数据主要是业务表，不是实时 IoT 遥测 |
| Eclipse BaSyx AAS | 以标准化资产标识、子模型描述设备能力 | AAS 接入成本较高，应作为可选适配层而非 P0 前置条件 |

ThingsBoard 能将设备告警传播到上级资产，并按用户范围显示；Eclipse BaSyx 则实现了 AAS 3.1 系列规范及 JSON、XML、AASX 与标准 API，可用于未来接入遵循工业数字孪生标准的设备语义。[2][7] 对 ForgeSight 而言，真正应借鉴的是“角色—责任范围—业务对象—任务”的绑定，而不是新建一套资产平台。

#### 1.3 ForgeSight 推荐实现

第一版只需在现有 PostgreSQL 平台库补充四类对象：`role_profile`、`user_scope`、`workspace_subscription`、`workspace_item`。`user_scope` 表达用户可见的数据源、工厂、车间、产线和主题；`workspace_item` 不是永久业务事实，而是由异常、报告、待确认口径和行动任务投影出的首页条目。

首页信息层级建议固定为：

- 第一层“现在需要我处理什么”：最多 3 条，按影响、紧急度、可信度和责任匹配排序；
- 第二层“我的运行范围”：本班产量、良率、停机、库存等 3—5 个岗位 KPI；
- 第三层“最近分析与交接”：历史任务、未关闭调查和上一班摘要；
- 只保留一个主按钮“发起分析”，卡片点击直接携带数据源和对象范围进入分析。

所谓“AI 工作台”不应让模型自由决定首页内容。候选条目必须来自确定性的订阅、异常和任务查询，LLM 只负责把事实压缩为岗位语言。这样既能角色化，又能保持可审计。

#### 1.4 比赛加分点

演示时分别切换生产主管和质量主管账号：前者看到三车间良率与设备状态，后者看到传感器26的抽检不良和缺陷构成。两人的数据源可以相同，但首页任务不同。这能直观证明 ForgeSight 不是“套了制造业皮肤的通用 BI”，而是理解岗位和责任范围的数据底座入口。

### 建议二：异常与变化收件箱

#### 2.1 实际例子

赛题数据中，2026-07-09 至 2026-07-15，二车间 5 号线共有 7 次非计划停机，累计 340 分钟，为五条生产线中最高；其中 2026-07-15 19:10 至 21:10，回流焊设备 E0034 因“缺料等待”停机 120 分钟。[1] 如果系统只在用户问“最近哪条线停机最多”时回答，发现已经滞后。更符合设备主管和生产主管工作方式的是：系统主动形成一条异常，并说明它为何值得处理。

一个合格的收件箱条目应显示：`L05 最近7天非计划停机340分钟（最高）`、本次最新事件 120 分钟、原因“缺料等待”、影响对象、数据时间、可信度、负责人、状态，以及“进入调查/确认/指派/忽略”动作。

#### 2.2 主流方案

ThingsBoard 的告警模型已经把这一问题拆得很成熟：告警包含触发对象、类型、严重度、状态、开始时间、最近事件时间和负责人；同一“对象 + 类型”的活动告警会更新而不是重复创建；状态由活动/清除与已确认/未确认两个维度组成；系统支持自动清除、评论、指派、REST API 和多渠道通知。[2] 它的通知规则还能配置升级链；若前一阶段有人确认或清除，后续升级停止。[3]

这是 ForgeSight 最值得直接借鉴的领域模型。ThingsBoard 本身是 Apache-2.0 的活跃开源 IoT 平台，但引入整个系统会带来设备接入、规则引擎、消息基础设施和独立权限体系，超出当前赛题需要。[22] 第一版更适合在现有平台库内实现业务异常生命周期。

#### 2.3 ForgeSight 推荐实现

建议引入：

```text
signal（原始变化）
  → detection_rule（阈值、同比/环比、趋势、数据质量、报告发现）
  → exception（去重后的业务异常）
  → assignment / comment / notification
  → acknowledge → investigate → resolve → verify → close/reopen
```

最小数据表可包括 `detection_rule`、`detected_signal`、`business_exception`、`exception_activity` 和 `exception_subscription`。去重键推荐为 `data_source + object_type + object_id + rule_id + time_bucket`；相同异常在窗口内更新峰值、持续时间和样本，不反复刷屏。严重度不让 LLM拍脑袋，而由规则映射业务影响；LLM可生成说明，但必须引用触发值、基线和时间范围。

检测器分三档上线：第一档复用 SQL 定时任务做阈值和数据质量检查；第二档增加滚动基线、同比/环比和变化点；第三档才接流式遥测。赛题数据是离线 CSV/数据库，第一档已经能完整演示，没必要伪装成实时 IoT。

“新鲜技术”可以用在规则配置而非规则执行。ThingsBoard 已提供 AI 辅助配置告警规则的交互，但真正执行仍是确定性条件。[3] ForgeSight 可以让管理员用自然语言说“某产线最近7天非计划停机超过历史中位数50%时提醒设备主管”，由 Agent 生成规则草稿、预览命中历史数据，人工发布后才生效。

#### 2.4 比赛加分点

最强演示不是红色卡片，而是完整状态变化：L05 异常自动出现 → 设备主管确认 → 系统合并同类事件 → 超时升级给生产主管 → 原因调查完成 → 指标恢复后自动建议关闭。这样既解决告警疲劳，又体现 Agent 主动发现与工程闭环。

### 建议三：制造调查工作区

#### 3.1 实际例子

最近 7 天，产品 P026“传感器26”的质量检验抽样 1,330 件、不良 52 件，抽检不良率 3.91%，在产品中最高。进一步展开，功能测试不良率 5.53%，老化测试 5.35%；缺陷数量最多的是包装破损 15 件和老化异常 13 件；该产品同期在 L04、L05、L01 三条线生产，其中 L04 工序数据不良率最高，为 3.51%。[1]

这不是一个“再问四次”的问题。质量主管的真实调查链是：异常产品 → 工单 → 产线 → 工序 → 检验单 → 缺陷 → 设备/停机 → 班次 → 现场补充事实。当前聊天界面能逐步查询，但证据被拆散在不同消息和 SQL 中，用户很难形成一个可交接、可复核的“调查对象”。

#### 3.2 主流与新技术

对象中心流程挖掘（Object-Centric Process Mining，OCPM）正适合这一类制造问题。传统流程挖掘要求每个事件属于一个 case，但生产事件经常同时关联机器、工单、组件、产品和交付；强行选一个 case 会复制事件或丢失关系。[5][6] PM4Py 已提供 OCEL 对象汇总、时间汇总、对象交互、下钻/上卷、对象图、对象中心直接跟随图和 Petri 网，还提供面向 LLM 的 OCEL/OC-DFG 抽象接口。[5]

AAS 是另一条互补路线：它给设备和资产提供稳定标识与标准子模型，Eclipse BaSyx Python SDK 支持 AAS 3.1 系列规范、AASX、JSON/XML 和标准服务接口。[7] OCPM回答“对象之间发生了什么”，AAS回答“这个设备对象是什么、有哪些标准属性”。两者都可作为未来互操作亮点，但第一版不应把所有赛题表强行改造成完整 OCEL 或 AAS。

#### 3.3 ForgeSight 推荐实现

在现有元数据和分析 Artifact 之上增加最小对象事件层：

```text
business_object(object_type, object_id, source_id, display_name, attributes)
business_event(event_type, occurred_at, source_table, source_pk, attributes)
event_object_link(event_id, object_type, object_id, role)
investigation(id, exception_id, owner, status, hypothesis, conclusion)
investigation_evidence(investigation_id, artifact_id/event_id/note_id, evidence_kind)
```

接入数据源时，Agent 根据外键、字段名、样例值和管理员确认生成对象映射草稿。例如 `mes_process_output.work_order_id` 映射工单，`product_id` 映射产品，`process_id` 映射工序，`line_id` 映射产线；映射发布后才能参与调查。这样保持“对制造业数据库通用”，又不假设所有客户表名一致。

前端调查工作区应是一个稳定页面而不是聊天长列表：左侧为异常摘要、影响和当前假设；中间为对象关系与事件时间线；下方为正常组/异常组对比、Pareto、控制图和结果表；右侧为证据、现场事实和待确认项。聊天作为操作入口，例如“只看夜班”“和正常工单对比”，但每次结果写回同一个 investigation。

PM4Py 只在用户选择“查看流程路径/异常路径”时离线计算 OC-DFG，不进入每个问题的必经链路。AAS 只做可选导入适配器：若客户提供 AASX，就映射设备主数据；否则继续使用普通维表。这个裁剪能保留新技术价值，避免架构失控。

#### 3.4 比赛加分点

以“为什么传感器26最近不良升高”为演示：系统自动构造产品—工单—产线—工序—检验—缺陷图，突出功能测试、老化测试和 L04，并把“包装破损可能与功能测试问题不是同一根因”标成两个待验证假设。评委能看到系统没有用相关性冒充因果，也能看到同一次调查如何复用证据。

### 建议四：人在回路的行动闭环

#### 4.1 实际例子

针对传感器26异常，Agent 不应直接写入 QMS 或下达停线命令。它可以生成行动草稿：

> 建议对 L04 上 P026 在功能测试和老化测试环节加严抽检；质量负责人王某，截止 2026-07-16 12:00；同时核查包装工序“包装破损”15件是否属于独立问题。预期验证：后续两个班次抽检不良率回落到已发布阈值内。

质量主管可以批准、编辑或拒绝。批准后第一版在 ForgeSight 建立任务并记录外部 QMS 单号；若客户提供 API，再通过 outbox 适配器创建外部任务。到期后 Agent 重跑同一指标，若未改善则建议升级，而不是把“创建了任务”等同于“问题解决”。

#### 4.2 主流方案与架构选择

LangGraph 的 Human-in-the-loop 可以在敏感工具调用前中断，允许 approve、edit、reject 或 respond，并要求通过持久化 checkpointer 保存状态；官方建议生产环境使用 Postgres 或 MongoDB saver。[8][9] 这与 ForgeSight 已有 LangGraph、PostgreSQL checkpoint、运行事件和 Artifact 体系高度匹配。

Camunda 的 BPMN User Task 提供 assignee、候选用户/组和任务可见性等完整语义，适合已经采用 BPMN 的大型企业。[11] Temporal 则把工作流执行定义为可跨故障恢复、可持续数秒到数年的 durable execution，并已有面向 Agent/HITL 的集成示例。[10][23] 但为比赛同时引入 LangGraph、Camunda 和 Temporal，会出现三套状态机和运维负担。

#### 4.3 ForgeSight 推荐实现

保持 LangGraph 为分析编排，增加应用自有的 `action_item` 领域模型：

```text
drafted_by_agent
→ awaiting_confirmation
→ accepted / edited / rejected
→ assigned
→ acknowledged
→ in_progress
→ resolved
→ verification_pending
→ closed / reopened
```

敏感边界由确定性策略决定：只读查询自动执行；创建 ForgeSight 内部行动需要用户确认；写入 QMS/EAM/MES、通知外部人员或改变生产状态必须中断并显示目标系统、参数、影响范围和幂等键。批准后写入 `integration_outbox`，worker 负责调用外部 API；外部单号和回执写回 Artifact。重试必须使用幂等键，避免网络抖动时重复建单。

当前阶段不引入 Camunda/Temporal。只有当后续客户要求跨数周审批、复杂 BPMN、数十种外部系统补偿或海量长期任务时，再将行动执行层替换或桥接到它们。这样既能答辩说明生产级演进路线，也不会牺牲第一版可运行性。

#### 4.4 比赛加分点

演示“Agent 提建议、人承担责任、系统验证效果”：在证据链中展示行动由谁批准、改了什么、外部单号、何时复查、复查 SQL 与指标结果。这个闭环比宣称“Agent 可以自己调用所有系统”更可信，也更符合制造现场安全边界。

### 建议五：班次交接与日会摘要

#### 5.1 实际例子

2026-07-15 夜班，二车间 5 号线回流焊 E0034 因缺料等待停机 120 分钟；最近 7 天该线累计非计划停机 340 分钟。接班人员不仅需要知道“停了120分钟”，还需要知道临时措施、物料是否到位、哪些工单受影响、下一班要做什么，以及谁已经接收。[1]

一个好的交接摘要应分成事实与人工说明：系统事实来自停机表、工单、产量、质量和未关闭行动；现场说明由交班人补充“物料预计 22:30 到线，先切换另一工单”；接班人必须确认。下一班打开工作台时，这条未关闭事项自动置顶，不会沉没在聊天记录或飞书群消息中。

#### 5.2 成熟产品做法

Oracle 的生产主管 Shift Report 就是非常直接的行业参照：它包含工单指标、未关闭异常、已完成和待完成工序、操作员签到；主管可手工填写摘要或用 AI Assist 生成，并保存为可检索 PDF。Oracle 明确指出，这类报告传统上往往由手写记录和多个界面人工汇总，用于向同事和利益相关者交接需要后续行动的问题。[4][12]

这一证据说明 ForgeSight 的报告能力不应只停留在周报/月报。对制造现场而言，班次交接是频率更高、更贴近执行的报告对象。生成式 AI 的合理位置是把结构化事实和人工记录压缩成摘要，而不是补写数据库里没有发生的事实。

#### 5.3 ForgeSight 推荐实现

复用现有阶段六报告引擎与飞书通道，新增 `shift_handover` 模板和三张轻量表：`shift_definition`、`handover_report`、`handover_acknowledgement`。生成过程为：

1. 按数据源、车间、产线、班次冻结事实快照；
2. 汇总计划/实际产量、良率、停机、质量、未关闭异常和行动；
3. LLM 仅根据冻结 Artifact 生成“本班变化—未结问题—临时措施—下一班动作”；
4. 交班主管编辑确认；
5. 发布到业务端和飞书，接班人确认；
6. 未关闭项投影到下一班工作台，禁止因报告发布而自动关闭。

报告必须显示“生成时刻的数据状态”。Oracle 也提示，开放异常等信息反映的是报告生成时的状态。[4] ForgeSight 应保存 `data_watermark`、指标版本和 Artifact ID，防止第二天回看时数字随源数据变化。

#### 5.4 比赛加分点

在一次演示中，先由系统生成 L05 夜班交接，再在飞书收到摘要并确认；切换到白班主管账号后，工作台置顶显示未关闭的缺料事项。它把已有报告、飞书、账号角色、异常和行动串成完整产品故事，开发成本也明显低于新做一套排产或 IoT 系统。

### 建议六：可解释的“可信回答卡”

#### 6.1 实际例子

用户问“这个月和上个月的良率比较”时，数据底座的最新日期只有 2026-07-15。即使 SQL 正确，“这个月”也只是 7 月 1—15 日的半月数据；若 6 月有整月数据，直接比较会受覆盖天数和产品结构影响。单独显示“高置信度 98%”会让业务用户误以为结论完整。

可信回答卡应该明确告诉用户：

- 口径：工序良率，已发布版本，公式 `SUM(good_qty)/SUM(input_qty)`；
- 时间：2026-07-01—07-15 对 2026-06-01—06-30，当前月不完整；
- 范围：数据源、产线/产品过滤和班次；
- 数据：最新时间、覆盖天数、样本量、缺失/重复检查；
- 执行：SQL 只读校验、成本门控、结果行数和结果一致性；
- 结论限制：只能做阶段性同比/环比描述，不能直接判断因果；
- 总体建议：将“98%”改成“可用但需注意时间不可比”，或要求用户确认采用“截至同期”比较。

#### 6.2 主流方案与新技术

OpenMetadata Standards 把指标、术语、数据契约、质量测试、事件、事件处理、列级血缘、所有者和审计统一为结构化元数据，并覆盖新鲜度、完整性、分布等质量能力。[13] OpenLineage 用 Run、Job、Dataset 和可扩展 Facet 描述运行时血缘，已经支持 Spark、Airflow、dbt、Flink 等集成，是未来跨数据管道追踪的标准化选择。[14]

Phoenix 使用基于 OpenTelemetry/OpenInference 的 Agent/LLM trace，可记录工具、检索、会话、成本，并给 span 增加人工或自动评价；它更适合研发和评测人员，而不是直接把技术 trace 展示给生产主管。[15][16] 因此 ForgeSight 应实行“双层证据”：业务卡先讲口径、覆盖和限制，技术证据链再提供 SQL、执行步骤、Artifact 和 token。

#### 6.3 ForgeSight 推荐实现

停止把可信度当成模型随手生成的一个百分数。建议定义可计算的五维对象：

| 维度 | 主要输入 | 硬门禁示例 |
|---|---|---|
| `semantic_trust` | 指标是否发布、版本、字段映射、临时推导 | 临时口径最高只能“中” |
| `data_trust` | 新鲜度、覆盖、样本、空值、重复 | 缺关键周期时不得“高” |
| `scope_trust` | 时间、对象、数据源、权限、可比性 | 比较范围不一致必须提示或追问 |
| `execution_trust` | 只读校验、EXPLAIN、执行成功、结果复核 | SQL/结果校验失败直接不可用 |
| `interpretation_trust` | 结论是否越过数据证据、是否声称因果 | 相关分析不得表述为原因 |

总等级由规则聚合，而不是五项简单平均；任何硬门禁都能封顶。后端响应增加 `trust_contract`，保存组件分数、证据 ID、封顶原因和用户可读限制。前端默认显示“已发布口径 / 数据截至7月15日 / 当前月仅15天 / SQL已校验”，点击展开才看五维细节和完整证据链。

OpenLineage 不必在比赛版强制部署：若当前数据通过 CSV/MySQL 初始化，现有 source/table/SQL/artifact 证据已经足够；未来接入 Airflow/dbt 时再消费 OpenLineage 事件。Phoenix 也可先用于开发环境的 Agent traces 和回归评测，不必替换当前业务证据链。

#### 6.4 比赛加分点

用同一个问题演示两次：第一次比较 7 月半月和 6 月整月，系统将可信度封顶并建议“截至同期”；用户确认后重算 7 月 1—15 对 6 月 1—15，可信卡恢复更高等级。评委能看到可信度会随着数据条件变化，而不是固定装饰标签。

## Synthesis：横向加分项——让系统越用越好，但不让它偷偷改规则

六项能力都会产生高价值反馈：用户忽略了哪个异常、修改了什么行动、纠正了哪个字段、哪条结论被判定无用。生产级做法不是把每次反馈立刻写进提示词，而是形成“采集—标注—评测—审批—发布”的受控改进循环。

Langfuse 支持从真实 trace 建立版本化数据集、人工标注、用户反馈和线上/离线评测；Argilla 面向领域专家收集反馈并构建高质量数据集；Phoenix 也支持 trace、数据集、实验和人工 annotation。[15][17][18] LangMem 可从会话中提取并管理长期记忆，且可使用 PostgreSQL store 持久化，但它解决的是“记住什么”，不是“这条记忆是否有权成为企业真相”。[19]

ForgeSight 推荐把记忆分为三类：

- 用户偏好：如“默认看二车间夜班”，可以低风险自动生效；
- 调查经验：如“包装破损通常先核查包装工序”，只能作为候选经验并显示来源；
- 企业语义：指标公式、口径、字段映射和业务规则，必须管理员审核、版本化、按数据源隔离。

每次发布语义或提示词前，从失败 trace 和人工反馈构建 golden dataset，至少回归 SQL 正确性、指标口径、范围解析、图表适配、结论忠实度和安全边界。DSPy GEPA 这类反思驱动的指令优化器能够基于验证集分数和文字反馈提出 prompt 候选，是一个新颖的离线实验方向；但其候选必须经过固定测试集、人工比较和版本发布，禁止直接在线替换生产 prompt。[20]

这个横向机制的答辩表述应是“受治理的持续改进”，而不是“Agent 自我进化”。前者可验证、可回退、可审计，后者很容易被评委追问污染、越权和错误放大。

## Recommendations：综合架构与优先级

六项能力可以共享一条事件主线，而不是成为六个孤立模块：

```text
业务表/元数据/指标口径
        ↓
定时检测与数据质量检查
        ↓
异常收件箱 ──→ 角色化今日工作台
        ↓
制造调查（对象、时间线、证据、假设）
        ↓
Agent 行动草稿 ── Human-in-the-loop ── 外部系统 outbox
        ↓
指标自动复查 ── 关闭/重开
        ↓
班次交接、日会摘要、周报
        ↓
反馈/标注 ── 离线评测 ── 审核发布
```

### 推荐迭代顺序

| 迭代 | 交付 | 原因 | 预计难度 |
|---|---|---|---|
| 1 | 可信回答卡改造 + 今日工作台责任范围 | 复用已有置信度、知识、权限和总览；立即修复用户信任与首页定位 | 中 |
| 2 | 异常收件箱最小生命周期 | 让产品从被动问答变成主动工作入口 | 中 |
| 3 | 班次交接模板与接班确认 | 高业务价值，最大程度复用报告和飞书 | 中 |
| 4 | 调查工作区与对象事件层 | 差异化强，但需要映射、页面和数据模型 | 中高 |
| 5 | 行动草稿、确认、outbox、自动复查 | 形成真正闭环，需权限、幂等与外部接口边界 | 中高 |
| 6 | 反馈队列、golden dataset、离线优化实验 | 为长期质量建设，不应抢在业务闭环之前 | 中高 |

### 第一版应做与不应做

第一版应做：岗位范围、异常规则草稿与发布、告警去重和确认、一个质量调查模板、内部行动任务、LangGraph 人工确认、交接摘要、五维可信卡、可回看的证据和审计。

第一版不应做：部署完整 ThingsBoard/Camunda/Temporal/OpenMetadata；声称实时 IoT；自动写入 MES；完整数字孪生；用 OCPM 自动证明根因；让 LangMem 或 GEPA 在线修改企业口径。把这些保留为架构演进点，反而更能显示团队知道生产系统的边界。

## 十一、建议的连续演示脚本

1. 以质量主管登录，今日工作台提示“传感器26最近7天抽检不良率3.91%，产品中最高”。
2. 打开异常收件箱，查看触发值、基线、样本量、数据截至日期和负责人；点击确认并进入调查。
3. 调查工作区自动连接 P026、相关工单、L04/L05/L01、功能测试、老化测试及缺陷类型；系统给出两个待验证假设，不宣称根因已经确定。
4. Agent 生成“L04 加严抽检 + 包装破损分流核查”的行动草稿；主管编辑负责人和期限后批准。
5. LangGraph 从持久化中断点恢复，写入内部任务和模拟外部 QMS 单号；证据链记录批准人、参数和幂等键。
6. 系统按计划复查后续两个班次指标；演示数据不够时明确显示“等待新数据”，而不是伪造恢复结果。
7. 夜班结束后生成交接摘要，飞书通知白班主管；白班确认后未关闭行动进入其今日工作台。
8. 最后展开可信回答卡，说明口径、数据范围、当前月完整性、SQL校验、结论限制和完整证据链。

这套脚本在一条业务故事里同时覆盖自然语言分析、业务知识、数据资源理解、Agent 编排、安全、图表、报告、外部触达、异常处理和工程可信度，比逐个点击功能菜单更接近真实产品验收。

## Limitations（局限与风险）

赛题数据没有人员排班、目标值、行动单、实时设备遥测和现场文本，因此今日工作台的责任范围、异常基线和闭环验证需要补充演示数据或后台配置。所有示例都应在产品中标为“模拟企业数据”，不能暗示为真实客户生产事件。

对象中心流程挖掘依赖可靠的事件时间和对象标识；如果客户数据库缺少工单、批次或设备关联，系统应降低关系可信度并请求管理员补映射。AAS 只对已有标准资产模型的客户有直接收益。

人在回路不能只做前端确认弹窗。后端必须持久化中断、鉴权、幂等、审计和恢复；外部系统调用失败要与分析失败分开呈现。LLM 生成的行动和摘要必须引用冻结事实，不能成为执行授权本身。

开源项目的许可证、版本与部署成本需要在正式集成前再次核查。本文推荐的主要是领域模型和接口标准，不等于必须把对应项目作为依赖。

### Counterevidence Register（反证与边界）

| 容易得出的结论 | 反证或边界 | 本文处理 |
|---|---|---|
| 有告警平台就应直接部署 ThingsBoard | ForgeSight 当前主要处理关系型业务数据，不具备实时遥测链路；完整 IoT 平台会重复权限、资产和规则能力 | 只借鉴告警领域模型，第一版用 SQL/任务检测 |
| OCPM 能自动找到根因 | OCPM 展示对象交互和路径差异，相关路径并不等于因果关系 | 输出证据和假设，不输出“已证明根因” |
| 接入 AAS 后即可适配所有制造企业 | 许多企业没有 AASX 或规范化设备子模型 | AAS 作为可选适配器，普通数据库映射仍是主路径 |
| 引入 Temporal/Camunda 才算生产级 | 当前已有 LangGraph 与 PostgreSQL 持久化；多编排器会增加一致性和运维风险 | 先做应用自有行动状态与 outbox，复杂长流程再演进 |
| AI 生成交接摘要可以替代人工交接 | 数据库缺少临时调机、人员和现场处置等事实 | 摘要基于冻结事实，允许人工补充并要求接班确认 |
| 置信度分数越精细越可信 | 不透明的 97% 仍无法说明时间覆盖和口径风险 | 使用五维证据与硬门禁，分数仅为辅助 |
| Agent 从反馈自动学习越快越好 | 单次错误反馈可能污染全企业语义或造成跨数据源泄漏 | 反馈进入隔离队列、离线评测和审核发布 |

### Claims-Evidence Table（关键结论与证据）

| 关键结论 | 主要证据 | 支持状态 |
|---|---|---|
| 首页应按岗位责任范围组织 | 赛题数据实例、Oracle 主管工作台、ERPNext 制造对象 [1][4][21] | 支持 |
| 异常需要去重、确认、指派、清除和升级 | ThingsBoard 告警与通知模型 [2][3] | 支持 |
| 制造调查适合最小对象事件层 | PM4Py 与 OCPM 文献 [5][6] | 支持 |
| 行动闭环采用持久化 HITL 与应用状态 | LangGraph persistence/HITL、Camunda/Temporal 对照 [8][9][10][11] | 支持 |
| 交接报告应冻结事实并保留未关闭事项 | Oracle Shift Report [4][12] | 支持 |
| 可信卡应结合语义、质量、血缘和执行证据 | OpenMetadata、OpenLineage、Phoenix [13][14][15][16] | 支持 |
| 持续改进必须经过标注、评测和审核 | Langfuse、Argilla、LangMem、GEPA [17][18][19][20] | 支持，但 GEPA 仅建议离线试验 |

## 十三、结论

六项优化背后其实只有一个产品转向：ForgeSight 不再只是“业务人员不会 SQL 时来提问的工具”，而是“围绕制造异常组织事实、调查、责任、行动、交接和复盘的可信工作空间”。

最有辨识度的技术组合不是最多，而是边界清楚：确定性检测和安全门禁负责不可破坏的规则；LLM负责语义理解、调查辅助和表达；LangGraph负责可恢复的 Agent 状态；PostgreSQL负责业务事实、证据和审计；人在回路负责生产责任；可选的 OCPM、AAS、OpenLineage、Phoenix 与离线 prompt 优化负责未来扩展。这个定位既符合 A07 的 Agent 要求，也能在答辩中经得住“为什么有用、为什么可信、出了错怎么办、怎么接真实企业”的连续追问。

## Bibliography（参考来源）

[1] 杭州自动化技术研究院赛题附件（2026），《制造业生产质量分析数据介绍》及配套 CSV，`D:/aaa/a-外包/data/数据介绍.md`（访问：2026-08-22）。

[2] ThingsBoard（2026），[Alarms](https://thingsboard.io/docs/user-guide/alarms/)（访问：2026-08-22）。

[3] ThingsBoard（2026），[Alarm Rules](https://thingsboard.io/docs/user-guide/alarm-rules/)；[Notification Rule Triggers](https://thingsboard.io/docs/reference/notification-system/rule-triggers/)（访问：2026-08-22）。

[4] Oracle（2024），[Create and Store a Shift Report in the Production Supervisor Workbench](https://docs.oracle.com/en/cloud/saas/readiness/scm/24d/mfg24d/24D-mfg-wn-f34294.htm)（访问：2026-08-22）。

[5] PM4Py（2026），[Object-Centric Process Mining and LLM Integration API](https://github.com/process-intelligence-solutions/pm4py/blob/release/docs/source/api.rst)（访问：2026-08-22）。

[6] Wil M. P. van der Aalst（2024），[How Object-Centric Process Mining Helps](https://www.vdaalst.com/publications/p1520.pdf)（访问：2026-08-22）。

[7] Eclipse BaSyx（2026），[BaSyx Python SDK](https://github.com/eclipse-basyx/basyx-python-sdk)（访问：2026-08-22）。

[8] LangChain（2026），[Human-in-the-loop middleware](https://github.com/langchain-ai/docs/blob/main/src/oss/langchain/human-in-the-loop.mdx)（访问：2026-08-22）。

[9] LangChain（2026），[LangGraph Persistence](https://github.com/langchain-ai/docs/blob/main/src/oss/langgraph/persistence.mdx)（访问：2026-08-22）。

[10] Temporal（2026），[Workflow Execution](https://github.com/temporalio/documentation/blob/main/docs/encyclopedia/workflow/workflow-execution/workflow-execution.mdx)（访问：2026-08-22）。

[11] Camunda（2026），[BPMN User Tasks](https://docs.camunda.io/docs/components/modeler/bpmn/user-tasks/)（访问：2026-08-22）。

[12] Oracle（2026），[Smart Operations: Create and Store a Shift Report](https://docs.oracle.com/en/cloud/saas/readiness/common/rrdem/smart-operations-create-and-store-a-shift-report-in-the-production-supervisor-workbench.html)（访问：2026-08-22）。

[13] OpenMetadata（2026），[OpenMetadata Standards](https://github.com/open-metadata/OpenMetadataStandards)（访问：2026-08-22）。

[14] LF AI & Data / OpenLineage（2026），[OpenLineage](https://github.com/OpenLineage/OpenLineage)（访问：2026-08-22）。

[15] Arize AI（2026），[Phoenix: AI Observability & Evaluation](https://github.com/Arize-ai/phoenix)（访问：2026-08-22）。

[16] Arize AI（2026），[Phoenix tracing, evaluation and annotations guide](https://github.com/Arize-ai/phoenix/blob/main/docs/phoenix/skill.md)（访问：2026-08-22）。

[17] Langfuse（2026），[Langfuse](https://github.com/langfuse/langfuse)；[Datasets](https://github.com/langfuse/langfuse-docs/blob/main/content/docs/evaluation/experiments/datasets.mdx)（访问：2026-08-22）。

[18] Argilla（2026），[Argilla](https://github.com/argilla-io/argilla)（访问：2026-08-22）。

[19] LangChain（2026），[LangMem](https://github.com/langchain-ai/langmem)（访问：2026-08-22）。

[20] DSPy（2026），[GEPA in depth](https://github.com/stanfordnlp/dspy/blob/main/docs/docs/diving-deeper/gepa-in-depth.md)（访问：2026-08-22）。

[21] Frappe（2026），[ERPNext Job Card domain implementation](https://github.com/frappe/erpnext/blob/develop/erpnext/manufacturing/doctype/job_card/job_card.py)（访问：2026-08-22）。

[22] ThingsBoard（2026），[ThingsBoard GitHub repository](https://github.com/thingsboard/thingsboard)（访问：2026-08-22）。

[23] Temporal（2026），[OpenAI Agents SDK Integration for Temporal](https://github.com/temporalio/sdk-python/blob/main/temporalio/contrib/openai_agents/README.md)（访问：2026-08-22）。

## Methodology Appendix（方法附录）

本报告采用“实际数据核验—官方产品/标准调研—开源实现比较—项目适配”四步法。首先读取赛题方数据说明并用 CSV 聚合核对案例数值；其次优先检索产品官方文档、标准项目与开源仓库；再按用户价值、与现有架构适配度、许可证/运维成本和比赛可演示性进行取舍；最后检查 ForgeSight 已有 LangGraph、PostgreSQL、语义检索、证据链、报告与飞书能力，避免重复建设。

主要结论至少由“制造业务场景证据 + 一个成熟领域模型 + ForgeSight 现有能力”联合支持。对较新的技术，如 durable agents、LLM 辅助规则配置和 GEPA，只将其列为受控增强或演进方向，不把实验性能力描述为已达到生产标准。
