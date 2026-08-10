# Claude 项目协作规则（ForgeSight 前端）

## 语言规则
- 文档、注释说明、用户沟通使用中文
- 技术名词、字段名、函数名、文件名、包名保持英文
- 界面文案使用简体中文（品牌名 ForgeSight 除外）

## 项目类型
- 主类型：web-app（data-dashboard + ai-agent 展示型前端）
- 复杂度：medium
- 定位：A07 企业数据底座智能问析 Agent 平台的演示前端，纯前端 + mock 数据，本阶段不真实调用 LLM 与后端

## 必读上下文
修改代码前，必须先阅读以下文件（按任务相关性）：
1. `docs/PRODUCT_SPEC.md` — 产品范围、模块、角色
2. `docs/TECH_STACK_LOCK.md` — 技术栈锁定
3. `docs/UI_SPEC.md` — 设计 tokens 与组件规范（所有 UI 工作必读）
4. `docs/DATA_MODEL.md` — 前端数据实体与字段
5. `docs/API_CONTRACT.md` — api 层函数契约
6. `docs/BUSINESS_RULES.md` — 业务与演示规则
7. `docs/DECISIONS.md` — 架构决策记录
8. `docs/TODO.md` — 当前进度

## 技术栈锁定
详见 `docs/TECH_STACK_LOCK.md`。核心：Vite 6 + React 19 + react-router-dom 7（JSX）+ Tailwind CSS v4 + Recharts v3 + Vitest。禁止引入其他 UI 组件库、状态管理库、CSS 方案。

## 目录分层约束
```
src/
  main.jsx            # 入口
  app/                # 路由装配（AppRouter.jsx、routeConfig.js）
  api/                # 统一数据访问层，页面获取数据的唯一入口（当前为 mock 实现）
  mocks/              # mock 数据源，仅允许被 src/api/ 引用
  components/
    layout/           # AppShell、Sidebar、Topbar
    ui/               # 通用原子组件（Button、Card、Badge、Table、Modal、Tabs、Icon...）
    charts/           # Recharts 统一主题封装（四类图表）
  pages/
    business/         # 业务人员工作区页面
    admin/            # 管理员工作区页面
  lib/                # 纯工具函数（cn.js、format.js 等）
  styles/             # index.css：Tailwind 入口 + @theme tokens + 少量必要自定义 CSS
  assets/             # logo 等静态资源
```
分层规则：
1. `pages/` 禁止直接 import `mocks/`，必须经 `api/`
2. `components/ui/` 与 `components/charts/` 禁止 import `api/` 与 `pages/`（保持纯展示）
3. 图表必须使用 `components/charts/` 封装，页面不直接 import recharts
4. 新页面必须在 `app/routeConfig.js` 注册并更新 `docs/PRODUCT_SPEC.md` 页面清单

## 反幻觉规则
1. 不得编造字段、api 函数、路由、环境变量、package
2. 不得私自引入 `docs/TECH_STACK_LOCK.md` 未列出的依赖
3. 数据字段以 `docs/DATA_MODEL.md` 为准，api 函数以 `docs/API_CONTRACT.md` 为准
4. 不确定的事项必须标记 UNKNOWN 并报告，不得猜测
5. 发现文档与代码不一致时，必须停止并报告

## 新 API 规则
涉及 Tailwind v4、Recharts v3、React 19、react-router-dom 7 等版本敏感 API 时，必须先验证：
1. `package.json` 中的当前版本
2. 项目已有代码中的用法
3. 官方文档（Tailwind v4 与 v3 配置方式差异大，Recharts v3 与 v2 API 有差异，必须按 v4/v3 写法）
禁止只凭模型记忆写新 API。必要时使用 `api-researcher` subagent。

## subagent 使用规则
1. 修改超过 3 个文件，完成后必须使用 `reviewer` subagent 审查
2. 前端 UI 开发（新页面、组件、布局）先经 `frontend-builder` subagent 校对规范
3. 引入或升级依赖、使用不熟悉的库 API 时，必须使用 `api-researcher` subagent

## 开发流程
1. `/plan` 制定实现计划
2. `/build` 按计划实现
3. `/review` 审查
4. `/update-docs` 同步文档

## UI 一致性红线（详见 docs/UI_SPEC.md）
1. 颜色、圆角、阴影、间距只允许使用 `@theme` 中定义的 token，禁止散写魔法值
2. 禁止夸张色彩堆砌、大面积渐变、emoji 当图标、营销感文案
3. 仅亮色模式；密度基准 12–13px 正文
4. 每个页面必须处理 loading / empty / error 三态

## 完成前检查
1. 是否违反 `docs/TECH_STACK_LOCK.md`
2. 是否违反 `docs/DATA_MODEL.md` 与 `docs/API_CONTRACT.md`
3. 是否违反目录分层约束与 UI 红线
4. 是否有编造字段、路由、api 函数、package
5. `npm run test` 与 `npm run build` 是否通过
6. 是否更新 `docs/TODO.md`；架构决策是否记入 `docs/DECISIONS.md`

## 禁止事项
1. 禁止私自更换技术栈（含迁移到 Next.js / TypeScript / 其他 CSS 方案）
2. 禁止绕过 `src/api/` 直接在页面使用 mock 数据
3. 禁止在未阅读相关 docs 的情况下修改代码
4. 禁止把聊天上下文中的临时假设当成项目事实
5. 禁止实现 `docs/PRODUCT_SPEC.md` 标记为「暂不实现」的功能
