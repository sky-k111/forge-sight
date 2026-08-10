# ForgeSight 分析方案调整 V1

## 产品定义

业务端不再暴露“从检查点继续”“创建分支”等技术概念。用户看到的能力统一称为“调整分析条件”：

- 原分析保持不变；
- 用户只填写需要变化的条件；
- 系统创建独立子会话和首个 Run；
- 新旧方案均可回看，并展示条件与同名 KPI 的基础差异。

## 支持的调整项

- 时间范围；
- 分析对象；
- 指标口径；
- 分组维度；
- 图表类型；
- 补充分析说明。

只调整图表或说明时，请求标记为 `reuseArtifacts=snapshot`，界面明确提示复用原结果快照。时间、对象、指标或分组发生变化时，请求标记为 `reuseArtifacts=refresh`，重新查询当前数据。

## 分支持久化

V1 复用现有 `analysis_session`、`analysis_branch`、`analysis_checkpoint` 和 `analysis_message.metadata_json`，不新增数据库表：

1. 子 Session 保存 `parent_session_id` 与 `fork_checkpoint_id`；
2. 不复制父 Session 的历史消息；
3. 调整项、复用策略、原 Run ID 写入子 Session 第一条用户消息的 `metadata_json`；
4. `GET /api/analysis/sessions/{sessionId}` 返回 `branchContext`，供前端恢复原方案卡片和差异展示。

## 创建接口

`POST /api/analysis/checkpoints/{checkpointId}/branches`

```json
{
  "title": "质量分析 · 调整方案",
  "userId": "local-test-user",
  "instruction": "基于原分析，将时间范围调整为最近30天，并按产品类别分组。",
  "adjustments": {
    "timeRange": "最近 30 天",
    "groupBy": "产品类别"
  },
  "reuseArtifacts": "refresh"
}
```

接口原子创建子 Session、审计分支记录和首个排队 Run，避免出现只有继承消息、没有运行的“空分支”。

## V1 边界

- 已实现业务化调整面板、独立 Run、原方案引用和基础对比；
- 分支 Run 会读取检查点的业务摘要和 Artifact allowlist 作为上下文；
- `snapshot` 当前表达结果复用意图和数据快照语义，后续可进一步把图表重绘优化成完全跳过 SQL 执行；
- 不向业务用户展示 LangGraph checkpoint ID、线程 ID 或内部 State。
