# ForgeSight 阶段 5 收口验收报告

验收日期：2026-07-22  
验收范围：智能问析 Agent 闭环，不包含阶段 6 完整报告生成与阶段 7 机器学习建模。

## 1. 收口结论

阶段 5 的最后一批功能已经完成：复杂指代与合并澄清、数据库 `EXPLAIN` 成本门控、服务中断恢复、并发幂等和双层安全审计。系统可以进入阶段 6；阶段 5 后续仅处理联调中发现的缺陷。

## 2. 本批完成能力

- 复合问题中的“它/其”绑定到上一步任务，依赖任务能够读取上一步结果 Artifact；
- 跨轮出现多个候选对象时主动澄清，模糊“最近”时同时询问时间范围；
- 多个缺失条件合并为一张卡片，回答后在原 Run 上恢复；
- MySQL 使用 `EXPLAIN FORMAT=JSON`，PostgreSQL 使用 `EXPLAIN (FORMAT JSON)`；
- 预计扫描行数或优化器成本超阈值时阻断执行，并允许最多两次 SQL 优化；
- Worker 使用数据库租约互斥认领；过期 `running` Run 可重新认领；
- 已存在 LangGraph Checkpoint 时从下一节点继续，不重复调用已经完成的 LLM 步骤；
- `waiting_user` Run 不会被 Worker 误认领；
- 意图层写操作拦截和 SQL AST 拦截均形成审计记录。

## 3. 自动化测试结果

| 测试项 | 结果 |
|---|---:|
| 后端 Pytest | 42 passed |
| 前端 Vitest | 33 passed |
| 前端生产构建 | 通过，673 modules transformed |
| MySQL EXPLAIN 解析 | 通过 |
| PostgreSQL EXPLAIN 解析 | 通过 |
| 长历史连续追问 | 通过 |
| Checkpoint 恢复且不重复调用 LLM | 通过 |

## 4. 真实服务验收记录

### 4.1 并发幂等与安全阻断

- 并发请求数：16；
- HTTP 接受响应：16；
- 实际创建 Run：1；
- 提交耗时：631 ms；
- 终态：`blocked / WRITE_INTENT_BLOCKED`；
- LLM 调用次数：0；
- Run：`run_a5f03b8f345e47f7bda4e1d8`；
- 管理端审计：已生成“Agent 写操作意图拦截”记录。

可复验命令：

```powershell
cd backend
.\.venv\Scripts\python.exe .\scripts\verify_phase5_reliability.py --concurrency 16
```

### 4.2 EXPLAIN 成本门控

- 问题：统计每条产线最近 7 天的产量趋势；
- Run：`run_63524764b1434f1b958ca354`；
- 状态：`succeeded`；
- 预计扫描行数：1；
- 优化器成本：1.06；
- 门控结果：允许执行。

### 4.3 合并澄清

- 问题：看看它最近怎么样；
- 历史候选对象：设备、工序；
- Run：`run_6cba517a4cd34838a5c1c024`；
- 状态：`waiting_user`；
- 一次返回两个澄清字段：`object`、`timeRange`。

## 5. 下一阶段

下一步进入阶段 6：完整报告生成。阶段 6 将复用阶段 5 的 Session、Run、任务拆解、语义检索、SQL 安全链路、结果 Artifact 和证据链，不重新建设 Agent 运行时。
