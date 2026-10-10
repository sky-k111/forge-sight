# ForgeSight

**面向制造业的企业数据智能问析与经营分析平台。**

[快速开始](#快速开始) · [功能版图](#功能版图) · [系统架构](#系统架构) · [项目文档](#项目文档) · [联系维护者](#联系维护者)

ForgeSight 让业务人员无需掌握 SQL 和数据建模技能，也能用自然语言完成
数据定位、安全查询、图表分析和结论追溯。系统保留从问题到结论的完整证据链，
同时提供个人经营看板、知识与技能治理、Agent 记忆、智能建模、报告编排和异常监测能力。

> 赛题背景：【A07】企业数据底座智能问析 Agent 系统
> （杭州自动化技术研究院）
>
> - [赛题原文与实现映射](docs/competition/README.md)
> - [项目需求与任务中心](docs/tasks/README.md)
> - [开发协作规范](AGENTS.md)

## 核心价值

| 能力 | 用户获得什么 |
| :--- | :--- |
| 自然语言问析 | 用业务语言提问，Agent 自动规划并执行分析 |
| 全证据链 | 上下文、SQL、执行校验、查询结果和结论均可追溯 |
| 智能可视化 | 自动选择折线、柱状、环形、散点、热力图等图表 |
| 分支分析 | 从检查点调整条件，保留原方案并生成对比方案 |
| 数据治理 | 管理主题、对象、规则、指标口径和数据源语义 |
| 智能建模 | 训练、评估、预测、AutoML 搜索、漂移监测与 MCP 远程模型接入 |
| 经营看板与报告编排 | 组织个人指标与图表，编排报告章节、对比分析和版本 |
| 记忆与技能治理 | 管理分析记忆与受控技能，维护可复用的业务分析能力 |
| 多租户隔离 | 租户管理、服务端权限检查与数据范围约束 |
| 安全执行 | 只读账号、写意图拦截、超时、行数与成本门控 |
| 持续触达 | 周报 Agent、消息中心、邮箱推送和受控文件下载 |
| 异常监测 | 数据断流与生产指标监控、可靠告警、自动恢复和跨端通知 |

## 系统架构

```mermaid
flowchart LR
    subgraph UX[体验层]
        direction TB
        WEB[业务工作区<br/>智能问析 · 会话 · 报告]
        ADMIN[管理工作区<br/>知识 · 资源 · 模型 · 审计]
    end

    subgraph API[服务层 · FastAPI]
        direction TB
        ROUTER[API 与身份鉴权]
        STREAM[SSE 事件流]
        DOMAIN[领域服务<br/>知识 · 元数据 · 建模 · 报告]
        GUARD[SQL 安全网关<br/>只读 · 超时 · 成本门控]
    end

    subgraph AI[智能编排层]
        direction TB
        GRAPH[LangGraph Agent]
        CONTEXT[语义检索与上下文组装]
        VERIFY[SQL 语法与安全校验]
        MODEL[OpenAI 兼容模型<br/>DeepSeek · Qwen · ChatGLM]
    end

    subgraph DATA[数据层]
        direction TB
        MYSQL[(MySQL 8.4<br/>企业业务库 · 只读)]
        PG[(PostgreSQL 16<br/>平台数据 · 读写)]
    end

    WEB -->|HTTP| ROUTER
    ADMIN -->|HTTP| ROUTER
    STREAM -.实时事件.-> WEB
    ROUTER --> DOMAIN
    ROUTER --> GRAPH
    GRAPH --> CONTEXT
    CONTEXT --> MODEL
    MODEL --> VERIFY
    VERIFY --> GUARD
    GUARD -->|SELECT| MYSQL
    DOMAIN --> PG
    GRAPH -->|状态 · 检查点 · 事件| PG
    PG -.持久化事件.-> STREAM

    classDef experience fill:#EFF6FF,stroke:#2563EB,color:#1E3A8A,stroke-width:1.5px;
    classDef service fill:#ECFDF5,stroke:#059669,color:#064E3B,stroke-width:1.5px;
    classDef agent fill:#FFF7ED,stroke:#EA580C,color:#7C2D12,stroke-width:1.5px;
    classDef storage fill:#F8FAFC,stroke:#475569,color:#0F172A,stroke-width:1.5px;
    class WEB,ADMIN experience;
    class ROUTER,STREAM,DOMAIN,GUARD service;
    class GRAPH,CONTEXT,VERIFY,MODEL agent;
    class MYSQL,PG storage;
    style UX fill:#F8FAFC,stroke:#93C5FD,color:#1E3A8A,stroke-width:1px;
    style API fill:#F8FAFC,stroke:#6EE7B7,color:#064E3B,stroke-width:1px;
    style AI fill:#FFFBEB,stroke:#FDBA74,color:#7C2D12,stroke-width:1px;
    style DATA fill:#F8FAFC,stroke:#CBD5E1,color:#0F172A,stroke-width:1px;
```

### 数据职责

- **MySQL 业务库**：保存生产工单、工序产量、质量检验、设备停机、
  库存等企业数据，ForgeSight 仅使用只读账号访问。
- **PostgreSQL 平台库**：保存业务知识、分析会话、Agent 检查点、
  报告、平台用户和审计记录。
- **LangGraph Agent**：负责分析状态机、澄清交互、错误恢复、
  分支重放和预算控制。

## 智能问析工作流

```mermaid
flowchart LR
    Q([自然语言问题]) --> P[理解意图]
    P --> C{信息是否充分}
    C -->|否| ASK[发起澄清]
    ASK --> P
    C -->|是| R[检索业务知识]
    R --> S[生成候选 SQL]
    S --> G{安全与成本校验}
    G -->|不通过| F[有限纠错]
    F --> S
    G -->|通过| X[只读执行]
    X --> V[结果解释与证据生成]
    V --> CH[图表自动选型]
    CH --> A([结论与证据链])

    classDef start fill:#1D4ED8,stroke:#1D4ED8,color:#FFFFFF,stroke-width:2px;
    classDef process fill:#EFF6FF,stroke:#3B82F6,color:#1E3A8A,stroke-width:1.5px;
    classDef decision fill:#FFF7ED,stroke:#F97316,color:#7C2D12,stroke-width:1.5px;
    classDef repair fill:#FEF2F2,stroke:#EF4444,color:#7F1D1D,stroke-width:1.5px;
    classDef result fill:#ECFDF5,stroke:#10B981,color:#064E3B,stroke-width:2px;
    class Q start;
    class P,R,S,X,V,CH process;
    class C,G decision;
    class ASK,F repair;
    class A result;
```

## 功能版图

### 业务工作区

- **分析总览与经营看板**：展示业务指标、数据源就绪度、可分析主题和推荐问题。
- **智能问析**：支持提问、澄清、流式执行、图表、结论和证据链。
- **历史会话**：按数据源和状态筛选，恢复上下文后继续分析。
- **智能建模**：完成算法选择、训练、评估、预测和方案对比，支持 AutoML 与 MCP 远程模型接入。
- **个人记忆**：维护和复用分析过程中的个人记忆。
- **周报中心**：Agent 自动生成并直接发布周报，以管理摘要、关键指标、趋势图、异常表和可折叠证据支持快速决策，并提供 PDF/Excel 下载。
- **消息中心**：统一接收 Agent 周报和管理员公告，通过未读、优先级和内容类型快速筛选。
- **智能异常监测**：查看数据断流、良率和非计划停机告警、恢复状态及证据趋势。

### 管理工作区

- **治理总览**：集中展示系统健康度和治理待办。
- **业务知识**：维护主题、对象、规则和指标口径。
- **数据资源**：接入数据源、扫描元数据并辅助理解业务语义。
- **模型治理**：管理模型注册、评审、发布、漂移监测和退役。
- **技能与记忆治理**：管理受控分析技能与 Agent 记忆。
- **租户管理**：维护租户、成员及数据访问边界。
- **系统配置**：展示只读、超时和行数上限等默认安全边界；实际参数由
  `backend/.env` 管理。
- **安全审计**：记录关键操作与安全策略执行结果。
- **公告与投递**：管理员按用户、组织或角色发布公告，并查看邮件状态、失败分类和安全重试。
- **监控规则**：从受控模板绑定已登记表字段，验证、发布、调度并查看失败任务。

## 项目文档

| 文档 | 内容 |
| --- | --- |
| [文档导航](docs/README.md) | 架构、设计与验收资料入口 |
| [赛题与实现映射](docs/competition/README.md) | 项目背景与需求来源 |
| [项目任务中心](docs/tasks/README.md) | 开发任务、依赖与验收状态 |
| [数据库说明](database/README.md) | 初始化、数据来源与数据库结构 |
| [飞书私聊机器人](docs/integrations/FEISHU_P2P_BOT.md) | 企业自建应用与独立进程配置 |
| [监测运维手册](docs/MONITORING_RUNBOOK.md) | 监测规则、告警与故障处置 |
| [开发规范](AGENTS.md) | 目录、代码、测试与协作要求 |

功能与任务状态以任务中心中的验收记录为准。

## 技术栈

| 层级 | 主要技术 |
| :--- | :--- |
| 前端 | Vite 6 · React 19 · React Router 7 · Tailwind CSS 4 · Recharts 3 |
| 后端 | FastAPI · SQLAlchemy 2 · LangGraph · pandas · scikit-learn · FLAML · LightGBM · XGBoost |
| 数据库 | MySQL 8.4（业务库）· PostgreSQL 16 + pgvector（平台库） |
| 模型 | OpenAI 兼容 API（如 DeepSeek、Qwen）· MCP 远程模型 |
| 工程 | Docker Compose · pytest · Vitest · Ruff · ESLint · GitHub Actions |

## 快速开始

### 环境要求

- Windows PowerShell
- Python 3.14（与当前 CI 保持一致）
- Node.js 24（与当前 CI 保持一致）
- Docker Desktop

### 1. 克隆项目并启动数据库

从准备存放项目的目录执行：

```powershell
git clone https://github.com/sky-k111/forge-sight.git
cd forge-sight
docker compose up -d
docker compose ps
```

等待两个数据库容器健康后再初始化数据库。

默认使用以下本地端口：

- MySQL：`3307`
- PostgreSQL：`5433`

### 2. 配置后端

```powershell
cd backend
Copy-Item .env.example .env
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-dev.txt
```

后端目录中的两个环境文件用途不同：

- `.env.example`：可提交的配置模板，只维护字段和安全占位值，不填写真实密钥。
- `.env`：当前电脑实际读取的配置，已被 Git 忽略；日常只修改这个文件。

首次安装才执行一次 `Copy-Item .env.example .env`。已有 `.env` 时不要重复执行，
否则会覆盖本地数据库、模型和邮件配置。使用当前 `docker-compose.yml` 的本地默认配置时，
至少替换为：

```dotenv
BUSINESS_DB_URL=mysql+pymysql://forgesight_reader:forgesight_reader@127.0.0.1:3307/forge_sight?charset=utf8mb4
PLATFORM_DB_URL=postgresql+psycopg2://postgres:forgesight_app@127.0.0.1:5433/forge_sight_app
POSTGRES_ADMIN_DB_URL=postgresql+psycopg2://postgres:forgesight_app@127.0.0.1:5433/postgres
ADMIN_DB_URL=mysql+pymysql://root:forgesight_root@127.0.0.1:3307/?charset=utf8mb4
LLM_API_KEY=替换为你的模型密钥
AUTH_TOKEN_SECRET=替换为随机长字符串
AUTH_ADMIN_PASSWORD=替换为本地管理员密码
EMAIL_CODE_SECRET=替换为独立随机长字符串
```

其中 `forgesight_reader` 是初始化脚本将创建的本地只读账号密码。
部署环境必须更换 Compose 默认密码，不得沿用上述开发值。运行初始化前，
请先配置所需字段中的 `CHANGE_ME`。`LLM_BASE_URL` 与 `LLM_MODEL` 应对应你使用的模型服务。

模板默认使用 `MAIL_PROVIDER=capture`，验证码写入本地捕获目录。邮箱门禁与
捕获模式由 `AUTH_EMAIL_GATE_ENABLED`、`AUTH_EMAIL_GATE_ALLOW_CAPTURE` 控制；
需要真实发信时配置 SMTP，并设置 `AUTH_EMAIL_GATE_ALLOW_CAPTURE=false`。
完整字段见 [环境配置模板](backend/.env.example)。

#### 使用 QQ 邮箱发送验证码

ForgeSight 可以向任意有效邮箱发送验证码；QQ 邮箱在这里仅作为发件邮箱。
在 QQ 邮箱设置中开启 SMTP 服务并生成授权码，然后只在 `backend/.env` 中填写：

```dotenv
AUTH_EMAIL_GATE_ENABLED=true
AUTH_EMAIL_GATE_ALLOW_CAPTURE=false
EMAIL_CODE_SECRET=替换为随机长字符串
MAIL_PROVIDER=smtp
MAIL_FROM_ADDRESS=你的QQ邮箱@qq.com
MAIL_FROM_NAME=ForgeSight
SMTP_HOST=smtp.qq.com
SMTP_PORT=465
SMTP_USERNAME=你的QQ邮箱@qq.com
SMTP_PASSWORD=你的QQ邮箱SMTP授权码
SMTP_USE_TLS=true

# 独立灰度/回滚开关（修改后重启后端）
REPORT_SCHEDULER_ENABLED=true
MAIL_WORKER_ENABLED=true
```

`SMTP_PASSWORD` 填 QQ 邮箱生成的 SMTP 授权码，不是 QQ 密码，也不是用户收到的六位验证码。
修改后需要重启后端。真实邮箱、授权码和 `EMAIL_CODE_SECRET` 不得写入 `.env.example`、文档或 Git。

### 3. 初始化数据库

```powershell
cd ..
backend\.venv\Scripts\python.exe database\bootstrap.py
```

初始化脚本会创建 MySQL 业务库、只读账号、官方数据集、PostgreSQL 平台库
以及知识基线。

### 4. 启动后端

```powershell
cd backend
.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

- 后端地址：<http://127.0.0.1:8000>
- 接口文档：<http://127.0.0.1:8000/docs>
- 数据库健康检查：<http://127.0.0.1:8000/health>

如需启用“飞书私聊提问、飞书原会话返回”的企业自建应用机器人，完成应用
权限与 `backend/.env` 配置后，另开进程运行：

```powershell
.venv\Scripts\python.exe scripts\run_feishu_bot.py
```

它不是报告模块使用的群 Webhook 机器人。完整配置见
[`docs/integrations/FEISHU_P2P_BOT.md`](docs/integrations/FEISHU_P2P_BOT.md)。

智能异常监测默认随后台 Worker 运行；如需拆成独立进程或配置飞书/钉钉即时告警，参见
[`docs/MONITORING_RUNBOOK.md`](docs/MONITORING_RUNBOOK.md)。

### 5. 启动前端

另开一个 PowerShell 窗口：

```powershell
cd frontend-new
npm ci
npm run dev
```

终端会输出实际可用地址。若默认 `5173` 已被占用，Vite 会自动选择下一个
可用端口，请以终端显示的地址为准。使用其他端口时，还需要把该地址加入
`backend/.env` 的 `CORS_ORIGINS`，然后重启后端。

### 一键启动

```powershell
.\fs.bat
```

常用参数：

```powershell
.\fs.bat -Backend
.\fs.bat -Frontend
.\fs.bat -SkipDb
.\fs.bat -Stop
```

## 项目目录

```text
forge-sight/
├── backend/                       # FastAPI 后端
│   ├── app/
│   │   ├── agent/                 # LangGraph 编排、状态与检查点
│   │   ├── api/                   # HTTP 与 SSE 接口
│   │   ├── auth/                  # 身份认证与权限
│   │   ├── db/                    # SQLAlchemy 模型与数据库会话
│   │   ├── delivery/              # 邮件模板、SMTP、投递队列与重试
│   │   ├── gateway/               # SQL 安全执行网关
│   │   ├── integrations/          # 飞书等外部协作渠道适配
│   │   ├── knowledge/             # 业务知识与指标口径
│   │   ├── metadata/              # 元数据扫描与语义理解
│   │   ├── messages/              # 消息中心与管理员公告
│   │   ├── modeling/              # 训练、评估、预测与模型治理
│   │   ├── reports/               # 报告模板与生成服务
│   │   └── retrieval/             # 语义检索与上下文组装
│   ├── scripts/                   # 后端维护与可靠性脚本
│   └── tests/                     # pytest 测试
├── frontend-new/                  # Vite + React 前端
│   └── src/
│       ├── api/                   # API 客户端
│       ├── components/            # 共享组件与数据图表
│       ├── features/              # 流式事件与业务状态
│       ├── pages/                 # 业务端、管理端和认证页面
│       └── styles/                # 全局样式与设计变量
├── database/                      # MySQL/PostgreSQL 初始化
├── scripts/                       # 项目级启动与辅助脚本
├── docs/
│   ├── README.md                  # 项目文档导航
│   ├── competition/               # 赛题原文与需求映射
│   ├── tasks/                     # 当前项目任务中心
│   │   ├── REQUIREMENTS.md        # 需求基线：要做什么
│   │   ├── TASK_INDEX.md          # 任务索引：如何拆分与依赖
│   │   ├── 未完成/                # 待实施大任务及其小任务
│   │   └── 已完成/                # 已通过验收的大任务
│   ├── phase-5-agent/             # 阶段五分析与验收资料
│   └── phase-6-reports/           # 阶段六历史调研资料
├── AGENTS.md                      # 开发与 AI 协作规范
├── docker-compose.yml             # 本地数据库容器
└── fs.bat                         # Windows 一键启动入口
```

## 测试与构建

```powershell
# 后端
cd backend
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m ruff check .

# 前端
cd ..\frontend-new
npm test
npm run lint
npm run build
```

测试应使用隔离的数据库；PostgreSQL 专项测试使用 `POSTGRES_TEST_URL`。
持续集成配置见 [ci.yml](.github/workflows/ci.yml)。

## 运行监控与发布

- 管理员可在“公告与投递”页查看调度到期数、队列状态、邮件耗时 P95 和死信/积压告警。
- 管理员接口 `GET /api/v1/operations/metrics` 只返回脱敏计数、状态和耗时，不返回邮箱、正文、验证码或凭据。
- 邮件故障时设置 `MAIL_WORKER_ENABLED=false`；调度故障时设置 `REPORT_SCHEDULER_ENABLED=false`。两个开关都不会影响历史周报、站内消息和已授权文件读取。
- 发布前按 [T 发布评审](docs/release/T_RELEASE_REVIEW.md) 执行；日常告警处置见 [运维手册](docs/release/T_OPERATIONS_RUNBOOK.md)，灰度与回滚见 [发布方案](docs/release/T_ROLLOUT_ROLLBACK.md)。

## 安全约束

- 不提交 `backend/.env`、邮箱凭据、Webhook 密钥或本地数据库备份。
- 业务数据库使用独立只读账号，只授予 `SELECT` 权限。
- SQL 执行经过写意图拦截、语法检查、超时、行数上限和成本预算校验。
- 密码使用 scrypt 加盐哈希；服务端身份覆盖客户端传入的用户标识。
- 新增下载、消息和邮件能力时，必须继续执行服务端权限检查与审计。
- MCP 远程模型默认禁止所有地址，通过 `MCP_MODEL_ALLOWED_ORIGINS` 显式配置允许的服务来源。

## 联系维护者

**Ethan Chen（陈奕恺）**

- GitHub：[sky-k111](https://github.com/sky-k111)
- 开发合作与项目咨询：[1503794397@qq.com](mailto:1503794397@qq.com)
- 缺陷反馈与功能建议：[GitHub Issues](https://github.com/sky-k111/forge-sight/issues)
