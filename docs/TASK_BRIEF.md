# A07 企业数据底座智能问析 Agent 系统 —— 下一阶段任务文档

> 本文档依据当前仓库实际内容整理，作为团队下一阶段（数据源、业务调研与前端、AutoML 优化）的任务分工、排期与验收依据。
> 现状基线均来自对 `backend/`、`frontend-new/`、`database/`、`docs/` 的代码与文档核查。

---

## 0. 背景与目标

系统已完成建模引擎全链路（任务管理 → 训练 → 质量门禁 → 治理闭环，对应建模里程碑 M1–M8），当前已具备：

- 自然语言 → 建模任务的 Agent 链路（`app/agent/modeling.py`、`intent.py`）；
- 四类任务（回归 / 分类 / 聚类 / 异常）23 个 sklearn 算法，零新依赖；
- 强制质量门禁（时间切分、交叉验证、基线对比、目标泄漏检测）+ 治理豁免闭环；
- 数据源管理（登记 / 测试 / 扫描 / 语义索引，当前仅 mysql / postgresql）。

本阶段围绕 **三个并行任务** 展开：

| 编号 | 任务 | 一句话目标 |
|---|---|---|
| 任务一 | 数据源调研 + 多数据源接口 | 让不同数据库类型都能接入平台 |
| 任务二 | 业务深入调研 + 前端逻辑梳理 + 纯前端 Demo | 摸清业务功能与交互逻辑，产出可演示的纯前端 Demo |
| 任务三 | AutoML 模块优化 | 扩充稀少数据，测试并优化建模引擎 |

---

## 1. 任务一：数据源调研与多数据源接口开发

### 1.1 现状基线（已核实）

- **适配层**：`backend/app/gateway/adapters.py`
  - `SUPPORTED_TYPES = {mysql, postgresql}`；`build_url()` 仅处理这两类；
  - `_apply_readonly()` 已对两类强制只读 + 超时（MySQL: `SET SESSION TRANSACTION READ ONLY` + `MAX_EXECUTION_TIME`；PG: `READ ONLY` + `statement_timeout`）；
  - 引擎按数据源缓存，`drop_engine` 在连接失败时释放。
- **登记模型**：`DataSource.type` 注释为 `mysql / postgresql`；`create_source` 用 `SUPPORTED_TYPES` 校验。
- **元数据扫描**：`backend/app/metadata/scanner.py` 以 `information_schema` 为权威来源；业务主题前缀 `TOPIC_BY_PREFIX = {mes_/qms_/eqp_/inv_/dim_}`；PG 仅扫 `public` schema。
- **前端**：`frontend-new/src/pages/admin/ResourcesPage.jsx` 已预留 `sqlserver`、`oracle` 两个"适配器规划中"入口；`src/assets/db-icons/` 已含 mysql / oracle / postgresql / sqlserver 四款 logo。
- **已有 API**：`/api/datasources`（CRUD、test、scan、semantic-index、job retry）。

### 1.2 调研任务（制造业数据源盘点）

输出一份《制造业数据源调研报告》，覆盖以下维度并给出"是否值得接入"的结论：

1. **业务系统常见数据源类型**：ERP（用友 / 金蝶 / SAP / Oracle EBS）、MES、QMS、设备 SCADA/OPC UA、IoT 时序库、WMS、PLM；
2. **数据库产品形态**：
   - 关系型：MySQL、PostgreSQL、SQL Server、Oracle、达梦 / openGauss / OceanBase 等国产库；
   - 时序型：TDengine、InfluxDB、TimescaleDB；
   - 文件/接口型：CSV/Excel、REST API、消息队列（Kafka/MQTT）；
3. **每类的接入难点**：驱动可用性（是否要新增依赖）、只读与超时如何强制、information_schema 差异、类型映射差异（如 Oracle 无 `LIMIT`、达梦方言）、许可证；
4. **赛题适配度评估**：优先接入对演示与评分有增量价值的类型（如 SQL Server / Oracle 能体现"多源接入"能力，国产库能体现信创）。

### 1.3 开发任务（多数据源接口）

原则：**保持现有架构**（适配器按类型注册 + 统一只读保证 + 引擎缓存），不推翻重构。

1. **连接器抽象**：在 `adapters.py` 中把"URL 构造 / 只读注入 / 驱动选择"收敛为可扩展的 `SUPPORTED_TYPES` 注册表，每种类型一个 build 分支（当前模式已具备，按类型扩展即可）。
2. **新增类型（按调研结论优先级）**：
   - SQL Server（`pymssql` / `pyodbc`）与 Oracle（`python-oracledb`，注意 SQL 方言与 `LIMIT` 的差异）；
   - 国产库（达梦 / openGauss，若驱动可得）；
   - 时序库（TDengine / InfluxDB，评估以"只读表结构 + 采样查询"的降级方式接入）。
   - **红线**：任何新增类型都必须落实"连接级只读 + 语句超时"（沿用 `_apply_readonly` 模式），并在 `test_connection` 中验证只读生效。
3. **扫描器方言适配**：`scanner.py` 的 information_schema 查询需按类型走分支；SQL Server / Oracle 的元数据 SQL 与 MySQL/PG 不同，抽成 `scan_schema(engine, source_type)` 分发。
4. **类型映射**：建模取数 `data.py` 中引号规则目前按 `postgres => "`、其余 `` ` `` 分支，需同步覆盖新类型方言。
5. **前端**：`ResourcesPage.jsx` 把"规划中"入口改为可用类型（补充端口、驱动、连接串说明），`db-icons` 复用。
6. **测试**：每种新类型至少一个适配层单测（build_url / 只读注入 / test_connection 失败路径）；无真实库时用 mock。

### 1.4 交付物与验收

| 交付物 | 验收标准 |
|---|---|
| 《制造业数据源调研报告》 | 覆盖 1.2 维度，标注接入优先级与依据 |
| 多数据源适配扩展 | 新增类型可通过 API 登记、测试连接、扫描出表；前端可见且可操作 |
| 只读与超时保证 | 每个新类型 `test_connection` 含只读校验；适配层单测通过 |
| 全量回归 | `backend/.venv/Scripts/python -m pytest -q` 全绿 |

---

## 2. 任务二：业务深入调研 + 前端逻辑梳理 + 纯前端 Demo

### 2.1 现状基线（已核实）

- **前端工程**：`frontend-new/`，Vite 6 + React 19 + Tailwind v4 + Recharts v3 + Vitest；`src/api/*` 已接入真实后端（`_http.js` 直连 `http://127.0.0.1:8000`，`VITE_API_BASE` 可覆盖）。
- **页面清单**（`src/app/routeConfig.js` + `src/pages/`）：
  - 业务侧 `business/`：Overview（总览）、Analysis（智能问析）、Modeling（业务建模）、Reports（报告）、ReportDetail（报告详情）；
  - 管理侧 `admin/`：AdminOverview（管理总览）、Knowledge（知识管理）、Resources（数据资源/数据源）、ModelGovernance（模型治理）、Settings、Security。
- **已接真实后端**：ModelingPage（真实训练进度轮询 + 质量门禁红黄绿）、ModelGovernancePage（复核/发布/下线/豁免原因）。
- **注意**：`frontend-new/CLAUDE.md` 仍写着"纯前端 + mock、不调用后端"，与现状（真实 API）不一致；`frontend-new/docs/` 目录不存在但 CLAUDE.md 引用了其中的文档。这是本任务需顺带修正的文档债务。

### 2.2 调研任务（业务功能深入调研）

1. **已有业务功能盘点**：逐页列出现有功能点（如 Analysis 页的提问、任务、调整面板；Modeling 页的建任务/训练/评估/解释/代码；治理页的列表/发布/下线），形成《业务功能清单》，每项标注：入口、依赖的 API、状态（真实可用 / 待完善）。
2. **额外业务功能调研**：面向制造业业务人员与管理层，设计补充功能并评估可行性：
   - 指标口径管理（指标公式、发布、绑定）；报告订阅与周期推送（现有 reports 模块可扩展）；
   - 异常告警（设备停机/不良率阈值触达）；数据血缘 / 表关系可视化；
   - 知识库内容扩充（生产/质量/设备/库存四大主题的指标与口径）；
   - 权限与审计增强（当前已有 Security 页）。
3. **产出**：《业务功能清单（含扩展建议）》+ 每项标注"是否进入本阶段 Demo"。

### 2.3 前端交互逻辑梳理（明确"点按钮 → 出界面"）

要求产出**页面级交互地图**，作为答辩与产品交互演示（提交材料③）的底稿：

- 对每个页面，绘制 **按钮 → 触发动作 → 出现面板/跳转路由 → 数据来源 API** 的映射表；
- 示例（以 Modeling 页为准，其余页面按同样格式补全）：
  | 按钮/控件 | 点击结果 | 数据来源 |
  |---|---|---|
  | 描述预测需求 | 跳转智能问析页 | — |
  | 开始训练 | 展开"训练过程"面板，轮询 `/train/status` | `startModelTraining` |
  | 提交治理复核 | 调用 `/review`，列表刷新为"待复核" | `submitModelForReview` |
  | 治理页"批准发布" | 高风险需填豁免原因，调用 `/publish` | `governModel` |
- 交互地图需与实际代码逐一核对（反幻觉：不编造按钮与路由）。

### 2.4 拉取新前端 + 纯前端 Demo 开发

1. **拉取新前端**：确认 `frontend-new/` 与团队最新代码同步（git 分支/远端核对），作为后续开发的唯一前端基线。
2. **纯前端 Demo 模式**：目标 = **不依赖后端、现场可直接演示**。
   - 在 `src/api/_http.js` 或 API 层增加 **mock 开关**（环境变量如 `VITE_DEMO_MODE`），开关开启时 API 层返回内置演示数据（复用 `src/mocks/` 已有数据并补充建模/治理演示数据），页面零改动即可演示；
   - 覆盖至少一条完整演示链路：智能问析提问 → 建模任务 → 训练进度 → 质量门禁结果 → 提交复核 → 治理列表；
   - 产出《纯前端 Demo 演示脚本》：点哪几步、期望看到什么界面。
3. **文档债务修正**：更新 `frontend-new/CLAUDE.md` 与缺失的前端文档说明（或在仓库级文档中标注真实联调状态）。

### 2.5 交付物与验收

| 交付物 | 验收标准 |
|---|---|
| 《业务功能清单（含扩展建议）》 | 覆盖现有页面 + 扩展建议，标注优先级 |
| 页面级交互地图 | 逐页"按钮→界面→API"映射，与实际代码一致 |
| 纯前端 Demo 模式 | `VITE_DEMO_MODE=1` 时无需后端即可走通完整演示链路 |
| 前端回归 | `npm run test`、`npm run build` 通过 |

---

## 3. 任务三：AutoML 模块优化

### 3.1 现状基线（已核实）

- **算法注册表**：`app/modeling/algorithms.py`，23 算法 / 4 任务类型，`ALGO_INDEX` + `CANDIDATES_BY_TASK`；**超参为固定默认值，无调参环节**（如 `Ridge(alpha=1.0)`、`RandomForest(n_estimators=100)`）。
- **引擎**：`app/modeling/engine/sklearn_engine.py`，已具备预处理（数值中位数/类别众数+编码）、时间切分 / StratifiedKFold / KFold 交叉验证、按 CV 主指标选优、基线对比、目标泄漏检测、全量重拟合、分组评估、回归预测区间（split conformal）。
- **质量门禁**：`app/modeling/quality.py` + `service.py`，问题码、riskLevel、强制阻断与治理豁免已闭环。
- **数据现状（关键约束）**：业务库种子数据**非常稀少**（`database/mysql/002_seed_official_data.sql`）：
  | 表 | INSERT 条数 |
  |---|---:|
  | mes_process_output | 12 |
  | qms_defect_detail | 9 |
  | qms_inspection | 6 |
  | inv_inventory_snapshot | 5 |
  | mes_work_order / eqp_downtime_record | 各 2 |
  | dim_* 主数据 | 各 1 |

  稀少数据导致：CV 折叠数被 `min(5, len//20)` 压到 2、`SMALL_SAMPLE` 问题码、模型难以稳定通过门禁（演示中 `mes_process_output` 12 行仍能出绿色模型属特例）。

### 3.2 数据扩展（扩展现有稀少数据）

目标：让训练数据达到可支撑质量门禁的规模（每张分析用表至少数百行、跨多月），同时**保持业务口径与字段合法性**（不引入泄漏、不破坏时间顺序）。

1. **数据扩充脚本**：在 `database/` 增加可重复执行的**数据生成器**（Python 脚本，非手写 SQL），按现有 schema 的业务口径生成：
   - `mes_process_output`：多产品×多产线×多工序×多月（目标 ≥ 500 行，含良率/缺陷/输入输出字段的可信关系）；
   - `qms_inspection` / `qms_defect_detail`：与工艺产量一致的检验与缺陷明细；
   - `eqp_downtime_record`：停机时长、报警、原因分布；
   - `inv_inventory_snapshot`：按日快照 + 季节波动；
   - 保持 `stat_date` 等时间字段连续递增，维度间符合业务约束（如 `good_qty ≤ input_qty`）。
2. **生成规则可复现**：固定随机种子，脚本幂等（重复执行不重复插入），并附数据字典说明生成口径。
3. **评估"稀少数据"本身**：保留小样本场景的建模能力（当前门禁对 <200 样本降级，这是正确保守行为，不应为迁就演示而放松门禁）。

### 3.3 AutoML 测试与优化

1. **测试加固**：
   - 新增"数据充足（≥500 行/多类）"与"数据稀少（<200 行）"两套基准测试，断言：充足数据下回归/分类/异常均能通过门禁或给出合理 riskLevel；稀少数据下触发对应问题码且不崩溃；
   - 覆盖新增算法注册（`ALGO_INDEX` 增删不影响引擎与 API）；
   - 为引擎选优、CV 波动、预测区间补回归测试。
2. **训练质量优化**（按优先级）：
   - **受控调参**：对 `auto` 策略引入轻量随机搜索/网格搜索（仅针对必选算法的关键超参，限制候选数与耗时，保持零重型依赖），并纳入 CV 选优；
   - **小样本自适应**：按样本量自动缩默认候选集（如 <200 行自动偏好 ridge/svr/logistic，≥1000 行自动启用 gbr/forest）；
   - **类别不均衡**：分类任务候选集按不均衡程度切换策略（已有 `class_weight=balanced`，可补充 SMOTE 等价物前先验证必要性）；
   - **异常检测**：`contamination` 依据业务先验（如 5%）设定，避免 `auto` 抖动。
3. **性能与可用性**：
   - 记录并限制定时训练耗时/内存（`modeling_max_rows`、候选数、调参预算）；
   - 训练进度与租约恢复已持久化（现状 OK），确认调参不破坏状态机。
4. **口径一致性**：`codegen` 生成代码与引擎口径对齐（当前 codegen 与引擎训练路径存在差异项，属已知债务，本次一并校准）。

### 3.4 交付物与验收

| 交付物 | 验收标准 |
|---|---|
| 数据生成器 + 扩充后业务库 | 关键表 ≥500 行且时间连续、口径合法；脚本幂等可复现 |
| 数据字典 / 生成口径说明 | 字段含义与生成规则文档化 |
| AutoML 优化 | 充足数据下主指标较现状提升且不引入新门禁风险；稀少数据行为不变 |
| 测试与基准 | 双规模基准测试 + 回归全绿；记录训练耗时基准 |
| 全量回归 | `backend/.venv/Scripts/python -m pytest -q` 全绿 |

---

## 4. 风险与注意事项

1. **依赖红线**：新增数据源驱动需评估是否违反"零重型依赖"约束（现有 `requirements.txt` 仅 sklearn/pandas 等）；若必须新增驱动，只加**轻量官方驱动**（如 `python-oracledb`），并记录理由。
2. **只读底线**：任何新数据源类型都必须强制只读 + 超时，业务账号永不写库（沿用 `business_engine` 只读模型与审计）。
3. **安全**：`backend/.env` 含真实数据库密码与 DeepSeek Key，**绝不提交、绝不回显**；数据源密码为演示环境明文存储，上线前需加密（现状已知债务，本期至少不扩大风险）。
4. **技术栈锁定**：前端不引入 UI 库/状态库/新 CSS 方案；图表走 `components/charts/` 封装。
5. **门禁不可放松**：数据扩充是为了让建模在真实口径下更可靠，不是为了让模型"更容易通过"；SMALL_SAMPLE 等保守行为是特性。
6. **文档一致性**：任务二需修正 `frontend-new/CLAUDE.md` 与缺失 docs 的过时描述，避免后续开发被误导。

---

*本文档基线核查时间：2026-08-17；若代码继续演进，请在各任务验收前重新核对现状基线。*
