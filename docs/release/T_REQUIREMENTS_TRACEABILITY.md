# T 需求追踪矩阵

<!-- markdownlint-disable MD013 -->

> 基线：`docs/tasks/REQUIREMENTS.md` 第 17 节。责任任务均为 `T集成测试与上线验收`，功能实现归属列用于定位缺陷。

| ID | 验收点 | 功能归属 | 验证层级 | 自动化或人工证据 |
| --- | --- | --- | --- | --- |
| T-EMAIL-01 | 未验证邮箱不能访问工作台 | Q/U | API、前端 | `test_auth_api.py`、`emailGate.test.jsx` |
| T-EMAIL-02 | 存量用户引导绑定邮箱 | Q/U | API、前端 | `test_auth_api.py`、`App.test.jsx` |
| T-EMAIL-03 | 任意可收信邮箱可验证 | Q | API | `test_auth_api.py`、真实 SMTP 配置检查 |
| T-EMAIL-04 | 验证码过期、频控、错误次数、一次性 | Q | 单元、API | `test_email_verification_service.py`、`test_auth_api.py` |
| T-EMAIL-05 | 不收集邮箱密码或授权码 | Q/U | 安全、人工 | 注册与验证页 375/1440 浏览器检查 |
| T-REPORT-01 | 同周期只创建一个任务 | R | 服务集成 | `test_report_automation.py` |
| T-REPORT-02 | 周报包含全部管理章节 | R/V/U | Schema、前端 | `test_report_schema.py`、`App.test.jsx` |
| T-REPORT-03 | 正常周报含业务图片、图表和两类表格 | V/U | 文件、浏览器 | `test_report_exports.py`、T07 截图 |
| T-REPORT-04 | Web/PDF 内容一致且无截断溢出 | V/U | 文件、浏览器 | `test_report_exports.py`、PDF 逐页渲染、375/1440 检查 |
| T-REPORT-05 | 结论可追溯到证据 | R/V/U | Schema、前端 | `test_report_schema.py`、`test_report_service.py` |
| T-REPORT-06 | 校验通过后 Agent 直接发布 | R | 服务集成 | `test_report_automation.py`、`test_release_e2e.py` |
| T-REPORT-07 | 数据不足生成异常说明且不伪造 | R | 单元、集成 | `test_report_automation.py`、`test_report_schema.py` |
| T-REPORT-08 | 重新生成形成不可变新版本 | R/V | 数据库、服务 | `test_reports.py`、`test_domain_migration.py` |
| T-MSG-01 | 周报发布后创建一条站内消息 | N | 端到端 | `test_release_e2e.py` |
| T-MSG-02 | 公告按接收范围创建消息 | N | API、服务 | `test_message_center_api.py`、`test_message_service.py` |
| T-MSG-03 | 支持分类、筛选和已读 | N/U | API、前端 | `test_message_center_api.py`、`App.test.jsx` |
| T-MSG-04 | 公告无需确认回执 | N/U | 契约、人工 | 消息 API Schema 与消息中心检查 |
| T-MSG-05 | 越权用户不能读取关联内容 | N/V | 安全、API | `test_message_center_api.py`、`test_report_file_access.py` |
| T-FILE-01 | 授权用户可下载 PDF/Excel | V | API、安全 | `test_report_file_access.py` |
| T-FILE-02 | 邮件含摘要、安全链接及正确 PDF | E/V | 端到端 | `test_release_e2e.py`、`test_email_delivery.py` |
| T-FILE-03 | Excel 默认不随邮件发送 | E | 服务集成 | `test_email_delivery.py` |
| T-FILE-04 | 邮件失败不影响站内内容 | E/N | 端到端 | `test_email_delivery.py`、`test_release_e2e.py` |
| T-FILE-05 | 自动重试、状态查看和人工重发 | E/U | API、前端 | `test_delivery_api.py`、`App.test.jsx` |
| T-FILE-06 | 同用户同版本不重复投递 | E | 幂等、容量 | `test_email_delivery.py`（100/1,000 接收人） |

## 判定规则

- P0 共 24 项，必须全部有证据；不能用“页面能打开”替代权限、数据库或文件断言。
- 自动化测试是合并门禁；T07 的浏览器、PDF 和 Excel 检查是发布前人工门禁。
- 旧人工复核和飞书主链路仅作兼容读取，不属于新自动周报验收路径。
