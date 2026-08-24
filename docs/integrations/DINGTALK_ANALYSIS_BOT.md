# 钉钉多轮问析机器人

## 能力与边界

钉钉企业内部应用通过 Stream 模式接收机器人消息。回调只校验身份并将消息幂等写入
平台库；后台 worker 复用 ForgeSight Analysis Session 和 Agent worker，不持有业务库
写权限。

```text
钉钉消息 → OAuth 绑定 → 数据源权限选择 → ExternalChatJob
         → 可恢复 worker → Analysis Session / Run → 钉钉主动消息 API
```

- 同一私聊持续复用一个 Analysis Session。
- 群聊按“群 + 发送人”隔离上下文，禁止不同账号共享历史。
- Agent 等待澄清时，用户下一条消息直接恢复原 Run。
- 消息 ID、Run idempotency key 和数据库唯一约束共同防止重复执行。
- 同一会话按接收顺序执行；worker 租约过期后可由其他进程重新认领。
- 未绑定、未选择数据源、账号停用或无显式数据范围时拒绝查询。
- 回复使用主动消息 API；不依赖短时有效的 `sessionWebhook`。

## 钉钉开放平台配置

1. 创建企业内部应用并启用机器人与 Stream 模式。
2. 配置 OAuth 回调地址并发布应用版本。
3. 申请读取机器人消息、读取用户基础身份以及机器人主动发送消息的权限。
4. 登录 ForgeSight 后通过绑定 API 发起 OAuth 授权，再选择当前账号获准的数据源。

绑定接口为 `/api/dingtalk/oauth/start`，数据源选择接口为
`/api/dingtalk/binding/source`。当前版本先提供稳定的服务端能力，后续可将这两个接口
挂到“账号与渠道”设置页，不与登录认证流程耦合。

## 服务端配置

复制 `backend/.env.example` 中全部 `DINGTALK_*` 配置。生产环境至少需要：

```dotenv
DINGTALK_CLIENT_ID=ding_xxx
DINGTALK_CLIENT_SECRET=请使用部署密钥
DINGTALK_REDIRECT_URI=https://example.com/api/dingtalk/oauth/callback
DINGTALK_AUTHORIZE_URL_TEMPLATE=请填写钉钉授权地址模板
DINGTALK_ROBOT_CODE=ding_xxx
PUBLIC_APP_URL=https://example.com
```

密钥只允许进入服务端环境变量或密钥管理服务。

## 数据库迁移与启动

从仓库根目录执行幂等初始化，会自动应用
`database/postgresql/013_dingtalk_reliable_chat.sql`：

```powershell
backend/.venv/Scripts/python.exe database/bootstrap.py
```

安装依赖并分别启动 API 与机器人进程：

```powershell
cd backend
.venv/Scripts/pip.exe install -r requirements.txt
.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
.venv/Scripts/python.exe scripts/run_dingtalk_bot.py
```

生产环境应由 systemd、Supervisor 或容器编排器分别守护 API 和机器人进程。机器人
进程可以多实例运行；数据库行锁、会话顺序和任务租约负责并发协调。

## 运维检查

- `external_chat_job.status` 长期处于 `retry_wait`：检查 Agent、主动消息权限和网络。
- `failed` 任务：查看 `attempt_count` 与 `error_summary`，修复后按运维流程重新入队。
- 机器人提示选择数据源：确认用户存在有效 `DataScopePolicy`，再调用数据源选择接口。
- 机器人重启：未完成的 `processing` 任务在租约到期后会被重新认领。
