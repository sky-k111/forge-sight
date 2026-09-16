# ForgeSight 训练模型（机器学习建模）实现方案

版本：v1.1（2026-09-16）　状态：M1–M5 已全部实施并验证（后端 646 passed / 前端 174 passed / 浏览器实测）
实施摘要：两级授权（personal/published）与管理员免审直发（direct_publish 留痕、四眼开关）已上线；
KMeans k 由轮廓系数自动选择；聚类输出群画像；线性/逻辑回归给出系数归一化重要度；
每个模型版本冻结可复现建模脚本（评估面板可查看）；FLAML 作为 thorough 档超参搜索引擎
（仅取 best_config 重建标准 sklearn 估计器，失败静默回退网格）；预测留痕表
model_prediction_trace 支持实际值自动回填，偏差监控页按 10%/25% 阈值输出正常/观察/漂移。
依据：《03会议材料-2026赛题手册》A07 命题要求、当前代码库（分支 codex/business-dashboard-20260914）、
AutoML 实拉调研（`D:\aaa\a-外包\research\2026-09-15-automl-tabular\research_report.md`）

---

## 0. 结论先行

1. **赛题要求的建模能力，当前版本已经覆盖约 80%**：六类算法、训练、隔离评估、推理、解释、治理发布、Agent 提案闭环都已存在且经过测试。不需要推倒重来。
2. **真正的缺口有四个**（按价值排序）：
   - G1 Agent 回答中的"预测编排"不完整：有 `modeling.predict` 能力，但"问题命中预测意图 → 自动找模型/提案建模 → 回答附带预测与依据"的编排没有产品化闭环；
   - G2 超参搜索深度：`strategy=auto` 只是六候选各训一次比指标，没有超参优化（AutoML 层）；
   - G3 解释覆盖面：特征重要度只覆盖 tree/forest（`engine.py:677`），线性/逻辑回归没有系数解释；预测没有置信度表达；
   - G4 建模代码可见性：手册要求"支持生成简单建模代码、用户可查看"，目前可查看取数 SQL，但不可查看可复现的建模脚本。
3. **AutoML 选型结论：引入 FLAML 作为可选的 `thorough` 档搜索后端，不替换自建引擎**。auto-sklearn 因停更（2023-04）+ 仅 Linux 排除；mljar 备选；TPOT/H2O 排除。默认路径保持零新增依赖。

---

## 1. 需求基线（赛题手册 A07 原文映射）

| 手册要求 | 原文要点 | 当前状态 |
|---|---|---|
| 建模任务类型 | 线性回归、决策树、随机森林、逻辑回归、KMeans、Isolation Forest | ✅ `engine.py:49 ALGORITHMS` 六类齐全 |
| 训练/评估/推理/解释 | 支持模型训练、模型评估、简单推理和模型结果解释 | ✅ 训练`service.py:456`、评估`:364`、推理`:690`、解释`engine.py:674`；⚠️ 解释仅 tree/forest |
| 建模代码生成与查看 | 支持生成……简单建模代码；用户可查看生成的代码或查询语句 | ⚠️ 取数 SQL 可查看；建模脚本缺失（G4） |
| Agent 闭环 | 用户提问→智能执行→结果展示；回答中可做预测 | ⚠️ 能力齐、编排弱（G1） |
| 工程可用性 | 可运行、交互清晰、异常处理合理 | ✅ 建模六步向导 + 治理页 |

---

## 2. 当前版本认知（as-is 代码地图）

- **训练引擎** `backend/app/modeling/engine.py`（768 行）：泄漏安全切分（random/time/group，`:310-356`）、sklearn Pipeline（impute/one-hot/scale）、六算法估计器工厂（`:454`）、指标与交叉验证、特征重要度与业务解释文本（`:674-732`）。
- **任务服务** `backend/app/modeling/service.py`（967 行）：`create_task`（取数 SQL 先过只读网关校验，`:215`）→`diagnose`（数据体检）→`train_task`（`:456`，strategy auto/manual、candidates、optimizationLevel）→`_recommended_model`/`_assess_model_quality`（`:345/364`）→工件持久化（pickle+schema，`:318-329`）→治理复核/发布/版本（GovernedModel）→`predict`（`:690`，仅已发布模型）。
- **Agent 层**：`agent/capabilities/modeling.py` 四个能力（find_reusable / propose[delegated] / predict / start_training[always-approval]）；`agent/runner.py:101` 的 `propose_modeling` 计划节点与 `graph.py:287-430` 的建模意图识别（任务类型、目标字段、特征选择、派生目标）。
- **前端**：`ModelingPage` 六步向导（训练数据体检→隔离验证数据→搜索候选方案→评估与对比→确定业务阈值→生成业务解释）；`ModelGovernancePage` 复核/发布/版本切换。
- **不变量（必须保持）**：训练取数只走只读执行网关；未发布模型不得被 Agent 调用；训练需用户审批；工件与版本绑定租户；不向前端泄漏连接串与内部异常。

---

## 3. 总体设计

### 3.1 分层与数据流

```
自然语言问题
   │ 意图识别（graph.py 现有）
   ├─ 分析/查询类 → 现有 analysis 链路
   └─ 预测/建模类 ──► 预测编排器（新增，§3.2）
                        │
        ┌───────────────┼───────────────────────┐
        ▼               ▼                       ▼
  find_reusable    propose（草案+审批）     modeling.predict
  （已发布模型）    → start_training        （已发布模型推理）
        │               │                       │
        │               ▼                       ▼
        │        训练worker（异步）        预测卡（§3.3）
        │        fast/balanced/thorough    值+区间+主因+模型版本
        │               │
        └───────► 治理发布 ◄────────────────────┘
```

训练数据一律经只读网关（现状保持）；`thorough` 档在 worker 内调用 FLAML（§3.4）。

### 3.2 G1 预测编排器（Agent 回答内预测）

新增 `backend/app/agent/capabilities/prediction.py`（编排，不新增模型逻辑）：

1. 意图为预测类（"预测/预计/下个月/趋势外推/会不会"）时先 `find_reusable_models`；
2. 命中已发布模型：校验契约（特征可得性）→ `predict` → 产出**预测卡**并入回答证据链（`capability-result` 事件，runner 已有事件类型可复用）；
3. 未命中：生成建模提案（现有 propose 子图）→ 审批 → 异步训练 → 本轮回答如实说明"已受理建模任务，发布后可预测"，**不伪造预测**；训练完成通过现有消息/事件通道通知，后续同类问题自动走第 2 步；
4. 契约不匹配（特征缺失/目标不一致）时降级为分析回答并说明原因。

验收：问题"预测下月各产线产量"在无模型时产生提案+审批事件；在有已发布模型时回答含预测卡且证据链可追溯到模型版本与训练评估。

### 3.3 G3 预测卡与解释增强

- 预测卡字段：预测值、**置信表达**（分类=概率+决策阈值；回归=测试集残差分位数区间）、top-3 主因（重要度）、模型版本与评估指标摘要、数据快照范围；
- 解释补齐：线性/逻辑回归增加**标准化系数解释**（engine 新增 `_linear_importance`，与现有 `extract_importance` 合并出口）；`thorough` 档模型可选 SHAP（`shap` 为可选依赖，导入失败回退 permutation importance）；
- 前端：Agent 回答区新增预测卡组件；ModelingPage 评估面板展示系数/SHAP 图。

### 3.4 G2 FLAML 接入（thorough 档）

- 依赖策略：`pip install flaml[automl]` 为**可选依赖**；`requirements.txt` 不硬加，代码 `try: from flaml import AutoML except ImportError: FLAML_AVAILABLE=False`；
- `train_task(strategy, optimization_level)` 映射：
  - `fast`（默认）：现状六候选单训比指标，零新增依赖；
  - `balanced`：现状 + 交叉验证复评（已有原语）；
  - `thorough`：FLAML `AutoML.fit(time_budget=映射表{小样本120s/中300s/大600s}, estimator_list=白名单子集)`；搜索结束取 `automl.model` 走现有工件/治理流程；
- **白名单约束**：`estimator_list` 只允许映射到赛题六算法家族的估计器（rf/extra_tree→随机森林族、logreg→逻辑回归、线性→linear；lgbm/xgboost 仅在用户显式开启"扩展估计器"开关时允许，并在模型卡片标注"超出赛题基线算法"）；
- 搜索轨迹落库：trial 数、最优配置、耗时写入 `modeling_evaluation`（新列或 JSON 字段），前端评估面板可视化；
- 失败回退：FLAML 超时/异常/未安装 → 自动回退 balanced 并在评估中标注原因；
- Windows 兼容：FLAML 自带 Windows CI（调研证据），lightgbm/xgboost 有 Windows wheel。

### 3.5 G4 建模脚本可见性

- 由 task + 最优配置确定性生成可复现 Python 脚本（sklearn 代码文本：取数 SQL 注释头 + Pipeline 构造 + 切分 + 指标），存工件旁路 `script_text`；
- ModelingPage 与报告/回答证据中提供"查看建模代码"（只读代码块，与现有"查看 SQL"同交互）；
- 脚本与模型版本绑定，发布后不可变。

### 3.6 模型复用与存储生命周期

- **复用语义**：发布即租户资产。跨人复用经 `find_reusable_models` 语义契约匹配（同数据源＋目标/特征语义重合度），跨期复用无需重训（模型学规律而非某期数据）；工件内捆 schema，新特征未登记时拒绝推理并提示重训。
- **寿命管理**：新版本发布→旧版本自动 retired（现状保持）；"能用≠还准"，由 P3b 偏差监控回答准不准。
- **存储保留策略（新增）**：每任务保留最近 N 个版本（默认 3）＋当前发布版；retired 超过宽限期（默认 90 天）的工件文件物理清除；数据库永久保留元数据、评估与 sha256 指纹（审计与回溯不受影响）；按租户设存储配额与告警。磁盘上界＝任务数 × N × 单工件大小（sklearn 工件为 KB–MB 量级）。

### 3.7 业务呈现层与角色分工（产品视图）

**定位：用户侧不暴露"模型"，只暴露"带依据的预测"；建模是对话的副产品，治理是管理员的职责。**

暴露梯度（按角色切分）：

| 层 | 内容 | 可见角色 |
|---|---|---|
| L0 回答层 | 预测值 + 一句话依据（含模型版本） | 全部业务用户 |
| L1 依据层 | 预测卡：置信范围、top-3 主因、数据范围、模型版本、查看评估 | 业务用户（只读） |
| L2 过程层 | 建模提案、训练进度、评估摘要、建模代码 | 发起人 + 管理员 |
| L3 治理层 | 模型档案、复核发布、版本切换/回滚、阈值、停用、偏差监控 | 仅管理员 |

措辞去技术化：用户侧只用"预测/依据/置信/主要影响因素"；算法名与指标名仅出现在管理员侧与建模代码视图。置信不足时明示"数据不足，暂不预测"，与周报"不强行给结论"的产品性格一致；预测携带冻结依据（模型版本+数据快照）。

用户端三条路径：

1. **对话式（主路径）**：预测类问题命中已发布模型→回答附预测卡；未命中→Agent 提案"建立该预测能力"→用户确认（审批）→异步训练→完成通知"已学会 XX 预测"→后续同类问题直接预测。用户心智为"Agent 学会新技能"，建模步骤全部后台化。
2. **看板式**：预测型组件仅取已发布模型，带"预测"角标与实测组件区分，hover 显示模型版本。
3. **主动式（进阶）**：建模六步向导保留为高级入口，普通用户无需感知。

管理员端从"管能不能训"升级为"管还准不准"：

- 模型档案：状态、版本历史、质量分、调用次数、最近预测时间；复核队列发布后才开放 Agent（不变量保持）；
- **偏差监控（新增）**：预测值与实际值回填对比→偏差趋势→超阈漂移告警→一键重训练提案；
- 阈值调整必填原因、停用即时生效并让 Agent 侧降级提示。

---

## 4. 实施阶段与验收

| 阶段 | 内容 | 验收标准 |
|---|---|---|
| P0 基线确认 | 回归现有建模全链路测试 | 现有测试全绿（后端 630+ / 前端 172+） |
| P1 解释补齐+脚本可见（G3/G4） | `_linear_importance`、script_text 生成与前端查看 | 单测：线性模型有系数解释；脚本可 `compile()`；前端代码块渲染 |
| P2 预测编排+预测卡（G1） | prediction 编排能力、回答证据链、预测卡组件 | 集成测试：有/无模型两条路径事件序列正确；无模型时不出现伪预测 |
| P3 FLAML thorough 档（G2） | 可选依赖接入、白名单、搜索轨迹、回退 | 单测（flaml 未装时回退）+ 集成测试（装时 thorough 产出 trial 记录）；治理不变量测试不回归 |
| P3b 预测偏差监控 | 预测留痕（输入快照+预测值+模型版本）、实际值回填对比、偏差趋势与漂移告警、重训练提案入口 | 回填后偏差报表可见；超阈产生告警事件；治理页可一键发起重训练 |
| P4（可选）时序外推 | 趋势类预测（时间特征回归 + 季节性基线对照） | 手册不强制；有时间再做 |

每阶段保持：训练取数只读网关、未发布不可推理、训练需审批、租户隔离四项不变量的回归测试。

---

## 5. 风险与取舍

1. **训练时长 vs 交互预期**：thorough 档限时搜索仍可能分钟级——保持异步 worker + 审批语义，回答中如实标注进度，不阻塞对话；
2. **小样本过拟合**：现有 `_assess_model_quality` 阈值门禁保持；FLAML 档额外记录 train/test 差距告警；
3. **依赖体积**：FLAML 可选安装，演示机预装、评测机缺失时自动回退，功能不残废；
4. **解释成本**：SHAP 仅 thorough 档按需计算并缓存入工件；
5. **赛题口径**：默认估计器白名单=六算法，扩展估计器显式开关+卡片标注，避免评审质疑超纲或不可解释。

---

## 7. 前后端完整改造清单（v2 实施版）

风格基线：沿用已确立的工作区设计语言——细边框面板、分区线、mono 数字、 slim 工具条、无装饰卡片；新组件一律 StatusPill/fact-strip/hairline 语汇。

### 7.1 后端改造

| # | 改造 | 落点 | 要点 |
|---|---|---|---|
| B1 | 授权两级制 | `modeling/service.py`、`contracts.py` | GovernedModel 增 `personal` 状态；准入=训练完成+质量门禁过+schema 有效；`predict` 授权改为「published OR (personal AND requester==owner)」；`find_reusable_models(viewer)` 带查看者；推理时重校验数据源访问权 |
| B2 | 管理员免审直发 | 同上 + `api/modeling.py` | 新转移 trained/personal→published，条件 requester=admin+门禁过；发布记录加 `direct_publish` 标记+操作人；审计动作"模型直发"；租户配置 `require_review_for_admin_publish`（默认关）留位 |
| B3 | 个人档寿命与配额 | service + 周期任务 | 个人档 30 天有效、每人活跃上限 3；重训即旧个人版作废；过期校验放在 predict/find 入口 |
| B4 | 正式工件硬拒绝 | `reports/service.py` | 周报/报告合成引用模型时校验 published，personal 一律拒绝（现有"已发布指标才能入正式报告"不变量的延伸） |
| B5 | 解释补齐 | `modeling/engine.py` | `_linear_importance`（标准化系数）并入 `extract_importance`；thorough 档可选 SHAP，失败回退 permutation importance |
| B6 | 建模脚本可见 | service + 工件元数据 | 由 task+最优配置确定性生成可复现 sklearn 脚本 `script_text`，随模型版本冻结；API 返回 |
| B7 | KMeans k 自动选 | engine | k=2..8 各训取轮廓系数最高（成本极低）；IsolationForest 保持 contamination=auto |
| B8 | FLAML thorough 升级 | 新 `modeling/automl_flaml.py` | 可选依赖；time_budget 映射+估计器白名单+trial 落库；未装/超时回退网格档 |
| B9 | 存储保留策略 | service + 周期任务 | 每任务保留最近 3 版+发布版；retired 超 90 天物理清除、元数据与指纹永留；租户配额告警 |
| B10 | 预测留痕与偏差监控 | 新表 `model_prediction_trace` + 回填任务 | 留痕：输入快照摘要、预测值、模型版本、请求人、scope；回填：按预测期经只读网关重查实际值；偏差趋势+超阈告警事件+重训练提案入口 |
| B11 | 预测编排 | `agent/capabilities/prediction.py` + runner | 预测/分组/异常意图→viewer 范围内找模型→predict→结果卡事件入证据链；无模型→propose 审批流，回答如实说明不伪造 |

### 7.2 前端改造

| # | 改造 | 落点 | 要点 |
|---|---|---|---|
| F1 | 结果卡组件族 | 新 `components/models/ResultCards.jsx` | PredictionCard（值+置信范围+top3 主因+模型版本）、GroupCard（归属群+群画像）、AnomalyCard（异常分+驱动字段）；scope 徽标：「个人模型 · 未经审核」amber vs「已发布 vN」teal；嵌入 AnalysisPage 证据链 |
| F2 | 建模页增强 | `ModelingPage.jsx` | 训练后显示个人档状态与"自用已开启"；"提交复核"按钮；管理员可见"直发（免审）"按钮+确认；评估面板加"查看建模代码"代码块与搜索轨迹（trial 数/最优配置） |
| F3 | 菜单结构调整 | `navigation.js`/`routeConfig.js` | 管理端「AI 与模型」下「模型治理」扩为子组：复核与发布（现页）、模型档案、偏差监控；业务端菜单不动，ModelingPage 内部加页签「建模任务 / 我的模型」 |
| F4 | 模型档案页 | 新 `ModelArchivePage.jsx` | 表格：状态/版本/质量分/调用次数/最近预测/直发徽标；版本切换与回滚、阈值修改（必填原因）、下线；fact-strip 汇总 |
| F5 | 偏差监控页 | 新 `ModelDriftPage.jsx` | 偏差趋势图（复用 CartesianChart）、告警列表、一键重训练提案；空状态紧凑 |
| F6 | 治理页联动 | `ModelGovernancePage.jsx` | 复核队列展示门禁摘要与直发徽标筛选 |

### 7.3 实施分期与验收

| 期 | 范围 | 验收 |
|---|---|---|
| M1 | B1-B4、B9 + F2、F3、F4、F6 | 两级授权/直发/保留策略单测+集成测；个人模型入周报被拒；非 owner 调个人模型 403；直发留痕可筛 |
| M2 | B5-B7 + F2 代码块 | 线性模型有系数解释；script_text 可 compile；k 自动选单测 |
| M3 | B11 + F1 | 有/无模型两路径事件序列正确；三类结果卡渲染；无模型不伪造预测 |
| M4 | B8 | flaml 未装回退单测；装时 thorough 产出 trial 记录 |
| M5 | B10 + F5 | 回填偏差报表可见；超阈告警；重训练入口 |

每期保持四项不变量回归：训练取数只读网关、未发布不对他人推理、个人模型不入正式工件、租户隔离+审计留痕。

## 8. 调研与证据索引

- 手册：`D:\aaa\a-外包\03会议材料-2026赛题手册_全文提取.txt`（A07 建模能力/任务清单/评分要点）
- AutoML 实拉调研：`D:\aaa\a-外包\research\2026-09-15-automl-tabular\research_report.md`（FLAML/auto-sklearn 克隆与代码引用；mljar 克隆失败记录）
- 现状代码：`backend/app/modeling/{engine,service,contracts}.py`、`backend/app/agent/capabilities/modeling.py`、`backend/app/agent/{runner,graph}.py`、`frontend-new/src/pages/business/ModelingPage.jsx`、`frontend-new/src/pages/admin/ModelGovernancePage.jsx`
