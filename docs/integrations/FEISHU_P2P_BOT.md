# 飞书单聊问析机器人接入

## 能力边界

本接入使用的是飞书**企业自建应用机器人**，不是“群自定义机器人 Webhook”。

链路如下：

```text
业务人员私聊飞书机器人
        ↓  im.message.receive_v1（长连接）
ExternalChatJob 幂等落平台库并快速确认事件
        ↓  飞书桥接 worker
ForgeSight Analysis Session / Run
        ↓  现有 Agent worker、只读 SQL、安全门禁、证据链
飞书机器人回复原单聊消息
```

机器人只接收 `p2p` 单聊。群消息在 SDK 策略层直接关闭。每位飞书用户必须
通过 `open_id` 显式绑定一个 ForgeSight 业务账号和一个数据源；未绑定用户
无法创建分析会话和 Run。

## 飞书开放平台配置

1. 在[飞书开放平台](https://open.feishu.cn/app)创建企业自建应用。
2. 在“添加应用能力”中启用机器人。
3. 在“权限管理”中至少申请：
   - 读取用户发给机器人的单聊消息：`im:message.p2p_msg:readonly`；
   - 以应用身份发消息：`im:message:send_as_bot`。
4. 在“事件与回调 / 事件配置”中选择“使用长连接接收事件”。
5. 添加事件“接收消息”，事件类型为 `im.message.receive_v1`。
6. 创建应用版本，设置可用范围并发布；权限或事件变化后需要重新发布/安装。

长连接由飞书 SDK 主动建立，不需要公网回调 URL，但运行机器必须能够访问公网。
飞书官方 Channel 快速接入说明见
[lark-channel-sdk quickstart](https://github.com/larksuite/oapi-sdk-python/blob/v2_main/doc/channel/quickstart.md)。

## ForgeSight 配置

在 `backend/.env` 增加：

```dotenv
FEISHU_BOT_ENABLED=true
FEISHU_BOT_APP_ID=cli_xxx
FEISHU_BOT_APP_SECRET=请填写应用密钥
FEISHU_BOT_BINDINGS_JSON={}
FEISHU_BOT_MAX_CONCURRENCY=4
```

首次可以保持空绑定启动。私聊机器人后，它只会返回当前用户的 `open_id`，
不会访问任何数据。然后配置绑定：

```dotenv
FEISHU_BOT_BINDINGS_JSON={"ou_xxx":{"userId":"local-test-user","sourceId":1}}
```

- `userId` 是 ForgeSight 的业务人员登录名，不是显示昵称；生产环境不建议使用
  `local-test-user`。
- `sourceId` 是该用户通过飞书问析时唯一允许使用的数据源。
- 一个飞书 `open_id` 对应一个明确的数据源，避免绕过网页端的数据源选择和隔离边界。
- App Secret 只能写入服务端 `.env` 或密钥管理服务，不能提交 Git、写进前端或截图传播。

## 启动

安装锁定依赖：

```powershell
cd backend
.venv\Scripts\pip.exe install -r requirements.txt
```

先启动 ForgeSight API（它会初始化平台表并启动 Agent worker）：

```powershell
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8002
```

再启动飞书长连接进程：

```powershell
.venv\Scripts\python.exe scripts\run_feishu_bot.py
```

生产部署时让 API 与飞书进程连接同一个 PostgreSQL 平台库，并分别交给 systemd、
Supervisor 或容器编排器保活。二者可以部署在同一台服务器，但应保持两个进程：
API 重启不会把正在接收的飞书事件与 Web 服务线程互相拖死，未处理消息则由
`external_chat_job` 在重启后重新认领。

## 当前交互

- 普通问题：创建或复用当前飞书私聊对应的 Analysis Session。
- 连续追问：沿用原会话上下文。
- Agent 主动澄清：机器人列出候选项；用户下一条文字会恢复原 Run，而不是新建 Run。
- 运行完成：后端从已落库的查询结果产物确定性生成 PNG，上传飞书并以消息卡片
  返回结论、图表、状态和 Token 用量。一次多问题分析最多展示 3 张图表。
- 当前支持折线图、面积图、柱状图、环形图、散点图、热力图和柱线组合图；
  明确要求表格或只有单值结果时不强行伪造图表。
- 图表上传或卡片发送失败会自动降级为文字回复，已经成功的 Agent Run 不会因此
  被标记为失败。
- `PUBLIC_APP_URL` 配成公网地址后，卡片才展示“查看完整图表与证据链”按钮；
  本机 `127.0.0.1` 模式不展示无效的手机跳转按钮。
- 非文本、群消息、未绑定身份：不进入 Agent 和数据库查询链路。
- 飞书事件重复投递：Channel SDK 去重 + `external_message_id` 唯一约束 + Analysis
  idempotency key 三层防重。

飞书图表和网页图表均取自同一个不可变 `result` 产物。飞书侧不会重新生成 SQL、
重新查询数据库或让 LLM 编造第二份图表数据，因此不会破坏证据链和数据源隔离。
