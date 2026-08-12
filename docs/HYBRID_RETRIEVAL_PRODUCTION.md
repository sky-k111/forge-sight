# ForgeSight 生产级混合检索设计

## 目标与边界

混合检索只负责扩大业务语义候选集合，不直接赋予 SQL 查询权限。最终进入 Agent
上下文包的指标、表、字段和关联关系，仍必须通过以下可信边界：

1. 指标必须处于“已发布”状态；
2. 指标必须在当前 `source_id` 下存在“已验证”的物理绑定；
3. 数据表必须已经登记，字段和关联路径必须来自元数据扫描结果；
4. SQL 网关继续执行只读、白名单、EXPLAIN 成本门控与结果行数限制。

因此，即使向量索引错误命中其他租户、停用指标或不存在的表，它们也不会进入
`executionPolicy.allowedTables`。

## 生产链路

```text
用户问题
  ├─ 现有规则/词法召回（精确指标名、编码、字段名、业务同义词）
  └─ pgvector 语义召回（自然表达、行业口语、释义改写）
          ↓
按 metric / table / business_object / business_rule 分池
          ↓
RRF 按名次融合（不直接混加异构分数）
          ↓
source_id + 发布/验证状态 + 注册元数据可信过滤
          ↓
最小化上下文包与 SQL 执行白名单
```

## 为什么选择这套方案

- PostgreSQL 已是平台库，Compose 也已使用 pgvector 镜像，无需额外引入独立向量数据库；
- RRF 对不同 embedding 模型的分数分布不敏感，便于后续更换模型；
- 规则检索保留产品编码、字段名等精确匹配能力，向量服务不可用时可自动降级；
- 数据源过滤在 SQL 查询阶段下推，不依赖应用层事后剔除；
- 索引按 `content_hash` 增量更新，删除、退回审核或停用的知识会被禁用。
- Query embedding 每次请求只生成一次；服务故障触发短时熔断，立即降级为规则检索，
  避免每个 Agent 任务重复等待外部模型超时。

## 启用步骤

1. 部署 OpenAI-compatible Embeddings 服务，例如支持 `BAAI/bge-m3` 的 Xinference、
   vLLM 或企业已有模型网关；不要在每个 FastAPI Worker 内加载一份大模型。
2. 在 `backend/.env` 配置：

```dotenv
RETRIEVAL_SEMANTIC_ENABLED=true
EMBEDDING_BASE_URL=http://embedding-service:9997/v1
EMBEDDING_API_KEY=
EMBEDDING_MODEL=BAAI/bge-m3
EMBEDDING_DIMENSIONS=1024
```

3. 在 `backend/` 目录执行索引任务：

```powershell
python scripts/rebuild_semantic_index.py
```

4. 在知识发布、退回审核、元数据扫描完成后，触发同一增量索引命令。生产环境应由
   独立 Job/队列消费变更事件，而不是阻塞知识管理 API。

## 上线验收

至少维护一组制造业真实问法，并持续统计：

- 指标 Recall@5、Top-1 Accuracy；
- 表/字段召回率与跨数据源泄漏数（必须为 0）；
- 无答案误命中率、需要澄清问题的识别率；
- SQL 生成成功率、执行成功率和最终业务答案正确率；
- lexical-only、semantic-only、hybrid 三组离线对比；
- P50/P95 检索延迟、embedding 错误率与降级率。

不要仅凭单条问题的余弦相似度设置固定阈值。阈值、TopK 和模型必须以这组评测集
标定，并按数据源和对象类型观测召回质量。

## GitHub 调研依据

- [pgvector/pgvector](https://github.com/pgvector/pgvector)：官方建议将 PostgreSQL
  全文检索与向量检索组合，并使用 RRF 或 Cross-Encoder 融合；同时指出带过滤条件
  的 HNSW 检索应关注 iterative scan、分区与租户过滤的召回影响。
- [pgvector/pgvector-python](https://github.com/pgvector/pgvector-python)：提供
  SQLAlchemy 向量类型、余弦距离、HNSW/IVFFlat 与候选集二次精排的标准实现。
- [infiniflow/ragflow](https://github.com/infiniflow/ragflow)：其生产 RAG 设计明确
  使用多路召回、融合重排和 metadata filtering，说明单独向量检索会漏掉产品编码、
  法规编号等精确文本。
- [qdrant/qdrant](https://github.com/qdrant/qdrant)：原生混合检索将 dense、sparse
  通道并行，并使用 RRF/DBSF 融合，验证了“并行召回而非失败后串行 fallback”的路径。
- [FlagOpen/FlagEmbedding](https://github.com/FlagOpen/FlagEmbedding)：BGE-M3 同时支持
  dense、lexical 和 multi-vector，但模型更强不等于可以跳过业务评测与权限过滤；其
  reranker 也应只在离线评测证明收益大于延迟成本后启用。
- [langgenius/dify](https://github.com/langgenius/dify)：知识库实践暴露了阈值为空、
  metadata filter 和混合检索实现差异造成的召回问题，因此 ForgeSight 将模型、维度、
  阈值、TopK、降级状态全部显式配置并返回到检索元数据中。
