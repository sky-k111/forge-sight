# ForgeSight Agent 记忆系统调研与设计

## Executive Summary（执行摘要）

ForgeSight 适合增加记忆系统，但不适合把某个通用框架整套替换进来。当前系统已经具备四类分散能力：LangGraph State 与 checkpoint 保存单次运行状态，`analysis_message` 保存会话记录，企业语义检索提供指标与表字段知识，“越用越聪明”机制把成功分析与用户反馈归纳为受治理的程序性技能。它们分别接近工作记忆、会话记忆、语义知识和程序性记忆，却缺少统一的记忆抽取、跨会话召回、时间有效性、冲突消解、遗忘与审计模型。因此，当前记忆不是“完全没有”，而是“有零件、没有记忆层”。

本报告建议采用五层工程模型：工作记忆、会话短期记忆、语义记忆、情景记忆和程序性记忆。这里的“五层”是结合 CoALA、LangGraph 和制造业问析场景得到的工程归纳，并非行业存在唯一公认的“五大框架”。开源实现方面，LangMem 与现有 LangGraph 技术栈最贴合；Mem0 适合作为通用用户记忆的接口参考与对照基线；Letta 是完整的有状态 Agent Runtime，侵入性过强；Graphiti 擅长带时间有效期的事实与关系；Cognee 更接近知识图谱与企业知识摄取平台。ForgeSight 应建设完整的领域化 `Memory Service`，复用 PostgreSQL、现有 embedding、混合检索、事件存储和治理体系，不额外引入一套会与现有系统争夺控制权的 Agent Runtime。

“记忆系统”和“越用越聪明”不会天然冲突。前者回答“过去发生了什么、当前用户偏好什么、哪些事实仍有效”，后者回答“遇到这类问题应该怎么分析”。正确关系是：分析事件先进入情景记忆；经过反馈、去重、重复证据和管理员审核后，才可能晋升为程序性技能。两者若都直接从一次成功对话自动写入执行提示，才会产生重复、错误放大和治理冲突。

## Introduction（调研范围与概念澄清）

LangGraph 官方将 thread-scoped state/checkpoint 定义为短期记忆，而跨 thread 的 Store 才是长期记忆；官方同时把长期内容区分为事实型语义记忆、经历型情景记忆和规则型程序性记忆[1]。CoALA 从认知架构角度把语言 Agent 描述为包含模块化记忆、内部/外部行动空间和决策过程的系统[2]。因此，“把聊天记录存进数据库”并不等于拥有完整 Agent 记忆。

本项目采用以下五层定义：

| 层级 | 保存什么 | ForgeSight 当前状态 | 主要缺口 |
|---|---|---|---|
| 工作记忆 | 当前 Run 的任务、SQL、校验、结果引用、重试状态 | 已有 `AnalysisState` 和 checkpoint | 基本够用，需控制状态体积 |
| 会话短期记忆 | 当前会话最近消息、澄清结果、分支上下文 | 已保存消息，但执行时通常只取最近 6 条 | 没有滚动摘要、重要消息保留策略 |
| 语义记忆 | 用户偏好、组织事实、指标口径、业务对象 | 企业知识库较强，用户偏好记忆缺失 | 知识与记忆边界、时间有效期、冲突版本 |
| 情景记忆 | 某次分析的问题、条件、动作、结果、失败原因、反馈 | 有 Run/Event/Artifact 作为原始事实 | 没有抽取成可检索的“经验片段” |
| 程序性记忆 | 可复用的分析步骤、约束和输出规范 | 已有受治理的 `analysis_skill` | 候选归纳仍较粗，需由情景证据驱动 |

这一分类把常被混淆的三个概念分开：知识库保存企业认可的业务事实；记忆保存交互中形成且有作用域、时间和来源的经验；技能保存经过治理的分析方法。

## Main Analysis（当前实现审计与方案比较）

### 当前实现审计

当前会话上下文在执行入口按消息序号读取最近 6 条，随后放入 `conversation_context`；只有当问题被判断为真实追问时，检索节点才把有限历史拼入检索问题。这个设计成本可控，但用户跨十几轮重新引用早期条件时容易遗忘，也无法跨会话记住个人偏好。

当前 checkpoint 的价值是故障恢复、人工澄清恢复和“从此节点继续分析”。LangGraph 官方也明确区分 checkpointer 与 Store：前者保存单 thread 状态快照，后者才保存跨 thread 的应用级长期数据[1]。所以 checkpoint 不应被包装成长期记忆。

当前“越用越聪明”机制已经是合格的程序性记忆雏形：技能只在开关开启时检索；命中同时采用词法与向量得分；使用情况被记录；成功 Run 获得正向/纠正反馈后只生成候选技能；候选必须经过管理员发布才生效。它的不足不是“没有学习”，而是候选技能只按良率、设备、库存和通用分析四类规则粗归纳，没有利用结构化的失败原因、修正内容、数据覆盖和结果质量形成更精细的经验。

### 五个主流开源方案

### 3.1 LangMem：最适合作为设计与组件来源

LangMem 提供 hot-path 记忆工具和后台记忆管理器，可抽取、合并和更新长期知识，并原生对接 LangGraph Store；官方建议生产环境使用 Postgres Store 而不是进程内 Store[3]。它与 ForgeSight 的 LangGraph、PostgreSQL 和异步 worker 方向一致。

但 LangMem 不能原样解决制造业权限问题。需要自定义 namespace、记忆 schema、准入规则和检索过滤，禁止模型在热路径中自由写入企业事实。建议只复用其 memory manager 思路和 Store 接口模式，不直接给 Agent 暴露无约束的 `manage_memory` 工具。

### 3.2 Mem0：适合作为接口参考与对照基线

Mem0 提供 `add/search/update/delete/history` 等完整 API，并支持 user、agent、run 等作用域以及自托管方式[4]。其论文采用“从对话抽取显著信息—合并更新—按需检索”的长期记忆路线，并报告相对全上下文方法更低的延迟和 token 开销[5]。

对于 ForgeSight，Mem0 最大优点是提供较完整的跨会话记忆接口；缺点是会引入另一套抽取、向量存储和检索逻辑，与现有 PostgreSQL 语义检索重复。若接入，应把它封装在 `MemoryRepository` 适配器后，由 ForgeSight 自己的 tenant、source、role、validity 和 evidence 过滤器约束。默认实现仍使用本项目的 PostgreSQL 存储，Mem0 适配器用于同题 A/B 评测和未来可替换性验证。

### 3.3 Letta / MemGPT：思想值得借鉴，Runtime 不宜接管

MemGPT 把有限上下文类比为操作系统内存层级，通过 core、recall、archival memory 和中断机制管理长上下文[6]。Letta 延续这一方向，定位是完整的 stateful-agent 平台，而不是一个轻量数据库插件[7]。

ForgeSight 已经拥有 Agent 图、事件存储、checkpoint、外部渠道、权限与安全校验。如果换成 Letta Runtime，会同时出现两套会话、工具调用、状态机和记忆生命周期，改造成本高且会弱化现有证据链。因此只借鉴“核心记忆常驻、档案记忆按需检索、后台整理”的层级思想，不直接嵌入 Letta。

### 3.4 Graphiti：适合时间事实与关系图扩展

Graphiti 保存实体、带有效期的关系、原始 episode 与自定义 ontology，并让派生事实可追溯到原始 episode[8]。Zep 论文强调企业 Agent 面临的是持续对话与动态业务数据，而不是静态文档检索，因此采用时间感知知识图维护历史关系[9]。

这与制造业“指标口径从某日生效”“设备归属产线发生变更”“用户偏好已被新选择覆盖”等问题高度匹配。但 Graphiti 自托管还要求图数据库、实体抽取、图检索和运维；其开源版本并不自带 ForgeSight 的用户、会话和治理层[8]。完整方案通过 `TemporalMemoryRepository` 保留图存储扩展点：常规事实先使用 PostgreSQL 的有效时间和版本字段，多跳关系与复杂时间推理达到明确阈值时再切换 Graphiti 实现，不让基础设施选型阻塞领域能力。

### 3.5 Cognee：更像知识与记忆摄取平台

Cognee 通过向量、知识图谱和 ontology，把多格式数据变成跨会话可检索记忆[10]。它适合文档、数据和知识的大规模摄取，也提供 LangGraph 集成。但 ForgeSight 已经完成企业语义层、数据源物理绑定、向量化与隔离，直接引入会与现有知识库能力高度重叠。

如果未来扩展“企业文档知识库”，Cognee 可作为独立试验；对于当前“从分析交互中学习”，它不是最短路径。

### 推荐架构：ForgeSight Memory Service

完整方案继续使用 PostgreSQL 和现有 embedding 服务，增加一层领域化 Memory Service，而非切换 Agent 框架。该服务不是一张“聊天摘要表”，而是一套覆盖采集、抽取、治理、召回、使用、反馈、合并、遗忘和技能晋升的独立领域子系统。

```text
Analysis Run / Feedback / User Correction
                 │
                 ▼
       append-only Memory Event
                 │
      后台抽取、脱敏、分类、去重
                 │
        ┌────────┴────────┐
        ▼                 ▼
  Semantic Memory   Episodic Memory
  偏好/稳定事实       问题/条件/动作/结果
        │                 │
        └────检索与重排────┘
                 │
        注入受限的 Agent Context
                 │
        多次证据 + 用户反馈
                 ▼
     Candidate Procedural Skill
                 │
             管理员审核
                 ▼
       Published Analysis Skill
```

### 4.1 组件架构

Memory Service 由十个可独立测试的组件组成：

| 组件 | 职责 |
|---|---|
| Memory Event Collector | 接收 Run、消息、反馈、修正、报告使用和外部渠道事件 |
| Memory Extractor | 用结构化输出从事件中抽取偏好、事实、经历和反思候选 |
| Policy & Redaction Guard | 执行敏感字段、权限、数据源范围和禁止记忆规则 |
| Memory Classifier | 判定 semantic、episodic、procedural-candidate 及作用域 |
| Deduplicator & Conflict Resolver | 去重、合并、版本化、冲突检测和事实失效 |
| Memory Repository | 统一增删改查、版本、证据和有效时间接口 |
| Hybrid Retriever & Reranker | 权限过滤后执行词法、向量、时间、重要度和置信度重排 |
| Context Assembler | 在 token 预算内生成带来源的结构化 Agent 上下文 |
| Consolidation & Forgetting Worker | 后台摘要、聚类、衰减、过期、压缩和物理清理 |
| Governance & Evaluation | 用户管理、管理员审核、A/B 实验、指标和审计日志 |

### 4.2 数据模型

建议新增以下核心表：

1. `agent_memory`：记忆正文、结构化 payload、类型、scope、置信度、重要度、有效时间、状态和版本。
2. `agent_memory_evidence`：关联 Run、Event、Message、Artifact、Feedback，保证每条记忆可追溯。
3. `agent_memory_relation`：表达 `supports / contradicts / supersedes / derived_from / similar_to` 关系。
4. `agent_memory_embedding`：复用现有语义对象索引，不重复实现 embedding 供应商逻辑。
5. `agent_memory_access_log`：记录检索、重排、注入、采用和结果，用于 A/B 评估与清理。
6. `agent_memory_feedback`：记录用户确认、纠正、拒绝、遗忘请求和管理员处置。
7. `agent_memory_job`：承载抽取、合并、重嵌入、过期和技能晋升等可重试后台任务。
8. `agent_memory_policy`：定义组织、角色、记忆类型的保留期、自动写入和审核要求。

作用域至少包含 `tenant_id / source_id / user_id / role_id / session_id / channel`。检索顺序必须是权限和有效期硬过滤，再做混合召回和重排，不能先全库向量搜索再事后过滤。

`agent_memory` 的状态机为：

```text
observed → candidate → active → superseded / expired / rejected
                            └→ archived → deleted
```

每条 active 记忆必须带 `recorded_at`、`valid_from`、`valid_to`、`confidence`、`provenance` 和 `policy_version`。删除采用先逻辑删除、后按保留策略物理清理；被新事实替代时保留历史版本，避免制造业时间比较引用今天的事实解释过去的数据。

### 4.3 写入、归纳与冲突处理

不建议让 Agent 每一步都自主写记忆。LangMem 同时支持 hot path 与 background manager[3]，但 ForgeSight 涉及企业数据，完整方案采用“热路径只写事件，后台生成候选，显式事实可即时确认”的双通道：

- 用户明确说“以后默认按自然月”“我更喜欢折线图”时，生成用户偏好候选，可让用户确认。
- Run 完成或失败后，把问题、口径版本、时间范围、表、SQL 指纹、结果摘要、质量分和反馈写成 episode。
- 后台 worker 只从 episode 抽取候选事实或经验，不直接改已发布指标和技能。
- 冲突记忆不覆盖删除，旧版本标记 `superseded`，保留有效时间与证据。

抽取器必须输出固定 JSON Schema，包括记忆类型、主语、谓词、宾语、适用范围、时间、置信度、敏感级别和证据引用。确定性 Guard 随后执行：禁止保存原始思维链；禁止保存明细结果行；禁止把临时推导口径标为企业事实；禁止跨 scope 合并；没有证据的模型反思只能成为低置信度 episode。

冲突处理按来源优先级和时间处理：企业已发布口径高于模型推断；用户显式修正高于隐式偏好；新版本只在有效时间重叠时使旧版本失效；无法自动判断的冲突进入管理员队列。任何冲突都不允许静默覆盖。

Generative Agents 采用完整经验流、重要度/时近性/相关性检索和高层反思[11]；Reflexion 则把任务反馈归纳为语言反思并保存到情景缓冲区供后续试验使用[12]。可借鉴二者，但本项目必须增加证据、权限和审核约束，不能把模型反思当成事实。

### 4.4 召回、重排与上下文组装

在现有图中增加受控记忆支路。企业语义、记忆和技能可以并行召回，再由确定性 `assemble_context` 节点合并：

```text
route_request → plan_tasks → activate_task → plan_context
                                         ├→ retrieve_enterprise_context
                                         ├→ retrieve_memory
                                         └→ retrieve_skill
                                                   ↓
                                            assemble_context
                                                   ↓
                                      metric/sql/safety/execution
```

1. 意图层输出本轮是否需要长期记忆以及所需类型。
2. 以当前用户、企业、数据源和任务为 namespace 做硬过滤。
3. 使用关键词 + embedding + recency + importance + confidence + feedback 重排。
4. 只注入少量结构化摘要，并带 `memory_id`、来源、有效期和置信度。
5. SQL 安全、指标口径、白名单和成本门控仍由原确定性节点执行；记忆永远不能授予权限。

建议重排分数为可配置加权模型：语义相关度 35%、词法匹配 15%、时间相关度 15%、重要度 10%、置信度 15%、用户/任务反馈 10%。这只是默认值，最终由离线评测调参；企业已发布事实不依靠相似度“竞争”权限，必须先由 scope 和类型规则选入候选集。

上下文组装器分别输出 `userProfile`、`relevantEpisodes`、`verifiedFacts`、`negativeLessons` 和 `analysisSkills`，每组设置条数与 token 上限。前端证据链展示命中记忆的摘要、来源、有效期和置信度，业务用户可以标记“有帮助/不相关/已过期”。

### 4.5 遗忘、压缩与生命周期

完整记忆系统必须能忘记。会话摘要定期压缩但保留关键澄清；低重要度 episode 随时间衰减；长期未命中且无正向反馈的隐式偏好自动过期；已发布企业事实按版本保留而不进行普通衰减；涉及个人数据的记忆支持用户主动删除和保留期策略。

后台 consolidation worker 定期完成相似记忆聚类、重复合并、矛盾检测、摘要再生成、embedding 更新和孤立证据清理。任务具备幂等键、最大重试、死信状态和可观察日志，后台失败不阻断主分析链路。

### 4.6 API 与前端产品能力

业务端增加“我的偏好与记忆”，而不是暴露底层向量：用户可查看系统记住的图表偏好、常用时间口径、最近经验，执行纠正、停用和删除；分析证据链新增“使用的历史经验”，明确本次结论是否受历史记忆影响。

管理员端增加“记忆治理”，包含候选记忆、冲突队列、企业级事实、保留策略、敏感信息命中、跨源隔离检测、技能晋升和 A/B 实验看板。管理员不能默认查看无权限用户的完整私有对话，只能查看必要摘要和审计证据。

对外 API 采用稳定领域接口：`remember`、`search`、`correct`、`forget`、`consolidate`、`promote`。内部通过 `MemoryRepository` 抽象存储，默认 PostgreSQL 实现，同时保留 Mem0 和 Graphiti adapter，避免将 Agent 节点绑定到某个供应商。

## Synthesis（与“越用越聪明”的关系）

两者是上下游，不是替代关系：

| 能力 | 记忆系统 | 越用越聪明技能 |
|---|---|---|
| 保存对象 | 用户偏好、事实、单次经历、失败原因 | 可复用的分析流程与约束 |
| 生效范围 | 用户/会话/企业/数据源 | 全局或数据源 |
| 写入门槛 | 自动候选，可确认或后台治理 | 必须管理员审核发布 |
| 使用方式 | 提供上下文和相似案例 | 指导任务怎么执行 |
| 错误风险 | 过期、冲突、越权召回 | 错误流程被重复执行 |

建议晋升规则为：单次成功只能形成 episode；同类问题累计至少 N 次正向证据、无高危失败、且修正内容一致时才生成技能候选；技能发布后仍保留证据 Run 列表和版本。一次失败可形成“不要这样做”的情景记忆，但不能自动改写系统提示或发布反向规则。

### 是否可以直接使用主流框架

结论是“可以复用组件和模式，不能零定制接入”。适配优先级如下：

| 方案 | 适配度 | 建议 |
|---|---:|---|
| LangMem / LangGraph Store | 5/5 | 作为接口和后台归纳模式来源；存储仍落现有 PostgreSQL |
| Mem0 | 4/5 | 作为接口参考、可替换适配器和对照实验，不立即作为主存储 |
| Graphiti | 3/5 | 作为复杂时间事实与多跳关系的可选存储适配器 |
| Cognee | 2.5/5 | 留给企业文档/知识摄取扩展，当前重复建设较多 |
| Letta | 2/5 | 借鉴分层思想，不接管现有 Agent Runtime |

## Recommendations（实施顺序与验收）

目标交付是完整闭环，而不是“先存起来以后再治理”：记忆开关、用户偏好、分析 episode、后台抽取、权限过滤、混合召回、来源展示、编辑删除、冲突合并、有效期、衰减遗忘、技能晋升、管理端治理和 A/B 对照都属于同一套完成标准。实际编码可按依赖顺序拆分提交，但产品验收以整个闭环为准。

验收不能只看“能不能想起来”，至少比较：跨会话偏好命中率、相似问题 SQL 首次成功率、追问澄清次数、记忆误召回率、过期记忆使用率、token/延迟开销、开关前后任务成功率。Mem0 的开源 benchmark 仓库覆盖事实召回、时间推理、多跳与跨会话问题，可借鉴评测结构，但 ForgeSight 还必须自建制造业 SQL 正确性与权限隔离测试集[13]。

测试体系包括单元测试、状态迁移测试、抽取 Schema 测试、召回评测、跨租户泄漏测试、过期事实测试、矛盾记忆测试、删除合规测试、后台任务恢复测试和端到端 SQL 回归。A/B 实验在创建 Run 时快照开关及策略版本，比较无记忆、仅用户偏好、完整记忆三组，防止因为测试时配置漂移得出错误结论。

生产观测至少包含：记忆抽取成功率、候选激活率、召回命中率、采用率、负反馈率、过期命中率、冲突积压、跨 scope 拦截次数、平均注入 token、增加延迟、技能晋升率，以及使用记忆前后的 SQL 首次成功率和结论纠正率。

### 风险边界

不得保存原始 Chain-of-Thought；不得把查询结果明细行长期写入用户记忆；不得把模型临时推导的指标升级为企业事实；不得跨企业、数据源或角色召回；用户必须能查看、纠正和删除个人记忆；管理员只能治理企业级记忆与技能，不能默认浏览无关用户私密对话。记忆命中必须在证据链中显示“命中了什么、来自哪里、是否仍有效”，否则系统会从可审计 Agent 退化为不可解释的提示词污染。

## Implementation（当前实现映射）

本分支已经将方案落为产品闭环，而不是单独的聊天记录表：

| 方案能力 | 当前实现 |
|---|---|
| 统一记忆对象 | `agent_memory` 保存 preference、episodic、semantic、procedural 四类长期记忆；LangGraph State 和最近消息继续承担工作记忆与短期记忆 |
| 来源与生命周期 | evidence、relation、feedback、job、policy 独立建模；支持 candidate、active、superseded、expired、deleted 状态以及纠正和遗忘 |
| 数据隔离 | 召回 SQL 首先硬过滤 tenant、source、user 和有效期，再做相关性排序；记忆不能扩大数据表或字段白名单 |
| 混合召回 | PostgreSQL/pgvector 与现有阿里云 embedding 配置复用；不可用或 SQLite 测试环境自动退化到词法、时近性、重要度和置信度排序 |
| Agent 接入 | 在每个分析任务的技能与企业语义检索之前增加 `memory.retrieve`；记忆只补全用户未明确指定的展示偏好，显式问题始终优先 |
| 后台沉淀 | 成功 Run 通过持久化 job 生成精简 episode；显式分析偏好可自动沉淀；后台任务失败有限重试且不阻断主查询 |
| 技能晋升 | episode/procedural memory 可由管理员提升为 candidate skill，仍必须经过原有技能审核发布流程 |
| 用户控制 | 业务端“我的偏好与记忆”支持查看、添加、纠正与遗忘；不向用户暴露向量或底层存储 |
| 管理治理 | 管理端“记忆治理”提供总览、候选/生效/覆盖状态、合并过期、技能提升和安全边界说明 |
| 可解释与实验 | Run 快照保存 memory on/off；证据链展示召回节点和相关记忆产物；治理页比较两组成功率、Token 与耗时 |

数据库增量位于 `database/postgresql/012_agent_memory.sql`。功能默认关闭，管理员开启后只影响新创建的 Run，使开启前后对照不受运行中配置漂移影响。

## Methodology（调研方法）

本报告于 2026-08-24 核对 ForgeSight 当前分支中的 Agent State、会话消息、checkpoint、语义检索和受治理技能实现；外部资料仅采用项目官方文档、官方 GitHub 仓库与原始研究论文。比较维度包括架构侵入性、与 LangGraph/PostgreSQL 的重叠、租户和数据源隔离、时间有效性、审计能力、运维成本与可评测性。框架厂商自行公布的 benchmark 只用于理解其设计目标，不直接作为选型排名依据。

## Bibliography

[1] LangChain (2026). "Memory overview". LangChain Documentation. https://docs.langchain.com/oss/python/concepts/memory

[2] Sumers, T. R., Yao, S., Narasimhan, K., Griffiths, T. L. (2023). "Cognitive Architectures for Language Agents". arXiv. https://arxiv.org/abs/2309.02427

[3] LangChain AI (2026). "LangMem". GitHub. https://github.com/langchain-ai/langmem

[4] Mem0 (2026). "Universal memory layer for AI Agents". GitHub. https://github.com/mem0ai/mem0

[5] Chhikara, P. et al. (2025). "Mem0: Building Production-Ready AI Agents with Scalable Long-Term Memory". arXiv. https://arxiv.org/abs/2504.19413

[6] Packer, C. et al. (2023). "MemGPT: Towards LLMs as Operating Systems". arXiv. https://arxiv.org/abs/2310.08560

[7] Letta AI (2026). "Letta". GitHub. https://github.com/letta-ai/letta

[8] Zep (2026). "Graphiti: Build Real-Time Knowledge Graphs for AI Agents". GitHub. https://github.com/getzep/graphiti

[9] Rasmussen, P. et al. (2025). "Zep: A Temporal Knowledge Graph Architecture for Agent Memory". arXiv. https://arxiv.org/abs/2501.13956

[10] Cognee (2026). "The Open-Source AI Memory Platform for Agents". GitHub. https://github.com/topoteretes/cognee

[11] Park, J. S. et al. (2023). "Generative Agents: Interactive Simulacra of Human Behavior". arXiv. https://arxiv.org/abs/2304.03442

[12] Shinn, N. et al. (2023). "Reflexion: Language Agents with Verbal Reinforcement Learning". arXiv. https://arxiv.org/abs/2303.11366

[13] Mem0 (2026). "Memory Benchmarks". GitHub. https://github.com/mem0ai/memory-benchmarks

## Limitations（调研限制）

各项目公布的 benchmark 使用的数据集、模型、提示词、检索预算和评审模型并不完全一致，不能仅凭项目方给出的单一百分比决定选型。本报告的适配结论主要依据 ForgeSight 当前代码、基础设施重叠度、治理要求和可迁移成本；框架性能仍应通过同一组制造业问题、同一模型和相同 token 预算做 A/B 验证。
