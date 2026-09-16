# ForgeSight 前端风格基线（强制，全页面适用）

来源：对 `D:\aaa\a-外包\docs\research` 下调研项目的界面实勘（chat2db 工作区、lightdash 看板、superset 列表、evidently 报告）+ 业务看板（OverviewPage）已验收形态。任何新页面、改版页面必须遵守本文；验收不通过 = 违反第 1 条或第 5 条。

## 1. 一个页面 = 一个工作区容器（最重要，禁止卡片堆叠）

- 页面主体是**一个**带边框容器（`rounded-panel border border-line bg-surface`），内部区域用 **1px 分隔线**（`border-t / border-b / divide-x / divide-y divide-line`）衔接。
- **禁止**在同一页堆叠两个及以上"独立圆角面板 + 页面底色间隙"的卡片墙——这是最常见的违规形态。
- 外层容器至多一个；工具条、结论行、事实条、左右分栏全部装在这一个容器里。
- 判定标准：把容器外边框去掉后，页面应仍是一块连续表面，而不是一堆散落的块。

## 2. 细工具条，无 hero

- 页头是一行 h-12 工具条：标题（text-sm font-semibold）+ 内联状态 + 右侧动作按钮。
- 不设大标题区、不设副标题长句；全局顶栏已有模块名时，页内标题用任务语义（如"执行审计记录"而非"权限与审计"）。

## 3. 结论与数字先行

- 工作区第一行是**一行状态结论**（图标 + 一句话 + 状态 pill + 主动作按钮）。
- 第二行是**分区事实条**：2–5 个关键数字，`divide-x divide-line`，数字 `font-mono text-base font-semibold`，标签 11px muted，补充说明 11px faint。
- 说明性文字永远退后，不占首屏主体。

## 4. 内容以扁平平铺分区展开

- 表格、表单、详情、图表直接铺在工作区内，用分隔线划分区块；区块头用小号标题（11px semibold muted）+ 右侧动作。
- 折叠区（accordion）是"行 + 展开的扁平内容"，展开内容不再包圆角卡片。
- 空状态紧凑（160–200px），不撑大框。

## 5. 卡片只允许两种身份

- **列表项**（superset 式）：一行 = 名称 + meta + 状态 pill，卡片感来自行本身。
- **图表瓦片**（lightdash 式）：看板中的可视化单元。
- 除此之外不得新增卡片容器。

## 6. 侧栏贴附，不悬浮

- 右侧详情/导轨与主区共享边框（`border-l`），属于同一容器（chat2db 三栏式）；独立悬浮侧卡禁止。
- 窄屏时侧栏降级为可开合抽屉或页签。

## 7. 字体档位（全项目统一）

- 正文 13–14px；辅助信息 12px；元数据 11px。
- 10px 只用于版本号、序号、mono 技术 ID、极短标签；9px 不用于业务信息（图表刻度除外）。
- 密度来自行高与分区，不来自缩小字号。

## 8. 状态必须真实

- 所有数字、状态、时间来自接口真实数据；装饰性假状态（写死的统计、未实现的策略承诺）一律删除。
- 技术 ID（mdl_/mt_/uuid）不进正文：tooltip 或底部一行 mono 脚注。
- 时间本地化显示（zh-CN），ISO 原文进 title。

## 9. 色彩与动效

- 冷白画布（bg-canvas/paper/surface 三级）、石墨文字、蓝绿（teal）+ 主蓝（primary）双强调，语义色只用于状态。
- 轻边框（border-line）、低阴影（shadow-card）；禁止渐变、装饰环、彩色底纹堆叠。
- transition 限定 colors/background/border/shadow/transform 中需要的项，禁止 transition-all。

## 10. 交互一致性

- 筛选状态同步 URL（useSearchParams），刷新不丢。
- 破坏性/成本动作两段式确认（按钮文案变化），不用 window.confirm。
- 导出类动作合并为一个下拉并显示预计条数。
- 表格行整行可点击进详情，行内动作按钮 stopPropagation。

## 参照锚点（实现时对照）

- 业务看板 OverviewPage：已验收的"工具条 + KPI 带 + 9/3 分栏"。
- 报告与交接 ReportsPage：单容器多行工作区。
- 周报设计器：三栏贴附 + 抽屉降级。
- 调研原图：chat2db 01-workspace / lightdash 02-dashboard / superset 02-home / evidently 06-manufacturing-drift-report。
