# ForgeSight 受限规划型 Agent 最终架构与改造方案

> 状态：最终目标设计，用于后续代码改造、接口评审、前端联调、测试验收与答辩说明。  
> 适用范围：智能问析、报告生成、智能建模、调查记录、通用系统能力、记忆与经验增强、飞书/钉钉入口。  
> 术语说明：本项目使用的是 **LangGraph**。用户讨论中出现的 “LangFlow” 在本文均按 LangGraph 理解；LangFlow 是另一类可视化编排产品，不是当前运行时。

## 1. 设计结论

ForgeSight 不应改造成可以任意调用底层工具的自由 ReAct Agent，也不应继续只把所有问题塞进一条不可调整的固定流水线。最终采用：

> **轻量请求路由 + 受限 Planner/Supervisor + 业务级 Capability Registry + 现有受治理 LangGraph 子图 + 确定性安全与发布策略。**

核心分工如下：

- 大模型负责理解目标、拆分任务、选择业务能力、根据结构化观察调整一次计划，以及组织最终解释。
- LangGraph 负责状态、条件路由、暂停恢复、能力编排、检查点、有限重试和运行预算。
- 现有 SQL 子图负责语义绑定、生成、校验、成本门控、执行、纠错、结果校验和证据，不允许 Planner 跳过其中任一步骤。
- 报告、建模、调查等领域服务负责真实业务动作，不在 Agent 节点中重复实现。
- 权限、租户、数据源范围、只读边界、指标发布、模型训练、报告发布与外部发送不由模型自由决定。

这套设计的目标不是让每次请求都多调用一次大模型，而是让复杂问题具备可观察、可限制、可回退的规划能力，同时保持简单问题原有的准确率、速度与安全边界。

## 2. 为什么采用这一方案

### 2.1 当前系统已经具备的基础

当前 `backend/app/agent/graph.py` 已经包含：

```text
route_request
  → system_utility / intent_guard
  → plan_tasks
  → activate_task
  → retrieve_memory
  → retrieve_skill
  → retrieve_context
  → propose_modeling 或 generate_query
  → validate_query
  → execute_query
  → prepare_retry（有限）
  → explain_result
  → advance_task
```

并已经具备多问题拆分、主动澄清、指标口径解析、混合检索、多数据源方言、只读白名单、任务约束、EXPLAIN 成本门控、有限重试、失败 SQL 指纹、置信度、证据链、检查点、记忆与经验召回。上述能力不重写，只通过稳定契约封装并纳入能力注册表。

### 2.2 当前不足

现有 `plan_tasks` 主要负责把一句话拆成多个线性任务。任务进入分析后，大部分路径仍固定，难以清楚表达：

- 为什么先查某项数据；
- 查询结果不足时为什么换维度或补充查询；
- 什么时候应该复用已发布模型；
- 什么时候应该把分析产物整理为报告；
- 哪个操作需要用户确认；
- Agent 为什么决定结束而不是继续尝试。

因此当前系统“功能可用”，但复杂任务的自主决策和产品可解释性不够显性，评委可能把它理解为一条固定工作流。

### 2.3 开源项目的共同经验

代码级调研表明，数据问答 Agent 没有“必须自由 ReAct”的单一主流：

1. LangGraph 官方 SQL Agent 使用列举表、读取 Schema、生成、检查、执行、错误回环的受控图，而不是无边界循环。[1]
2. DataHub Analytics Agent 使用模型驱动工具循环搜索文档、业务定义、Schema、血缘和 SQL，但其通用 SQLAlchemy 执行层没有 ForgeSight 现有的代码级只读解析，因此只能借鉴上下文优先和工具组织方式，不能照搬执行边界。[2][3]
3. `eosho/langchain_data_agent` 使用外层意图/数据源路由和内层固定 SQL 子图，并用 `sqlglot` 强制 SELECT、危险函数限制和 LIMIT，和 ForgeSight 的目标最接近。[4][5]
4. `shreyabaid007/text-to-sql-agent` 采用模型—工具循环，但把只读校验和人工审核放进不可绕过的数据库工具，证明自主选择工具与硬安全边界可以共存。[6][7]
5. DataAgent-Pro 使用 Supervisor 在 Schema、SQL、分析、可视化和报告能力之间路由，各能力内部仍为固定链；这是本方案“外层动态、内层受控”的直接参考。[8][9]
6. ORQ 示例不让模型自由写 SQL，而是让模型选择参数化业务查询工具，适合 ForgeSight 已发布、高频指标的稳定查询路径。[10][11]
7. DataFoundry 使用不同的 Agent 运行时，但其语义 Trace、工具/产物分离、检查点延续和上下文选择记录适合前端证据链设计；不照搬其通用 Skill/MCP 自由度。[12]

由此锁定：模型自主性应放在**业务能力选择和计划调整**，而不是数据库安全、权限和执行器内部。

## 3. 最终逻辑架构

```text
网页 / 飞书 / 钉钉 / 定时调度 / 内部事件
                    │
                    ▼
        Identity + Tenant + Source Scope
                    │
                    ▼
        Request Segmenter / Lightweight Router
          ├─ conversation ────────────────┐
          ├─ utility fast path ───────────┤
          ├─ simple analysis fast path ───┤
          └─ complex / cross-capability ──▼
                                  Bounded Planner
                                           │
                                  Capability Registry
        ┌───────────────────────┬──────────┼─────────────────────────┐
        ▼                       ▼          ▼                         ▼
  Governed Query Subgraph   Report     Modeling                 Investigation
  现有 SQL 安全链整体复用    Service     Service                   Service
        │                       │          │                         │
        └───────────────────────┴──────────┴─────────────────────────┘
                                           │
                                  Observation + Artifacts
                                           │
                         Result Quality / Replan-at-most-once
                                           │
                         Faithfulness + Confidence + Evidence
                                           │
                                      Final Response
```

### 3.1 两条执行路径

#### 快速路径

以下请求不进入 Planner：

- 单一、明确的指标查询；
- 明确的通用能力，如当前时间、系统支持范围、当前数据源；
- 普通闲聊；
- 查看已有报告、任务或模型状态；
- 纯粹的帮助说明。

快速路径直接执行已有节点或单一能力，避免增加延迟、Token 和新的错误面。

#### 规划路径

满足任一条件时进入 Planner：

- 一句话包含多个有依赖的问题；
- 需要查询后根据结果决定下一步；
- 同时涉及问析、模型、报告或调查；
- 用户要求“为什么”“根因”“继续下钻”“形成报告”等多步目标；
- 数据质量、时间覆盖或指标口径可能影响后续策略；
- 需要在多个候选能力间做选择。

进入 Planner 不代表允许任意循环。Planner 只能选择注册能力，并受到调用次数、数据库执行次数、重规划次数、Token、时延和副作用策略限制。

## 4. 能力模型

### 4.1 API、能力与领域服务的区别

```text
前端 API ─────────┐
飞书/钉钉入口 ────┼──→ Domain Service
定时调度器 ───────┤       ↑
Planner Capability┘       │
```

- API 是外部入口和鉴权契约。
- Capability 是 Planner 可见的受控业务契约。
- Domain Service 是真实业务实现。
- Planner 不通过 HTTP 调用本服务自己的 API，而是在同一进程/任务系统内调用领域能力适配层。

这样网页、机器人、定时任务和 Agent 复用同一套权限、幂等、审计与状态机，不会产生四套逻辑。

### 4.2 统一能力描述

每项能力注册以下元数据：

```json
{
  "name": "analysis.query_data",
  "version": "1.0",
  "description": "按企业语义和只读策略执行一次受治理数据分析",
  "inputSchema": {},
  "outputSchema": {},
  "effectLevel": "read_only",
  "allowedRoles": ["business", "admin"],
  "requiredScopes": ["analysis:read"],
  "timeoutSeconds": 30,
  "retryPolicy": "internal_only",
  "approvalPolicy": "none",
  "costClass": "database_and_llm",
  "idempotent": true
}
```

`effectLevel` 固定为：

- `pure`：不访问外部状态；
- `read_only`：只读查询；
- `create_draft`：创建草稿或任务提案，但不发布；
- `long_running`：启动训练、全新报告生成等长任务；
- `external_effect`：发布、订阅、发送消息等外部副作用；
- `governance`：指标、模型、权限等治理变更。

### 4.3 Planner 可自主调用的能力

| 能力 | 作用 | 内部实现 |
| --- | --- | --- |
| `system.get_time` | 当前时间、日期、星期、自然时间换算 | 确定性代码 |
| `system.describe_capabilities` | 说明支持的数据源、分析、报告、模型能力 | 能力目录快照 |
| `scope.get_current` | 当前账号、租户、角色、数据源和只读范围 | 鉴权上下文 |
| `task.get_status` | 查看分析、报告、建模任务状态 | 领域查询 |
| `analysis.inspect_context` | 检索指标、口径、Schema、字段、业务规则 | 现有 retrieval/knowledge/metadata |
| `analysis.query_data` | 完整受治理问析 | 现有 SQL 子图整体封装 |
| `analysis.inspect_result_quality` | 检查样本量、时间覆盖、缺失、比较完整性 | 确定性规则为主 |
| `analysis.build_visualization` | 根据结果选择并生成图表规格 | 现有 Artifact/图表逻辑 |
| `investigation.continue` | 基于已有 Checkpoint 调整条件并建立对比 | Analysis Branch/Run |
| `modeling.find_reusable` | 查找同数据源、任务类型和语义相符的已发布模型 | modeling service |
| `modeling.propose` | 形成建模草案、目标、特征、算法和取数 SQL | 现有 `propose_modeling` |
| `modeling.predict` | 使用有权限的已发布模型推理 | modeling service |
| `report.compose_from_evidence` | 把当前已校验 Artifact 整理为报告草稿 | report service |
| `report.start_fresh` | 按模板创建新的异步报告分析任务 | report service + Analysis Run |

### 4.4 必须确认或命中既定策略的能力

| 能力 | 默认策略 |
| --- | --- |
| `modeling.start_training` | 用户确认建模草案；记录取数 SQL、特征、算法和成本预算 |
| `modeling.retrain` | 用户确认，或已审核的重训策略触发 |
| `report.publish` | 人工审核通过，或已审核订阅的自动发布策略通过 |
| `report.send` | 用户明确要求，或订阅中已授权接收人和渠道 |
| `report.subscribe` | 用户明确确认周期、模板、数据源、接收人和渠道 |
| `investigation.create_action` | 用户确认负责人、截止时间与内容 |

确认使用 LangGraph `interrupt()` 与现有 `analysis_interaction` 持久化。恢复后重新校验权限、能力版本和目标资源状态，不能只相信暂停前的结果。

### 4.5 不注册为 Planner 能力的内部操作

- 原始 `execute_sql`；
- 单独的 `generate_sql` 或 `validate_sql`；
- 切换租户、越权选择数据源；
- 修改数据库连接与凭据；
- 元数据扫描、全量语义索引、索引重建；
- 指标口径直接发布；
- 模型治理直接发布/下线；
- 飞书、钉钉、邮件的底层 HTTP 请求；
- 任意 Shell、任意 Python 或任意网络请求。

这些操作由受控子图、管理员流程、Worker 或基础设施调用，避免模型绕过业务边界。

## 5. Planner 设计

### 5.1 结构化计划契约

Planner 只能输出符合 Schema 的计划：

```json
{
  "goal": "分析本周良率下降原因并形成质量周报草稿",
  "assumptions": [],
  "steps": [
    {
      "id": "step-1",
      "capability": "analysis.query_data",
      "purpose": "确认良率趋势和异常时间",
      "arguments": {"question": "..."},
      "dependsOn": [],
      "successCriteria": ["返回时间序列", "披露指标口径"],
      "onFailure": "clarify_or_stop"
    },
    {
      "id": "step-2",
      "capability": "analysis.inspect_result_quality",
      "purpose": "判断是否具备下钻条件",
      "arguments": {"artifactRef": "${step-1.result}"},
      "dependsOn": ["step-1"],
      "successCriteria": ["给出结构化质量结论"],
      "onFailure": "stop_with_limitations"
    },
    {
      "id": "step-3",
      "capability": "report.compose_from_evidence",
      "purpose": "形成报告草稿",
      "arguments": {"artifactRefs": ["${step-1.result}"]},
      "dependsOn": ["step-1", "step-2"],
      "successCriteria": ["草稿引用全部关键数值证据"],
      "onFailure": "return_analysis_only"
    }
  ],
  "stopConditions": ["目标已满足", "需要用户澄清", "预算耗尽"],
  "budget": {"maxCapabilityCalls": 8, "maxDatabaseExecutions": 3, "maxReplans": 1}
}
```

计划在执行前校验：能力是否存在、角色和 Scope 是否允许、依赖是否为 DAG、参数引用是否合法、预算是否超限、副作用是否具有确认策略。非法计划不执行，由确定性代码降级到现有固定路径或要求澄清。

### 5.2 Planner 不生成隐藏思维链

系统保存和展示的是业务可审计理由：

- 当前目标；
- 选择了哪项能力；
- 该能力要验证什么；
- 收到什么结构化观察；
- 为什么继续、调整、澄清或结束。

不要求、不保存、不展示模型的自由形式 Chain-of-Thought。证据链展示的是状态、工具输入摘要、策略判定和产物引用。

### 5.3 结构化 Observation

能力不得把大段日志或完整结果塞回 Planner。统一返回：

```json
{
  "status": "succeeded",
  "summary": "7 天数据完整，7 月 23 日良率显著下降",
  "artifactRefs": ["art_xxx"],
  "quality": {
    "coverage": 1.0,
    "freshness": "2026-08-28T23:59:59+08:00",
    "sampleCount": 8022,
    "limitations": []
  },
  "reasonCode": "OK",
  "suggestedNext": ["drill_down_by_line", "compose_report"],
  "usage": {"databaseExecutions": 1, "llmCalls": 2}
}
```

完整表格、SQL、模型结果和图表仍保存在 Artifact 中，Planner 只拿摘要和引用。

### 5.4 重新规划

只允许结构化、有限重规划。触发条件包括：

- 比较周期缺失；
- 请求维度没有可用字段；
- 样本量不足；
- 数据时间覆盖不完整；
- 查询成功但不足以回答“为什么”；
- 可复用模型不满足新数据范围；
- 能力返回可恢复错误。

普通问题默认最多重规划一次。安全错误、权限错误、租户或数据源越界、预算耗尽、相同失败指纹重复出现时禁止重规划。

## 6. `analysis.query_data` 受治理子图

这是保护准确率的核心。Planner 只能调用整体能力，不能看到或跳过内部节点：

```text
freeze tenant/user/source scope
  → retrieve memory（弱证据）
  → retrieve skill（分析程序建议）
  → retrieve semantic context
  → resolve published metric / temporary metric disclosure
  → generate SQL
  → read-only parser + table allowlist
  → dialect validation
  → task constraints（时间、维度、指标、图表）
  → EXPLAIN cost guard
  → execute with timeout and row limit
  → selective retry / duplicate fingerprint stop
  → completeness and result quality
  → explanation + confidence + evidence artifact
```

以下优先级不可被 Planner 或记忆覆盖：

```text
权限和租户边界
> 企业已发布指标口径
> 已确认数据源物理绑定
> 临时推导口径
> 用户偏好和历史经验
```

临时推导允许执行，但必须降低置信度并披露定义、计算公式和未发布状态。

## 7. 报告能力最终设计

### 7.1 当前真实链路

当前手动创建报告已经执行：

```text
POST /api/v1/reports
  → reports.service.create_report
  → AnalysisSession + AnalysisRun
  → LangGraph 多任务问析
  → ReportSection / Artifact
  → 结构化报告校验
  → 审核或订阅策略自动发布
  → Outbox
  → 站内消息、PDF、飞书、钉钉、邮件
```

定时订阅由 Scheduler 创建 Analysis Run，报告内容仍由问析 Agent 产生；触发时间、发布策略和接收人不由 Agent 临时决定。这个边界保留。

### 7.2 两种报告能力

#### `report.compose_from_evidence`

用于“把刚才的分析整理成报告”。输入为当前 Run 已校验的 Artifact、模板、标题和受众草稿，不重新查询数据库。它解决外层 Planner 已经完成分析后，调用当前 `create_report()` 又启动一遍 Agent 导致重复查询和结论漂移的问题。

#### `report.start_fresh`

用于用户直接要求完整周报、定时订阅或专题报告。创建独立 Analysis Run，按模板完成所有章节；返回 `accepted + reportId + runId`，由 Worker 异步执行，不阻塞当前 Planner。

### 7.3 发布与发送

`compose` 和 `start_fresh` 只生成草稿或异步任务。`publish`、`send` 必须：

- 通过 `weekly-report/v1` 或对应报告 Schema 校验；
- 所有数值有 Artifact 引用；
- 重新校验用户、租户、数据源和受众；
- 命中人工确认或已审核订阅策略；
- 幂等写入发布事件；
- 由投递 Worker 调用飞书、钉钉或邮件 Provider。

飞书/钉钉 Provider 是传输适配器，不是 Planner 工具。

## 8. 建模能力最终设计

### 8.1 当前正确边界

当前 Agent 的 `propose_modeling` 负责识别任务类型、目标字段、特征、候选算法、样本量、白名单取数 SQL和可复用模型。确认后由 `/api/v1/modeling/tasks` 与 modeling service 执行训练、评估、治理和推理。

这个边界正确：LLM 理解“为什么建模、用哪些业务数据”，AutoML 引擎确定性完成“如何防泄漏地训练和评估”。不把 sklearn 训练循环写进 Planner。

### 8.2 Planner 的建模决策

Planner 的顺序固定为：

```text
理解预测/分类/聚类/异常检测目标
  → find_reusable
  → 能复用则建议 predict
  → 不能复用则 propose
  → 用户确认训练草案
  → start_training 异步任务
  → evaluation / governance
```

模型训练、重训、治理发布属于高成本或治理操作，必须确认或命中管理员审核策略。模型推理只有在模型已发布、特征契约匹配、数据源和租户一致时可以由 Planner 自主调用。

## 9. 通用能力与混合请求

### 9.1 应支持的通用能力

- 当前时间、日期、星期、月份边界和自然时间换算；
- 当前用户、角色、租户、数据源和权限范围；
- 当前连接状态、最近扫描和数据新鲜度；
- 支持的数据库、图表、报告和模型能力；
- 分析、报告、建模任务状态；
- 基于当前数据源生成可用示例问题。

天气、新闻、股票、娱乐和通用网页搜索不属于制造业数据问析核心，不纳入默认能力目录。

### 9.2 多片段路由

请求先按语义片段分类为：

```text
conversation | utility | analysis | report | modeling | command
```

例如：

> “现在几点？顺便分析本周良率为什么下降。”

系统并行/顺序执行 `system.get_time` 和分析路径，再合并回答。

> “现在几点？如果已经下班就生成今天的生产日报。”

两个能力存在条件依赖，进入 Planner；生成草稿可以自动执行，发布/发送需要确认或订阅授权。

## 10. 调查、记忆与越用越好用机制

### 10.1 调查

调查不是新的 SQL 引擎，而是对 Analysis Session、Checkpoint、Branch、Artifact 和条件变更的产品封装。Planner 可提出：

- 调整时间范围；
- 更换分组维度；
- 对异常值下钻；
- 建立 A/B 条件对比；
- 把当前结果形成报告草稿。

每次继续调查必须继承租户、用户、数据源和已确认指标口径；改变数据源或越权维度时重新授权。

### 10.2 记忆与经验

记忆只用于：

- 用户图表、粒度、时间和表达偏好；
- 已确认的调查经验；
- 历史失败模式和经过验证的解决方式；
- 相关旧调查的引用。

记忆不能授予工具、改变权限、覆盖已发布指标、放宽 SQL 安全或自动批准副作用。Planner 召回记忆后仍需能力参数校验；候选记忆只有达到证据和反馈门槛后才能成为活跃经验。

“越用越好用”主要影响能力排序、检索排序、澄清方式和图表偏好，不改变硬安全路径。

## 11. 安全、权限与多租户

### 11.1 Scope 冻结

每个 Run 创建时冻结：

```json
{
  "tenantId": "tenant_x",
  "userId": "user_x",
  "role": "business",
  "sourceIds": [1],
  "dimensionScope": {},
  "capabilityScopes": ["analysis:read", "report:draft"]
}
```

所有能力调用从 Run Scope 取值，Planner 输出的 `tenantId/userId/sourceId` 不作为授权依据。异步恢复、外部机器人、报告发布和模型推理再次校验当前权限。

### 11.2 外部入口一致性

飞书和钉钉只负责身份绑定、消息幂等、会话映射和结果投递。绑定后进入与网页相同的 Session/Run、Router、Planner、Capability Registry、记忆、经验、权限与证据链，不建立“机器人简化版 Agent”。

### 11.3 副作用分级

| 操作 | Agent 可否直接执行 |
| --- | --- |
| 只读数据查询 | 可以，受 SQL 子图约束 |
| 生成图表或草稿 | 可以 |
| 创建异步报告草稿 | 可以 |
| 使用已发布模型推理 | 可以，需契约匹配 |
| 启动训练 | 需要确认 |
| 发布指标/模型 | 管理员治理流程 |
| 发布或发送报告 | 需要确认或订阅策略 |
| 修改连接、权限、租户 | 禁止 |

## 12. 异步、幂等、恢复与预算

### 12.1 长任务

报告完整生成、模型训练、模型重训不在 Agent Worker 的同步节点中长期占用。能力返回：

```json
{
  "status": "accepted",
  "jobId": "job_x",
  "runId": "run_x",
  "resumePolicy": "notify_on_terminal"
}
```

当前对话可结束并提示任务已创建；完成后通过站内消息和用户授权渠道通知。若业务必须等待，则使用持久化 interaction/checkpoint 恢复，而不是 FastAPI BackgroundTasks 或内存 Future。

### 12.2 幂等键

能力调用幂等键包含：

```text
run_id + plan_revision + step_id + capability_version + normalized_arguments_hash
```

外部发送再加入目标用户、渠道和报告版本。进程重启、SSE 重连、飞书/钉钉事件重复投递不得重复训练、重复发布或重复发送。

### 12.3 预算

预算由服务端策略生成，Planner 只能在预算内选择：

- 最大能力调用数；
- 最大数据库执行数；
- 最大重规划数；
- 最大 SQL 重试数；
- 最大 LLM Token；
- 最大同步时长；
- 最大结果行数；
- 最大异步任务数。

预算耗尽后必须返回已有证据、未完成目标和限制原因，不得静默继续。

## 13. LangGraph 状态与节点

### 13.1 新增状态

在现有 `AnalysisState` 上增加：

```python
plan: dict | None
plan_revision: int
active_plan_step_id: str | None
capability_calls: list[dict]
observations: list[dict]
replan_count: int
capability_budget: dict
pending_approval: dict | None
finalization_reason: str | None
```

### 13.2 最终主图

```text
START
  → route_and_segment
      ├─ conversation_response
      ├─ utility_fast_path
      ├─ simple_analysis_fast_path（复用当前固定问析）
      └─ build_plan
             → validate_plan
             → select_next_step
             → authorize_capability
                 ├─ request_approval → interrupt → resume
                 └─ invoke_capability
                         → record_observation
                         → evaluate_progress
                             ├─ select_next_step
                             ├─ replan（最多一次）
                             ├─ clarify → interrupt → resume
                             └─ finalize
```

`analysis.query_data` 自身仍是一个编译后的受控子图，或者由当前节点链以子图方式挂载。不要把其内部节点放进通用 Capability Registry。

### 13.3 降级策略

发生以下情况时退回现有固定路径：

- Planner 输出无法通过 Schema；
- 计划引用不存在能力；
- 单一明确查询无需规划；
- Planner 服务超时；
- 功能开关关闭；
- A/B 策略分配到基线组。

降级必须生成事件 `planner.degraded` 并记录 reason code，不向用户展示技术异常堆栈。

## 14. 领域接口建议

### 14.1 能力执行接口

```python
class Capability(Protocol):
    descriptor: CapabilityDescriptor

    def authorize(self, context: CapabilityContext, arguments: dict) -> AuthorizationDecision: ...
    def execute(self, context: CapabilityContext, arguments: dict) -> CapabilityResult: ...
```

`CapabilityContext` 只由服务端构造，包含 Run、用户、租户、数据源、预算、计划版本和幂等键。

### 14.2 推荐模块边界

```text
backend/app/agent/
  planner.py                 # 结构化计划生成
  plan_schema.py             # Plan / Step / Observation Schema
  capability_registry.py     # 能力目录与版本
  capability_executor.py     # 鉴权、预算、幂等、审计
  progress.py                # 完成度、停止和重规划判定
  capabilities/
    system.py
    analysis.py
    report.py
    modeling.py
    investigation.py

backend/app/agent/graph.py   # 主图与当前 query 子图接入
backend/app/reports/         # 报告领域实现保持独立
backend/app/modeling/        # 建模领域实现保持独立
backend/app/gateway/         # SQL 安全与执行保持独立
```

现有 API 只改为调用相同领域服务，不需要改造成“所有请求先经过 Agent”。

## 15. 事件协议与前端展示

### 15.1 新增事件

```text
planner.started
plan.created
plan.validated
plan.degraded
capability.started
capability.completed
capability.failed
observation.recorded
plan.revised
approval.requested
approval.resolved
planner.completed
```

事件只保存业务理由、输入摘要、输出摘要、Artifact 引用、预算和状态，不保存隐藏思维链。

### 15.2 业务端展示

默认收起为：

```text
调查计划 · 4 步 · 已完成 3 步
```

展开后显示：

```text
1 读取业务口径和数据范围        完成
2 查询良率趋势                  完成
3 数据覆盖不足，改为按产线下钻   已调整
4 生成质量周报草稿              进行中
```

点击步骤后，右侧任务控制台显示：

- 调用的业务能力，而不是 Python 函数名；
- 为什么调用；
- 使用的数据源、指标版本和时间范围；
- 结构化观察和限制；
- SQL、结果表、图表和报告 Artifact；
- Token、数据库执行次数、耗时和预算；
- 是否经过确认、重试或重规划。

用户不需要看到 `retrieve_context`、`prepare_retry` 等内部节点，管理员/调试视图可以查看完整 Trace DAG。

### 15.3 前端交互

- Planner 不是新的独立菜单，主要体现在智能问析和调查记录。
- 报告中心、建模中心继续作为领域工作台，显示异步任务、审核和治理。
- 确认卡片统一展示操作、影响范围、数据源、接收人/算法、成本和取消选项。
- 功能开关只放管理员实验设置，不暴露“第一版/第二版”等内部措辞。

## 16. 准确率与安全评估

### 16.1 A/B 基线

保留当前固定图作为基线，增加：

```text
AGENTIC_PLANNER_ENABLED
AGENTIC_PLANNER_ROLLOUT_PERCENT
AGENTIC_PLANNER_MAX_CAPABILITY_CALLS
AGENTIC_PLANNER_MAX_REPLANS
```

同一问题集运行基线与 Planner，比较结果而不是 SQL 字符串。

### 16.2 测试集

- 单一指标查询；
- 同义词和制造业术语；
- 模糊时间与对象；
- 一句话多个独立问题；
- 有依赖的多步骤问题；
- 追问与指代；
- 缺失指标和临时口径；
- 时间数据不完整；
- 跨表关联；
- 错误列名、方言差异与执行失败；
- 报告生成与证据复用；
- 模型复用、提案、确认与推理；
- 外部机器人重复消息；
- 提示注入、越权数据源和副作用诱导。

### 16.3 指标

| 维度 | 指标 |
| --- | --- |
| 结果 | 执行结果正确率、关键数值一致率 |
| 语义 | 指标口径正确率、时间/维度约束覆盖率 |
| 交互 | 应澄清正确率、拒答正确率、追问延续率 |
| Agent | 计划有效率、能力选择正确率、停止正确率、重规划有效率 |
| 可信 | 结论—结果忠实度、证据覆盖、置信度校准 |
| 安全 | 只读违规、越权、未确认副作用，目标必须为零 |
| 工程 | P50/P95 时延、Token、数据库执行数、失败恢复率 |

### 16.4 上线门槛

- 简单查询结果与现有固定图相比不得出现可重复的准确率回退；
- 安全违规和未确认副作用必须为零；
- 复杂多步问题的完整任务成功率应有可复现提升；
- 查询次数、Token 和 P95 时延在配置预算内；
- Planner 失败时固定路径可用；
- 网页、飞书和钉钉对同一账号、数据源和问题得到一致证据结果。

## 17. 完整改造工作清单

本文不把目标拆成“第一版、第二版”。完成状态以以下整体能力是否全部落地为准：

### 后端

- [x] Plan、Step、Observation、Capability Descriptor 契约；
- [x] 能力注册、版本、鉴权、预算、幂等与审计；
- [x] 快速路径和 Planner 路径判定；
- [x] 结构化 Planner 与计划校验；
- [x] 当前 SQL 链封装为不可拆跳的 `analysis.query_data`；
- [x] 结果质量检查和最多一次重规划；
- [x] 报告 `compose_from_evidence` 与 `start_fresh`；
- [x] 建模复用、提案、推理与训练确认边界（训练仍由人工确认后进入现有建模中心）；
- [x] 通用能力与多片段合并；
- [x] 长任务、interaction、checkpoint 和恢复；
- [x] 飞书/钉钉统一进入同一 Run 与能力系统；
- [x] 新事件与运行用量统计；
- [x] 基线降级、用户稳定分桶、运行快照与管理员灰度开关。

### 前端

- [x] 调查计划折叠组件；
- [x] 能力步骤、观察、重规划与确认状态；
- [x] 步骤详情与 Artifact 联动；
- [x] 报告复用当前证据入口；
- [x] 建模复用/提案/确认状态；
- [x] 业务视图与管理员完整 Trace 分层；
- [x] Planner 实验开关与对照指标管理页。

### 测试与材料

- [x] 固定图/Planner 双模式测试集；
- [x] 权限、副作用、未知能力与无效模型计划测试；
- [x] 异步重启、重复事件和幂等测试；
- [x] 报告、模型、飞书和钉钉既有回归测试；
- [ ] 使用制造业问题集形成准确率、成本、时延与复杂任务收益报告；
- [ ] 答辩架构图、演示脚本和失败降级演示。

## 18. 典型完整用例

用户：

> “分析本周良率为什么下降，看看有没有可复用模型预测下周风险，然后整理成质量周报发给我。”

最终执行应为：

```text
1 route_and_segment：识别为跨分析、建模和报告的复杂目标
2 build_plan：形成查询 → 质量检查 → 下钻 → 模型复用 → 报告草稿 → 发送确认
3 analysis.query_data：确认良率趋势
4 inspect_result_quality：数据完整，可继续下钻
5 analysis.query_data：按产线、产品、缺陷类型定位贡献因素
6 modeling.find_reusable：发现已发布且特征契约匹配的风险模型
7 modeling.predict：生成下周风险推理 Artifact
8 report.compose_from_evidence：复用上述 Artifact 形成周报草稿，不重复查询
9 approval.requested：展示报告版本、接收渠道和接收人
10 用户确认
11 report.publish + report.send：确定性校验、Outbox、投递 Worker
12 finalize：返回分析结论、模型版本、报告 ID、投递状态和完整证据链
```

如果没有可复用模型，步骤 6 返回 `NO_REUSABLE_MODEL`，Planner 调用 `modeling.propose`，向用户展示训练草案，但不得自动启动训练；报告仍可基于现有分析证据生成，并披露“尚未完成预测模型”。

## 19. 答辩表述

推荐统一表述：

> ForgeSight 不是把数据库交给一个自由循环的大模型，而是采用受限规划型 Agent。模型负责拆解业务目标、选择查询、质量检查、模型复用、报告等业务能力，并根据结构化结果有限调整计划；所有 SQL 都必须经过语义口径、只读白名单、方言、任务约束和成本门控子图。训练、发布和外部发送则通过确认或预先审核的策略执行。这样既保留 Agent 的自主规划和工具选择，也满足企业数据底座对准确、可审计、可恢复和安全的要求。

当评委问“为什么不是纯 ReAct”时：

> 数据问答的风险集中在错误口径、越权、写操作和无界查询。LangGraph 官方 SQL Agent和多个开源实现同样采用显式图或混合架构。我们把自主性放在业务决策，把安全红线放在不可绕过的代码边界，并用 A/B 测试证明复杂任务收益而不是主观宣称。

## 20. 最终锁定的架构决策

1. 不全面重写当前 Agent，保留现有 SQL 安全链和领域服务。
2. 不把底层 SQL 节点注册成 Planner 工具。
3. 复杂目标使用受限 Planner，简单目标使用快速路径。
4. Planner 只调用业务级能力，能力内部仍是确定性服务或受治理子图。
5. 周报生成是能力，发布和发送是受确认/策略控制的副作用。
6. 建模提案和已发布模型推理可由 Planner 调用，训练与治理必须确认。
7. 通用能力纳入轻量路由；只有存在依赖时才由 Planner 编排。
8. API、机器人、调度器和 Planner 复用同一领域服务，不让 Agent 通过 HTTP 调用自己。
9. 记忆、Skill 和经验只能影响排序与建议，不能改变权限、指标和安全边界。
10. 前端展示业务计划、能力调用、观察、调整和证据，不展示隐藏思维链。
11. 所有长任务、确认和外部副作用必须持久化、幂等、可恢复。
12. 保留固定图基线、功能开关和灰度策略，以准确率和复杂任务成功率决定启用范围。

## 参考资料

[1] LangChain, [Build a custom SQL agent](https://docs.langchain.com/oss/python/langgraph/sql-agent).  
[2] DataHub Analytics Agent, [Agent graph](https://github.com/datahub-project/analytics-agent/blob/466e8ab3db967450db9578c7f649925f96e3de8c/backend/src/analytics_agent/agent/graph.py).  
[3] DataHub Analytics Agent, [SQLAlchemy engine](https://github.com/datahub-project/analytics-agent/blob/466e8ab3db967450db9578c7f649925f96e3de8c/backend/src/analytics_agent/engines/sqlalchemy/engine.py).  
[4] eosho/langchain_data_agent, [SQL graph](https://github.com/eosho/langchain_data_agent/blob/9081abf1f118a18b9152b5966ce1c65c7a704baf/src/data_agent/graph.py).  
[5] eosho/langchain_data_agent, [SQL validator](https://github.com/eosho/langchain_data_agent/blob/9081abf1f118a18b9152b5966ce1c65c7a704baf/src/data_agent/validators/sql_validator.py).  
[6] shreyabaid007/text-to-sql-agent, [LangGraph chat graph](https://github.com/shreyabaid007/text-to-sql-agent/blob/f80a3eaf09371b1f26dcb1fdd66cb6417ed7c5a0/src/app/graphs/chat/graph.py).  
[7] shreyabaid007/text-to-sql-agent, [Read-only and human-review DB tool](https://github.com/shreyabaid007/text-to-sql-agent/blob/f80a3eaf09371b1f26dcb1fdd66cb6417ed7c5a0/src/app/tools/db.py).  
[8] ChetanVK10/DataAgent-Pro, [Supervisor graph](https://github.com/ChetanVK10/DataAgent-Pro/blob/68e09bf191f14651e8d49611f8afd87a5de7296b/backend/agents/graph.py).  
[9] ChetanVK10/DataAgent-Pro, [Supervisor node](https://github.com/ChetanVK10/DataAgent-Pro/blob/68e09bf191f14651e8d49611f8afd87a5de7296b/backend/agents/nodes/supervisor.py).  
[10] ORQ LangGraph Demo, [Assistant graph](https://github.com/orq-ai/orq-langgraph-demo/blob/de1ebc46ac1f00a19d5ecc9f1454b8de2bc41d43/src/assistant/graph.py).  
[11] ORQ LangGraph Demo, [Parameterized SQL tools](https://github.com/orq-ai/orq-langgraph-demo/blob/de1ebc46ac1f00a19d5ecc9f1454b8de2bc41d43/src/assistant/sql_tools.py).  
[12] DataGallery Lab, [DataFoundry](https://github.com/datagallery-lab/datafoundry).  
[13] LangChain, [Workflows and agents](https://docs.langchain.com/oss/python/langgraph/workflows-agents).  

## 附录：与现有文档的关系

- `PHASE5_AGENT_ANALYSIS_DESIGN.md` 继续作为现有会话、事件、持久化、Checkpoint、Trace 与 API 的详细历史设计依据。
- 本文是跨问析、报告、建模、调查和通用能力的最终目标架构；发生冲突时，后续 Agent 化改造以本文锁定的能力边界为准。
- 报告结构、自动发布、消息和投递仍分别遵守 `docs/phase-6-reports/`、`docs/contracts/` 和 `docs/integrations/` 的领域契约。
- 记忆与经验仍遵守 `docs/competition/agent-memory-research/` 中的来源、隔离、候选、纠正和遗忘规则。
