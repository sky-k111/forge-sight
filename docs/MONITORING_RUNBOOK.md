# 智能异常监测运行手册

本手册对应 `SMART_ANOMALY_MONITORING_DESIGN.md` 的第一版纵向链路。模块只负责监控、警报、通知和自动恢复，不提供人工关闭、处置、行动或业务验收。

## 1. 初始化

在仓库根目录执行：

```powershell
backend\.venv\Scripts\python.exe database\bootstrap.py
```

迁移 `database/postgresql/015_monitoring_alerts.sql` 会创建规则模板、规则、游标、任务、观测、警报、证据、投递和数据源契约表。应用启动时会幂等写入三类模板，并为已有启用用户补充 `anomaly_monitoring` 数据权限。

## 2. 启动

默认 `MONITORING_WORKER_ENABLED=true`，随现有后端持久队列 Worker 一起运行：

```powershell
cd backend
.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

生产拆分进程时，可关闭 API 进程内的监控 Worker，再分别启动：

```powershell
.venv\Scripts\python.exe -m app.monitoring.worker
.venv\Scripts\python.exe -m app.monitoring.delivery_worker
```

所有任务、租约、重试、水位和投递状态均保存在 PostgreSQL；进程退出不会丢任务，租约过期后其他 Worker 可以重新认领。

## 3. 配置即时通知

站内消息不依赖外部配置。飞书或钉钉通知复用已有企业应用凭据和账号绑定：

- 飞书：`FEISHU_APP_ID`、`FEISHU_APP_SECRET`；
- 钉钉：`DINGTALK_CLIENT_ID`、`DINGTALK_CLIENT_SECRET`、`DINGTALK_AGENT_ID`；
- `PUBLIC_APP_URL` 必须是接收人设备能访问的 ForgeSight 前端地址，不能在真实移动端使用 `127.0.0.1`。

规则的 `notificationPolicy.userIds` 可限制接收用户；留空时通知所有对该数据源拥有 `anomaly_monitoring` 权限的启用用户。`channels` 可选 `inbox`、`feishu`、`dingtalk`。外部平台故障只改变投递状态，不会改变警报事实。

## 4. 使用路径

1. 管理员进入 `/admin/monitoring-rules`。
2. 选择数据断流、工序良率或非计划停机模板，填写已登记的数据源 ID 和通知用户 ID。
3. 创建草稿，先“验证”，再“发布”。未登记表或字段会被确定性拒绝。
4. 发布后等待周期调度，或点击“立即运行”。系统只查询上一个完整小时、班次或自然日。
5. 业务用户进入 `/workspace/alerts` 查看告警；详情页展示规则口径、观测趋势、样本量、数据截至时间和投递结果。
6. 指标连续满足恢复条件后，状态自动从 `firing` 进入 `recovering`，最终成为 `resolved`。Web、站内消息和即时通知使用同一个警报 ID。

## 5. 故障排查

- 管理端“最近运行”显示 `dead_letter`：修复数据源或字段登记后点击“重试”。
- 规则验证失败：检查 `DataTable.registered` 和 `TableField` 是否已完成元数据扫描与登记。
- Web 无告警：检查用户是否拥有 `business_key=anomaly_monitoring` 且覆盖对应 `source_id` 的允许策略。
- 只有站内消息、没有飞书或钉钉：检查账号绑定、企业应用凭据和规则通知渠道。
- 移动端按钮打不开：将 `PUBLIC_APP_URL` 改为手机可访问的局域网或公网 HTTPS 地址并重启 Worker。

运维接口：

- `GET /api/v1/monitoring/operations/metrics`
- `GET /api/v1/monitoring/runs`
- `GET /api/v1/monitoring/dead-letters`
- `POST /api/v1/monitoring/runs/{id}/retry`

## 6. 验证

```powershell
cd backend
.venv\Scripts\python.exe -m pytest tests/test_monitoring_service.py tests/test_monitoring_api.py -q

cd ..\frontend-new
npm test -- --run src/pages/business/AlertsPage.test.jsx src/app/navigation.test.js
npm run build
```
