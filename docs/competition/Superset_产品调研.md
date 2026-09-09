# Apache Superset 产品调研报告

> 调研对象：Apache Superset（开源 BI / 数据探索与可视化平台）
> 调研维度：① 前端 UI 风格 ② 支持的功能 ③ 底层实现架构
> 目的：为 ForgeSight 的图表体系、语义层、多租户/RBAC、异步 SQL Lab、嵌入分析等设计提供参照。

---

## 0. 一句话定位

Superset 是一个 **「面向数据分析师与业务人员的开源自助式 BI 平台」**：用 Web 界面连接数十种 SQL 数据源，无需写代码即可探索数据、拖拽生成图表、组织成仪表板，并支持细粒度权限与嵌入分析。核心价值是 **「低门槛可视化 + 强扩展的数据接入」**。

---

## 1. 前端 UI 风格

### 1.1 技术栈
- **框架**：React + TypeScript，函数组件 + Hooks
- **状态管理**：Redux（全局状态）+ 组件本地状态；复杂状态用 `useReducer`，耗计算用 `useMemo/useCallback` 缓存
- **构建**：Webpack 打包；npm workspaces 管理 monorepo
- **国际化**：内置 i18n 机制，UI 文案可多语言化

### 1.2 组件 / 样式规范
- **组件库**：直接采用 **Ant Design（AntD）**，不重复造轮子；统一用 `@superset-ui/core` 对 AntD 组件做二次封装
- **样式方案**：使用 **Emotion**（CSS-in-JS），优先引用主题变量（theme variables），避免写死颜色与自定义 CSS
- **组件目录约定**：`superset-frontend/src/components/{ComponentName}/index.tsx`，PascalCase 目录 + 同名文件
- **质量要求**：每个组件必须有 Storybook（`.stories.tsx`）与 Jest + React Testing Library 单测（`.test.tsx`）
- **可访问性**：使用 ARIA roles，保证键盘与读屏可访问
- **主题体系**：基于 AntD + ECharts 主题，默认 / 暗色 / 可配置主题；chart 级支持可配置色板，JSON metadata 可调整字体与颜色

### 1.3 可视化底层
- **静态/交互图表**：大量使用 **Apache ECharts**
- **地理空间（地图）**：使用 **deck.gl**（Polygon / Arc / Grid / Screen Grid / Path 等图层）
- **表格**：**AG Grid**

**给 ForgeSight 的启示**：图表与技术选型可以「ECharts 负责多数图表 + deck.gl 负责任何空间维度 + AG Grid 负责大表格」，比从零写 canvas/SVG 性价比高；主题用变量而非写死颜色，方便后续做暗色与品牌色切换。

---

## 2. 支持的功能

### 2.1 五大核心能力
1. **Chart Explorer（图表探索器）**：可视化拖拽构建器，插件化，提供 40+ 可视化类型
2. **SQL Lab（SQL 工作台）**：Web IDE，支持查询历史、结果可视化、CSV 导出、异步/并行执行
3. **Dashboards（仪表板）**：把多个图表组织成页面，支持布局拖拽、级联筛选、URL 状态
4. **Semantic Layer（语义层）**：轻量语义层，自定义 dimension / metric，复用口径
5. **Database / 数据引擎连接**：支持 50+ 主流 SQL 数据库（MySQL、PostgreSQL、ClickHouse、Doris、BigQuery、Snowflake 等）

### 2.2 常用可视化类型（示例）
- Line / Bar / Area / Time-series Bar
- Pie / Donut
- Scatter
- Big Number（KPI 卡片）
- Table
- World Map（choropleth 密度地图）
- Heatmap
- Sankey（桑基图）
- Treemap
- Gantt（2025-07 新增）
- Radar & Rose、Box Plot
- Geospatial / deck.gl：Polygon、Arc、Grid、Screen Grid、Path

### 2.3 权限 / 安全
- **认证**：OAuth2、LDAP、Remote User、Database 认证后端
- **权限**：RBAC（角色 + 权限）
- **行级安全**：Row-Level Security (RLS)，同一张表对不同人只放行部分数据
- **嵌入分析**：签名 GuestToken 实现 iframe / 嵌入式仪表板
- **多租户线索**：通过「数据源归属 + RLS + RBAC」实现逻辑上的多租户隔离

### 2.4 异步 / 缓存 / 部署
- **异步任务**：Celery（周期性任务 + 查询任务排队）
- **缓存**：Redis（查询结果缓存 + message broker）
- **实时推送**：WebSocket 推送任务状态与实体变更
- **部署**：Docker、Kubernetes、Helm Charts

**给 ForgeSight 的启示**：KPI 卡片（Big Number）、Table、Line/Bar/Pie/Heatmap/Sankey/Treemap 是我们可对照补齐的图表清单；语义层正是我们「指标口径治理 + 指标复用」的功能对标；若做强查询，可借鉴 SQL Lab 的异步执行 + 历史复用思想。

---

## 3. 底层架构

### 3.1 总体三层架构
```
React 表现层  ──►  Flask REST API / 应用层  ──►  可插拔数据库抽象层
(charts,           (查询编排、认证、RBAC、      (BaseEngineSpec + SQLAlchemy
 dashboards,         缓存、异步调度)              方言适配)
 SQL Lab)
```

### 3.2 技术栈
- **后端语言**：Python
- **Web 框架**：Flask + Flask-AppBuilder（自带 Admin / RBAC / 认证）
- **ORM**：SQLAlchemy（1.4.x，承担 ORM 与数据库方言抽象）
- **数据处理**：Pandas
- **异步任务**：Celery
- **分布式协调**：Redis（缓存 + 分布式锁 + 事件发布）
- **元数据库**：存储用户 / 角色 / 图表 / 仪表板 / 数据源连接等元信息

### 3.3 核心抽象：BaseEngineSpec
- 每种数据库用一个 `BaseEngineSpec` 子类实现，插拔式注册
- 职责：SQL 方言、认证方式、SSH 隧道、时间列 / 指标的识别、`EXPLAIN` 与 SQL 校验等
- 这就是「连接任意数据库都能统一查询」的关键

### 3.4 一条查询的生命周期
1. 用户在 Dashboard / SQL Lab 交互
2. 前端构造 `QueryContext` / `QueryObject` 序列化模型
3. 后端做安全校验（数据源可见性、RLS、SQL 白名单）
4. 检查查询结果缓存（Redis / 文件缓存）
5. 若无缓存，落到目标数据库执行
6. 异步场景走 **Global Task Framework (GTF)**：任务入队 Celery，客户端轮询或 WebSocket 流式接收进度
7. 结果回填缓存并返回

### 3.5 实时通信
- 可选 **Node.js WebSocket sidecar**（`superset-websocket`）
- 通过 **Redis Pub/Sub** 与 Flask 通信，推送实体变更 / 异步任务进度

### 3.6 设计原则（可复用的工程理念）
- **无状态 Web 层**：可水平扩展
- **可插拔数据库方言**：新增数据库不改主流程
- **异步任务解耦**：长查询不进同步 HTTP 线程
- **分布式协调**：缓存 / 锁 / 事件统一走 Redis

---

## 4. 结合 ForgeSight 的对比与启示

| 维度 | Superset | ForgeSight（可对标） |
| --- | --- | --- |
| 定位 | 自助式 BI，人拖拽 | 自然语言问析 Agent，机器生成图表 |
| 图表 | 40+ 类型（ECharts + deck.gl + AG Grid） | 已支持 6 类图表 + KPI 卡 + 结果表格，基础够用 |
| 语义层 | 自定义 dimension/metric | 已有「指标口径治理」机制，可强化 |
| 隔离 | 数据源归属 + RLS + RBAC | 已做多租户改造 |
| 查询 | SQL Lab 异步 + 缓存 + 历史 | 可借鉴异步长查询、结果缓存、query history |
| 嵌入 | GuestToken | 可给外部系统做嵌入式问析 |

**建议的可借鉴点（按优先级）**：
1. **图表覆盖补全**：对照 Superset 的 Big Number / Heatmap / Sankey / Treemap / Gantt 等，把「支持图表类型」写清楚并补齐高频类型。
2. **语义层思想**：Superset 的 dimension / metric 定义就是「口径治理」，与我们的指标元数据高度同源，可用作口径文档的表达术语。
3. **异步长查询**：参考 SQL Lab / Celery + Redis 异步 + WebSocket 推送进度，避免长 SQL 阻塞同步接口。
4. **缓存与并发**：Redis 结果缓存 + 分布式锁，是做查询幂等和生产健壮性的成熟做法。
5. **嵌入分析**：若未来给企业做集成，可用签名 token 提供只读嵌入。

---

## 5. 参考来源

- Superset 官方前端组件风格指南 / 开发者文档
- Superset 官方架构与功能概览（deepwiki 汇总）
- Superset 官方 Release Notes（Gantt 图表、Bootstrap 主题管理、2015-2025 迭代）
