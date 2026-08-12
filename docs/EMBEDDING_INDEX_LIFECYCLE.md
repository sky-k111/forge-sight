# Embedding 索引生命周期

## 索引什么

ForgeSight 的向量索引用于寻找业务语义，不用于替代业务数据库。

需要向量化：

- 已发布指标的名称、别名、定义、公式和维度；
- 已登记数据表及字段的业务说明；
- 业务对象、业务规则和分析主题；
- 后续接入企业文档知识库时，经过切分的文档片段。

不需要逐行向量化：

- 生产、质量、设备、库存等事实表中的每条业务记录；
- 数量、金额、时间等需要精确计算的字段值。

这些事实数据继续通过受控 SQL 实时查询。只有表结构、字段说明、指标口径或业务规则发生变化时，才需要更新语义索引。

## 两类索引过程

### 首次全量索引构建

也称 initial indexing、full backfill 或 bootstrap。它把现有的业务语义资产批量编码并写入 PostgreSQL pgvector：

```powershell
cd backend
python scripts/rebuild_semantic_index.py --full
```

更换 Embedding 模型、向量维度或文本拼装规则后，也必须执行全量重建。

### 后续增量索引同步

也称 incremental indexing、upsert 或 delta sync。同步程序根据 `content_hash` 只重新编码新增或发生变化的语义资产，并禁用已经删除或退回审核的资产：

```powershell
cd backend
python scripts/rebuild_semantic_index.py
```

生产环境中，知识发布、元数据扫描和数据源导入事务应写入持久化 outbox/任务表，由独立 worker 异步执行增量同步；不要让管理 API 同步等待 Embedding 接口，也不要只依赖可能丢失的进程内后台任务。

## 这不是“冷加载 / 热加载”

冷加载和热加载通常描述模型、配置或资源是否需要停机加载。这里更准确的工程术语是：

- 首次全量索引构建（全量回填）；
- 增量索引同步（事件驱动 upsert）。

产品文案可以称为“初始化索引”和“在线更新索引”，但后端日志、接口和文档应使用上面的准确术语。

## 阿里云百炼配置

`text-embedding-v4` 使用 OpenAI 兼容接口，ForgeSight 固定请求 1024 维向量。API Key 只能写入被 Git 忽略的 `backend/.env`：

```dotenv
RETRIEVAL_SEMANTIC_ENABLED=true
EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
EMBEDDING_API_KEY=填入百炼通用APIKey
EMBEDDING_MODEL=text-embedding-v4
EMBEDDING_DIMENSIONS=1024
EMBEDDING_SEND_DIMENSIONS=true
```

正式部署优先将公共端点替换为当前业务空间和地域对应的专属域名。API Key 与地域必须匹配。
