# A07 赛题要求与实现映射

本目录保留杭州自动化技术研究院【A07】“企业数据底座智能问析 Agent 系统”的原始赛题文件，并说明 ForgeSight 当前实现与赛题要求的对应关系。

- [赛题原文：企业数据底座.docx](企业数据底座.docx)

## 核心要求映射

| 赛题能力 | ForgeSight 实现 | 主要位置 |
|---|---|---|
| 业务知识管理 | 分析主题、业务对象、业务规则、指标口径及指标发布审核 | `backend/app/knowledge/`、`frontend-new/src/pages/admin/KnowledgePage.jsx` |
| 数据资源理解 | MySQL/PostgreSQL 数据源接入、元数据扫描、字段说明、样例值、表关系图 | `backend/app/metadata/`、`frontend-new/src/pages/admin/ResourcesPage.jsx` |
| 自然语言智能分析 | 意图识别、任务规划、澄清、SQL 分析、结果解释和流式事件展示 | `backend/app/agent/`、`frontend-new/src/pages/business/AnalysisPage.jsx` |
| SQL 生成与即席执行 | SQL 方言约束、只读校验、成本门控、超时/行数限制和查询执行 | `backend/app/gateway/` |
| 分析结果展示 | 表格、文字结论以及折线、柱状、环形、散点、组合、热力图 | `frontend-new/src/components/charts/` |
| 常见机器学习建模 | 线性回归、决策树、随机森林、逻辑回归、K-Means、Isolation Forest 的训练、评估、推理与治理 | `backend/app/modeling/`、`frontend-new/src/pages/business/ModelingPage.jsx` |
| 报告生成 | 模板评估、分章节生成、复核、审批、发布、飞书推送和 PDF 导出 | `backend/app/reports/`、`frontend-new/src/pages/business/ReportsPage.jsx` |
| 可运行系统原型 | FastAPI + React + MySQL 业务库 + PostgreSQL 平台库，可通过一键脚本启动 | `scripts/forge-sight.ps1`、`fs.bat`、`docker-compose.yml` |

## 数据底座

- MySQL `forge_sight`：生产工单、工序产量、质量检验、缺陷明细、设备停机、库存快照及四张维表；应用使用只读账号访问。
- PostgreSQL `forge_sight_app`：知识、元数据、分析会话与事件、报告、建模、审计和用户数据。
- 初始化入口：`backend/.venv/Scripts/python.exe database/bootstrap.py`。
- MySQL 数据完整性校验：`database/mysql/003_validate.sql`。

## 评分点对应

赛题评分关注业务知识组织、数据资源理解、自然语言分析、Agent 闭环、系统展示、工程可用性和应用价值。ForgeSight 在分析页面展示“问题 → 语义上下文 → SQL → 安全校验 → 执行结果 → 图表/结论”的证据链，并通过数据库只读账号、SQL 拦截、成本预算、超时限制、结果行数限制和审计日志保障工程可用性。

提交材料中的 PPT、演示视频、团队分工及过程文档属于参赛团队的交付物，不由运行时代码自动生成；后续应在本目录继续归档对应定稿。
