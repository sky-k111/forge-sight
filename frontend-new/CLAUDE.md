# Claude 项目协作规则（ForgeSight 前端）

## 语言规则
- 文档、注释说明、用户沟通使用中文
- 技术名词、字段名、函数名、文件名、包名保持英文
- 界面文案使用简体中文（品牌名 ForgeSight 除外）

## 项目类型
- 主类型：web-app（data-dashboard + ai-agent 展示型前端）
- 复杂度：medium
- 定位：A07 企业数据底座智能问析 Agent 平台前端。**大部分模块已接入真实 FastAPI 后端**（数据源/资源/知识/安全/问析/报告/建模），业务主线无 mock 残留；仅 `api/overview.js` 的管理端健康/治理队列仍是 mock 占位

## 必读上下文
修改代码前，必须先阅读以下文件（按任务相关性）：
1. `../backend/IMPLEMENTATION_PLAN.md` — 后端实现规划与阶段接口契约（建模/报告/问析契约以它为准）
2. `../AGENTS.md` — 仓库级安全与协作规则（只读数据网关、审计、禁止提交凭据等）
3. `../docs/phase-5-agent/` — 问析 Agent 阶段设计（事件、状态机、追踪图）
4. `../docs/phase-6-reports/` — 报告模块阶段设计
5. `../README.md` — 项目总览与启动方式
6. `src/app/routeConfig.js` — 路由装配与页面清单

## 技术栈锁定
Vite 6 + React 19 + react-router-dom 7（JSX）+ Tailwind CSS v4 + Recharts v3 + Vitest（已锁定版本，见 package.json）。禁止引入其他 UI 组件库、状态管理库、CSS 方案。

## 目录分层约束
```
src/
  main.jsx            # 入口
  app/                # 路由装配（routeConfig.js、AppRouter.jsx）
  api/                # HTTP 客户端层，页面获取数据的唯一入口
    _http.js          # 真实后端访问基座（httpGet/httpPost，BASE = VITE_API_BASE ?? http://127.0.0.1:8000）
    _client.js        # 仅 overview.js 使用的 mock 响应工具
    modeling.js       # 智能建模/模型治理 API（/api/v1/modeling/*、/api/v1/model-governance/*）
    reports.js        # 报告 API，真实模块的代码风格参考
  mocks/              # mock 数据源，仅允许被 src/api/ 引用
  components/
    layout/           # AppShell、Sidebar、Topbar
    ui/               # 通用原子组件（Button、Card、Badge、Table、Modal、Tabs、Icon...）
    charts/           # Recharts 统一主题封装（RankBarChart、TrendLineChart、ComboMetricChart 等，页面不得直接 import recharts）
  features/
    analysis/         # 问析实时订阅（api.js，SSE 推送）、视图适配（runViewAdapter）
  pages/
    business/         # 业务人员工作区页面（AnalysisPage、ModelingPage...）
    admin/            # 管理员工作区页面（ModelGovernancePage...）
  lib/                # 纯工具函数（cn.js、format.js 等）
  styles/             # index.css：Tailwind 入口 + @theme tokens + 少量必要自定义 CSS
  assets/             # logo 等静态资源
```
分层规则：
1. `pages/` 禁止直接 import `mocks/`，必须经 `api/`
2. `components/ui/` 与 `components/charts/` 禁止 import `api/` 与 `pages/`（保持纯展示）
3. 图表必须使用 `components/charts/` 封装，页面不直接 import recharts
4. 新页面必须在 `src/app/routeConfig.js` 注册

## 前后端联调约定
- 开发联调默认连 `http://127.0.0.1:8000`（FastAPI），可用环境变量 `VITE_API_BASE` 覆盖
- 演示用户 `USER_ID = "local-test-user"`（与后端 contracts 默认一致）
- 建模/治理 API 契约与后端 `backend/app/modeling/contracts.py`、`backend/app/api/modeling.py`、`backend/app/api/model_governance.py` 一一对应；改前端字段前先核对后端返回的 task/model dict
- Agent 建模提案链路：问析识别建模意图 → 后端生成 `modeling-proposal` run（status `modeling-proposal`，proposal 字段）→ 前端提案卡确认 → `createModelingTask` → sessionStorage `forgesight:modeling-draft` 记录 taskId → 跳建模页
- 建模任务状态机：`draft → training → trained / failed`（trained 后可取 evaluation 与 predict）；治理状态机：`pending →(review pass)→ published`、`pending →(review reject)→ rejected →(review)→ published`，published/rejected 可 retire；非法迁移后端返回 409

## 反幻觉规则
1. 不得编造字段、api 函数、路由、环境变量、package
2. 不得私自引入技术栈锁定外的新依赖
3. 数据字段与 API 契约以实际代码为准（后端 contracts.py / 前端 api/ 模块），文档与代码不一致时以代码为准并报告
4. 不确定的事项必须标记 UNKNOWN 并报告，不得猜测

## 新 API 规则
涉及 Tailwind v4、Recharts v3、React 19、react-router-dom 7 等版本敏感 API 时，必须先验证：
1. `package.json` 中的当前版本
2. 项目已有代码中的用法
3. 官方文档（Tailwind v4 与 v3 配置方式差异大，Recharts v3 与 v2 API 有差异，必须按 v4/v3 写法）
禁止只凭模型记忆写新 API。

## 开发流程
1. `/plan` 制定实现计划
2. 按计划实现（跨多文件的改动完成后自查，必要时用 subagent 独立审查）
3. 跑测试与构建收口
4. 同步文档（CLAUDE.md / IMPLEMENTATION_PLAN.md / 阶段报告）

## UI 一致性红线
1. 颜色、圆角、阴影、间距只允许使用 `@theme` 中定义的 token，禁止散写魔法值
2. 禁止夸张色彩堆砌、大面积渐变、emoji 当图标、营销感文案
3. 支持亮/暗双模式（`data-theme` 切换，新颜色必须同时定义两套）
4. 密度基准 12–13px 正文
5. 每个页面必须处理 loading / empty / error 三态

## 完成前检查
1. 是否违反技术栈锁定
2. 是否违反目录分层约束与 UI 红线
3. 是否有编造字段、路由、api 函数、package
4. 前端契约是否与后端实际返回一致（新建/改动建模、报告、问析 API 时重点核对）
5. `npm run test` 与 `npm run build` 是否通过；改动后端时 `../backend/.venv/Scripts/python.exe -m pytest -q` 是否通过
6. 是否更新相关文档与阶段报告

## 禁止事项
1. 禁止私自更换技术栈（含迁移到 Next.js / TypeScript / 其他 CSS 方案）
2. 禁止绕过 `src/api/` 直接在页面使用 mock 数据
3. 禁止在未阅读相关文档与对应后端代码的情况下修改接口
4. 禁止把聊天上下文中的临时假设当成项目事实
5. 禁止提交 `../backend/.env` 或任何明文凭据（AGENTS.md 红线）
6. 禁止删除数据库表或数据；改表结构必须用迁移/ALTER TABLE（仓库既有约束）
