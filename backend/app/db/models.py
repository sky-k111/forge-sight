"""平台库 ORM 模型。所有表都在 forge_sight_app，业务库 forge_sight 无 ORM（只读裸 SQL）。"""

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.platform import Base

# PostgreSQL 使用可索引的 JSONB，测试环境 SQLite 退化为标准 JSON。
JSON_TYPE = JSON().with_variant(JSONB, "postgresql")


class AuditLog(Base):
    """审计日志：谁在什么时间对什么资源做了什么，结果如何。"""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, comment="发生时间")
    user_name: Mapped[str] = mapped_column(String(50), default="演示用户", comment="操作人")
    role: Mapped[str] = mapped_column(String(20), default="业务人员", comment="角色")
    action: Mapped[str] = mapped_column(String(255), comment="执行动作，如 查询工序良率 / DELETE FROM ...")
    resource: Mapped[str] = mapped_column(String(255), default="", comment="涉及数据资源")
    result: Mapped[str] = mapped_column(String(20), comment="允许 / 已拦截 / 已记录")
    detail: Mapped[str] = mapped_column(Text, default="", comment="详情（SQL 摘要、拦截原因等）")


# 结果 → 前端展示的 tone（与前端 StatusPill 对齐）
RESULT_TONES = {"允许": "green", "已拦截": "red", "已记录": "blue"}


class DataSource(Base):
    """数据源连接（当前支持 mysql / postgresql）。密码为演示环境明文存储，生产必须加密。"""

    __tablename__ = "data_source"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), comment="数据源名称，如 华东制造数据底座")
    type: Mapped[str] = mapped_column(String(20), comment="mysql / postgresql")
    host: Mapped[str] = mapped_column(String(100), default="127.0.0.1")
    port: Mapped[int] = mapped_column(Integer, default=3306)
    db_name: Mapped[str] = mapped_column(String(64), comment="数据库名")
    username: Mapped[str] = mapped_column(String(64), comment="只读账号")
    password: Mapped[str] = mapped_column(String(128), default="")
    status: Mapped[str] = mapped_column(String(20), default="未测试", comment="未测试 / 连接正常 / 连接失败")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, comment="默认数据源")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class SemanticIndexJob(Base):
    """Durable semantic-index synchronization task for one isolated data source."""

    __tablename__ = "semantic_index_job"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_id: Mapped[int] = mapped_column(Integer, index=True)
    mode: Mapped[str] = mapped_column(String(20), default="incremental", comment="incremental / full")
    reason: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    error: Mapped[str] = mapped_column(Text, default="")
    result_json: Mapped[dict] = mapped_column(JSON_TYPE, default=dict)
    embedding_model: Mapped[str] = mapped_column(String(160), default="")
    embedding_dimensions: Mapped[int] = mapped_column(Integer, default=0)
    requested_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)
    lease_owner: Mapped[str | None] = mapped_column(String(160), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)


class DataTable(Base):
    """已登记的数据表（资源目录）。业务库真实结构来自扫描，中文语义可人工修正。"""

    __tablename__ = "data_table"
    __table_args__ = (UniqueConstraint("source_id", "name", name="uq_source_table"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[int] = mapped_column(Integer, default=1, index=True, comment="所属数据源")
    name: Mapped[str] = mapped_column(String(64), comment="物理表名")
    cn_name: Mapped[str] = mapped_column(String(100), default="", comment="中文名")
    topic: Mapped[str] = mapped_column(String(20), default="", comment="业务主题：生产/质量/设备/库存/主数据")
    row_count: Mapped[int] = mapped_column(Integer, default=0, comment="行数")
    primary_key: Mapped[str] = mapped_column(String(64), default="", comment="主键字段")
    comment: Mapped[str] = mapped_column(String(255), default="", comment="表注释")
    data_updated: Mapped[str] = mapped_column(String(20), default="", comment="数据更新至（按最新日期字段推断）")
    registered: Mapped[bool] = mapped_column(Boolean, default=True, comment="是否已确认登记（新表 AI 推荐后为 False）")
    scanned_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, comment="最近扫描时间")


class TableField(Base):
    """已登记表的字段信息。"""

    __tablename__ = "table_field"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[int] = mapped_column(Integer, default=1, index=True, comment="所属数据源")
    table_name: Mapped[str] = mapped_column(String(64), index=True, comment="所属表")
    name: Mapped[str] = mapped_column(String(64), comment="字段名")
    cn_name: Mapped[str] = mapped_column(String(100), default="", comment="中文名（来自字段注释，可修正）")
    data_type: Mapped[str] = mapped_column(String(40), default="", comment="数据类型")
    ordinal: Mapped[int] = mapped_column(Integer, default=0, comment="字段顺序")
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否主键")
    ref_table: Mapped[str] = mapped_column(String(64), default="", comment="外键指向的表")
    comment: Mapped[str] = mapped_column(String(255), default="", comment="字段注释")
    sample_value: Mapped[str] = mapped_column(String(100), default="", comment="样例值")


class TableRelation(Base):
    """表间关联关系（外键扫描 + 命名约定推导）。"""

    __tablename__ = "table_relation"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[int] = mapped_column(Integer, default=1, index=True, comment="所属数据源")
    from_table: Mapped[str] = mapped_column(String(64), comment="来源表")
    from_field: Mapped[str] = mapped_column(String(64), comment="来源字段")
    to_table: Mapped[str] = mapped_column(String(64), comment="目标表")
    to_field: Mapped[str] = mapped_column(String(64), comment="目标字段")
    source: Mapped[str] = mapped_column(String(20), default="扫描", comment="来源：扫描 / 推导 / 人工")


class KnowledgeTopic(Base):
    """分析主题：组织指标、对象和规则的顶层业务分类。"""

    __tablename__ = "knowledge_topic"

    code: Mapped[str] = mapped_column(String(20), primary_key=True, comment="稳定编码，如 QUAL")
    name: Mapped[str] = mapped_column(String(50), unique=True, comment="主题名称")
    color: Mapped[str] = mapped_column(String(20), default="blue")
    description: Mapped[str] = mapped_column(String(255), default="")
    coverage: Mapped[int] = mapped_column(Integer, default=0, comment="语义覆盖率，仅用于治理展示")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class BusinessObject(Base):
    """业务对象：产品、工单、工序、设备等企业语义实体。"""

    __tablename__ = "business_object"

    id: Mapped[str] = mapped_column(String(50), primary_key=True, comment="稳定英文标识")
    name: Mapped[str] = mapped_column(String(80), unique=True)
    topic: Mapped[str] = mapped_column(String(50), default="全局")
    description: Mapped[str] = mapped_column(String(255), default="")
    relation_count: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class BusinessRule(Base):
    """业务规则：指标计算、数据校验、异常识别与查询约束。"""

    __tablename__ = "business_rule"

    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    name: Mapped[str] = mapped_column(String(180), unique=True)
    rule_type: Mapped[str] = mapped_column(String(30), default="业务规则")
    scope: Mapped[str] = mapped_column(String(50), default="全局")
    content: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class KnowledgeMetric(Base):
    """企业统一指标语义；这里只描述业务含义，不出现某个数据源的物理字段。"""

    __tablename__ = "knowledge_metric"

    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    topic: Mapped[str] = mapped_column(String(50))
    definition: Mapped[str] = mapped_column(Text, default="")
    formula: Mapped[str] = mapped_column(Text, comment="业务语义公式，如 SUM(合格数量)/SUM(投入数量)")
    synonyms_json: Mapped[str] = mapped_column(Text, default="[]")
    dimensions_json: Mapped[str] = mapped_column(Text, default="[]")
    time_field: Mapped[str] = mapped_column(String(64), default="统计时间", comment="统一语义时间维度")
    precision: Mapped[str] = mapped_column(String(50), default="")
    owner: Mapped[str] = mapped_column(String(50), default="数据治理组")
    status: Mapped[str] = mapped_column(String(20), default="待审核", comment="待审核 / 已发布")
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class MetricSourceBinding(Base):
    """指标在单个数据源上的物理实现；同一指标的各数据源绑定严格隔离。"""

    __tablename__ = "metric_source_binding"
    __table_args__ = (UniqueConstraint("metric_id", "source_id", name="uq_metric_source"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    metric_id: Mapped[str] = mapped_column(String(20), index=True)
    source_id: Mapped[int] = mapped_column(Integer, index=True)
    physical_formula: Mapped[str] = mapped_column(Text, comment="针对该数据源的可执行 SQL 表达式")
    time_field: Mapped[str] = mapped_column(String(64), default="")
    filter_expression: Mapped[str] = mapped_column(Text, default="")
    sql_dialect: Mapped[str] = mapped_column(String(20), default="mysql")
    status: Mapped[str] = mapped_column(String(20), default="待验证", comment="待验证 / 已验证 / 已停用")
    version: Mapped[int] = mapped_column(Integer, default=1)
    notes: Mapped[str] = mapped_column(Text, default="")
    validated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class MetricTableBinding(Base):
    """单个数据源指标绑定所使用的物理表。"""

    __tablename__ = "metric_table_binding"
    __table_args__ = (UniqueConstraint("binding_id", "table_name", name="uq_metric_binding_table"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    binding_id: Mapped[int] = mapped_column(Integer, index=True)
    table_name: Mapped[str] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(30), default="事实表", comment="事实表 / 维表 / 明细表")


class MetricFieldBinding(Base):
    """统一语义字段到单个数据源物理字段的映射。"""

    __tablename__ = "metric_field_binding"
    __table_args__ = (UniqueConstraint("binding_id", "semantic_field", name="uq_metric_binding_semantic_field"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    binding_id: Mapped[int] = mapped_column(Integer, index=True)
    semantic_field: Mapped[str] = mapped_column(String(80), comment="如 合格数量、投入数量、统计时间")
    table_name: Mapped[str] = mapped_column(String(64))
    field_name: Mapped[str] = mapped_column(String(64))
    transform_expression: Mapped[str] = mapped_column(Text, default="", comment="单位换算或枚举归一表达式")


class KnowledgeReference(Base):
    """知识引用索引，用于阻止删除仍被分析模板、任务或其他知识项引用的内容。"""

    __tablename__ = "knowledge_reference"
    __table_args__ = (
        UniqueConstraint(
            "entity_type", "entity_id", "consumer_type", "consumer_id", name="uq_knowledge_reference"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entity_type: Mapped[str] = mapped_column(String(30), index=True, comment="topic / object / rule / metric")
    entity_id: Mapped[str] = mapped_column(String(50), index=True)
    consumer_type: Mapped[str] = mapped_column(String(30), comment="metric / analysis_template / report_template")
    consumer_id: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(String(255), default="")


class AnalysisSession(Base):
    """连续业务分析会话；分支会创建新会话，不改写父会话。"""

    __tablename__ = "analysis_session"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    title: Mapped[str] = mapped_column(String(200), default="新分析")
    user_id: Mapped[str] = mapped_column(String(64), index=True, default="local-test-user")
    source_id: Mapped[int] = mapped_column(Integer, index=True)
    status: Mapped[str] = mapped_column(String(20), default="active")
    parent_session_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    fork_checkpoint_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class AnalysisMessage(Base):
    """会话消息。流式增量由事件承载，这里保存最终可恢复内容。"""

    __tablename__ = "analysis_message"
    __table_args__ = (UniqueConstraint("session_id", "sequence_no", name="uq_analysis_message_seq"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(40), index=True)
    run_id: Mapped[str | None] = mapped_column(String(40), index=True, nullable=True)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="completed")
    sequence_no: Mapped[int] = mapped_column(Integer)
    metadata_json: Mapped[dict] = mapped_column(JSON_TYPE, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class AnalysisRun(Base):
    """一次用户提问触发的完整 Agent 运行及其用量聚合。"""

    __tablename__ = "analysis_run"
    __table_args__ = (UniqueConstraint("session_id", "idempotency_key", name="uq_analysis_run_idempotency"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(40), index=True)
    trigger_message_id: Mapped[str] = mapped_column(String(40))
    idempotency_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    source_id: Mapped[int] = mapped_column(Integer, index=True)
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    current_step_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    workflow_version: Mapped[str] = mapped_column(String(32), default="analysis-v1")
    context_version: Mapped[int] = mapped_column(Integer, default=1)
    last_event_seq: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    llm_call_count: Mapped[int] = mapped_column(Integer, default=0)
    retry_call_count: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    output_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    total_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    retry_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    estimated_cost: Mapped[float] = mapped_column(Numeric(18, 8), default=0)
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    budget_snapshot_json: Mapped[dict] = mapped_column(JSON_TYPE, default=dict)
    skill_enhancement_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    skill_experiment_variant: Mapped[str] = mapped_column(String(16), default="off")
    lease_owner: Mapped[str | None] = mapped_column(String(100), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class AnalysisTask(Base):
    """健壮意图层从一个 Run 拆出的独立或依赖分析问题。"""

    __tablename__ = "analysis_task"
    __table_args__ = (UniqueConstraint("run_id", "task_order", name="uq_analysis_task_order"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(40), index=True)
    task_order: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(200))
    normalized_question: Mapped[str] = mapped_column(Text)
    mode: Mapped[str] = mapped_column(String(24), default="single")
    analysis_type: Mapped[str] = mapped_column(String(32), default="query")
    status: Mapped[str] = mapped_column(String(24), default="pending")
    depends_on_json: Mapped[list] = mapped_column(JSON_TYPE, default=list)
    intent_json: Mapped[dict] = mapped_column(JSON_TYPE, default=dict)
    assumptions_json: Mapped[list] = mapped_column(JSON_TYPE, default=list)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AnalysisRunStep(Base):
    """某个稳定步骤的一次尝试；失败尝试不可覆盖。"""

    __tablename__ = "analysis_run_step"
    __table_args__ = (
        UniqueConstraint("run_id", "task_scope", "step_key", "attempt", name="uq_analysis_step_attempt"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(40), index=True)
    task_id: Mapped[str | None] = mapped_column(String(40), index=True, nullable=True)
    task_scope: Mapped[str] = mapped_column(String(40), default="run", comment="空 task 的稳定唯一占位")
    step_key: Mapped[str] = mapped_column(String(64))
    step_order: Mapped[int] = mapped_column(Integer)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(24), default="pending")
    label: Mapped[str] = mapped_column(String(200), default="")
    detail: Mapped[str] = mapped_column(Text, default="")
    input_refs_json: Mapped[list] = mapped_column(JSON_TYPE, default=list)
    output_refs_json: Mapped[list] = mapped_column(JSON_TYPE, default=list)
    error_json: Mapped[dict | None] = mapped_column(JSON_TYPE, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AnalysisEvent(Base):
    """Run 内 append-only 领域事件，是实时与历史重放的共同事实源。"""

    __tablename__ = "analysis_event"
    __table_args__ = (UniqueConstraint("run_id", "seq", name="uq_analysis_event_seq"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(40), index=True)
    run_id: Mapped[str] = mapped_column(String(40), index=True)
    task_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    seq: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    step_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attempt: Mapped[int | None] = mapped_column(Integer, nullable=True)
    visibility: Mapped[str] = mapped_column(String(16), default="user")
    replayable: Mapped[bool] = mapped_column(Boolean, default=True)
    payload_json: Mapped[dict] = mapped_column(JSON_TYPE, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class AnalysisArtifact(Base):
    """SQL、上下文、结果、图表和结论等不可变产物。"""

    __tablename__ = "analysis_artifact"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(40), index=True)
    task_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    step_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    type: Mapped[str] = mapped_column(String(32), index=True)
    schema_version: Mapped[str] = mapped_column(String(16), default="1.0")
    content_json: Mapped[dict | list | None] = mapped_column(JSON_TYPE, nullable=True)
    storage_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_json: Mapped[dict] = mapped_column(JSON_TYPE, default=dict)
    checksum: Mapped[str] = mapped_column(String(64))
    source_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class AnalysisCheckpoint(Base):
    """业务可见检查点投影；完整执行状态由 LangGraph Checkpointer 管理。"""

    __tablename__ = "analysis_checkpoint"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(40), index=True)
    run_id: Mapped[str] = mapped_column(String(40), index=True)
    step_key: Mapped[str] = mapped_column(String(64))
    event_seq: Mapped[int] = mapped_column(Integer)
    parent_checkpoint_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    workflow_version: Mapped[str] = mapped_column(String(32))
    state_schema_version: Mapped[int] = mapped_column(Integer, default=1)
    langgraph_thread_id: Mapped[str] = mapped_column(String(80))
    langgraph_checkpoint_ns: Mapped[str] = mapped_column(String(80), default="")
    langgraph_checkpoint_id: Mapped[str] = mapped_column(String(100))
    state_summary_json: Mapped[dict] = mapped_column(JSON_TYPE, default=dict)
    artifact_refs_json: Mapped[list] = mapped_column(JSON_TYPE, default=list)
    rollbackable: Mapped[bool] = mapped_column(Boolean, default=False)
    checksum: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class AnalysisInteraction(Base):
    """澄清等 interrupt/resume 人机交互。"""

    __tablename__ = "analysis_interaction"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(40), index=True)
    task_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    kind: Mapped[str] = mapped_column(String(24), default="clarification")
    status: Mapped[str] = mapped_column(String(24), default="pending")
    prompt_json: Mapped[dict] = mapped_column(JSON_TYPE, default=dict)
    response_json: Mapped[dict | None] = mapped_column(JSON_TYPE, nullable=True)
    resume_node: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AnalysisBranch(Base):
    """父子会话和分支起点审计记录。"""

    __tablename__ = "analysis_branch"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    parent_session_id: Mapped[str] = mapped_column(String(40), index=True)
    child_session_id: Mapped[str] = mapped_column(String(40), unique=True)
    fork_checkpoint_id: Mapped[str] = mapped_column(String(40))
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class ExternalChatConversation(Base):
    """外部会话与 ForgeSight 分析会话的稳定映射。

    当前由飞书单聊机器人使用；保留 channel 字段，后续接钉钉/企微时不需要
    再复制一套会话模型。外部渠道只能绑定到一个明确的用户与数据源，禁止
    退化成无身份、可遍历全部数据源的公共入口。
    """

    __tablename__ = "external_chat_conversation"
    __table_args__ = (
        UniqueConstraint(
            "channel", "tenant_key", "external_chat_id", name="uq_external_chat_conversation"
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    channel: Mapped[str] = mapped_column(String(24), index=True)
    tenant_key: Mapped[str] = mapped_column(String(80), default="", index=True)
    external_chat_id: Mapped[str] = mapped_column(String(120), index=True)
    external_user_id: Mapped[str] = mapped_column(String(120), index=True)
    platform_user_id: Mapped[str] = mapped_column(String(64), index=True)
    source_id: Mapped[int] = mapped_column(Integer, index=True)
    analysis_session_id: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(20), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class PlatformFeatureFlag(Base):
    """可审计的平台级能力开关；运行创建时将当前值快照到 AnalysisRun。"""

    __tablename__ = "platform_feature_flag"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    config_json: Mapped[dict] = mapped_column(JSON_TYPE, default=dict)
    updated_by: Mapped[str] = mapped_column(String(64), default="system")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class AnalysisSkill(Base):
    """受治理的程序性分析技能：描述怎么分析，不授予任何数据访问权限。"""

    __tablename__ = "analysis_skill"
    __table_args__ = (
        UniqueConstraint("slug", "source_id", "version", name="uq_analysis_skill_version"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    source_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(160))
    slug: Mapped[str] = mapped_column(String(120), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    topic: Mapped[str] = mapped_column(String(50), default="通用分析")
    status: Mapped[str] = mapped_column(String(20), default="candidate", index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    trigger_json: Mapped[dict] = mapped_column(JSON_TYPE, default=dict)
    preconditions_json: Mapped[list] = mapped_column(JSON_TYPE, default=list)
    procedure_json: Mapped[list] = mapped_column(JSON_TYPE, default=list)
    output_policy_json: Mapped[dict] = mapped_column(JSON_TYPE, default=dict)
    constraints_json: Mapped[list] = mapped_column(JSON_TYPE, default=list)
    evidence_run_ids_json: Mapped[list] = mapped_column(JSON_TYPE, default=list)
    confidence: Mapped[float] = mapped_column(Numeric(5, 4), default=0)
    created_by: Mapped[str] = mapped_column(String(64), default="system")
    reviewed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class LearningFeedback(Base):
    """用户对分析结果的显式反馈，是候选技能生成的主要证据。"""

    __tablename__ = "learning_feedback"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(40), index=True)
    user_id: Mapped[str] = mapped_column(String(64), default="local-test-user")
    rating: Mapped[str] = mapped_column(String(24))
    correction_type: Mapped[str] = mapped_column(String(40), default="")
    comment: Mapped[str] = mapped_column(Text, default="")
    candidate_skill_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class AnalysisSkillUsage(Base):
    """技能命中和结果快照，用于比较开关前后的成功率、耗时与成本。"""

    __tablename__ = "analysis_skill_usage"
    __table_args__ = (
        UniqueConstraint("run_id", "task_id", "skill_id", name="uq_analysis_skill_usage"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(40), index=True)
    task_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    skill_id: Mapped[str] = mapped_column(String(40), index=True)
    variant: Mapped[str] = mapped_column(String(16), default="on")
    relevance_score: Mapped[float] = mapped_column(Numeric(8, 6), default=0)
    applied: Mapped[bool] = mapped_column(Boolean, default=True)
    outcome: Mapped[str | None] = mapped_column(String(24), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ExternalChatJob(Base):
    """外部消息的持久化收件箱与投递状态。

    飞书要求事件处理快速返回，不能在事件回调内等待 Agent。消息先以
    external_message_id 幂等落库，再由机器人进程异步执行和回复；进程重启后
    未完成任务仍可重新认领。
    """

    __tablename__ = "external_chat_job"
    __table_args__ = (
        UniqueConstraint("channel", "external_message_id", name="uq_external_chat_message"),
        CheckConstraint("attempt_count >= 0", name="ck_external_chat_attempt_count"),
        Index("ix_external_chat_job_claim", "channel", "status", "lease_expires_at"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    channel: Mapped[str] = mapped_column(String(24), index=True)
    tenant_key: Mapped[str] = mapped_column(String(80), default="", index=True)
    external_message_id: Mapped[str] = mapped_column(String(120))
    external_chat_id: Mapped[str] = mapped_column(String(120), index=True)
    external_user_id: Mapped[str] = mapped_column(String(120), index=True)
    question: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    conversation_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    analysis_session_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    run_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    ack_message_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    result_message_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    result_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    lease_owner: Mapped[str | None] = mapped_column(String(100), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error_summary: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ReportTemplate(Base):
    """报告模板只描述业务问题，不绑定具体企业的物理表字段。"""

    __tablename__ = "report_template"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    report_type: Mapped[str] = mapped_column(String(24), default="weekly")
    topic: Mapped[str] = mapped_column(String(40), default="综合")
    audience: Mapped[str] = mapped_column(String(120), default="业务负责人")
    description: Mapped[str] = mapped_column(String(255), default="")
    sections_json: Mapped[list] = mapped_column(JSON_TYPE, default=list)
    min_coverage: Mapped[int] = mapped_column(Integer, default=60)
    version: Mapped[int] = mapped_column(Integer, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class Report(Base):
    """报告业务身份；具体正文冻结在 ReportVersion 中。"""

    __tablename__ = "report"
    __table_args__ = (
        UniqueConstraint(
            "subscription_id",
            "schedule_key",
            "audience_scope_hash",
            name="uq_report_schedule_audience",
        ),
        Index("ix_report_period_template", "period_start", "period_end", "template_id"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    template_id: Mapped[str] = mapped_column(String(40), index=True)
    source_id: Mapped[int] = mapped_column(Integer, index=True)
    report_type: Mapped[str] = mapped_column(String(24), default="weekly")
    topic: Mapped[str] = mapped_column(String(40), default="综合")
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    owner_id: Mapped[str] = mapped_column(String(64), default="local-test-user", index=True)
    reviewer_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    subscription_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    schedule_key: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    audience_scope_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    publish_source: Mapped[str] = mapped_column(String(32), default="manual")
    period_start: Mapped[datetime] = mapped_column(DateTime)
    period_end: Mapped[datetime] = mapped_column(DateTime)
    scope_json: Mapped[dict] = mapped_column(JSON_TYPE, default=dict)
    current_version_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    analysis_session_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    analysis_run_id: Mapped[str | None] = mapped_column(String(40), index=True, nullable=True)
    error_json: Mapped[dict | None] = mapped_column(JSON_TYPE, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class ReportVersion(Base):
    """不可变报告版本；发布后不允许被重新生成覆盖。"""

    __tablename__ = "report_version"
    __table_args__ = (UniqueConstraint("report_id", "version", name="uq_report_version"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    report_id: Mapped[str] = mapped_column(String(40), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(24), default="draft")
    supersedes_version_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("report_version.id", ondelete="RESTRICT"), nullable=True
    )
    summary: Mapped[str] = mapped_column(Text, default="")
    content_json: Mapped[dict] = mapped_column(JSON_TYPE, default=dict)
    data_snapshot_json: Mapped[dict] = mapped_column(JSON_TYPE, default=dict)
    validation_json: Mapped[dict] = mapped_column(JSON_TYPE, default=dict)
    schema_version: Mapped[str] = mapped_column(String(32), default="weekly-report/v1")
    content_checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    confidence_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confidence_algorithm_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    workflow_version: Mapped[str] = mapped_column(String(64), default="")
    model_snapshot_json: Mapped[dict] = mapped_column(JSON_TYPE, default=dict)
    data_quality_json: Mapped[dict] = mapped_column(JSON_TYPE, default=dict)
    created_by: Mapped[str] = mapped_column(String(64), default="report-agent")
    approved_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ReportFile(Base):
    """由某个不可变报告版本生成的下载文件。"""

    __tablename__ = "report_file"
    __table_args__ = (
        UniqueConstraint("version_id", "format", name="uq_report_file_version_format"),
        CheckConstraint("format IN ('pdf', 'xlsx')", name="ck_report_file_format"),
        CheckConstraint(
            "status IN ('queued', 'generating', 'ready', 'failed', 'dead_letter')",
            name="ck_report_file_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    report_id: Mapped[str] = mapped_column(String(40), index=True)
    version_id: Mapped[str] = mapped_column(String(40), index=True)
    format: Mapped[str] = mapped_column(String(12))
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    storage_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    file_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    checksum: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_content_checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    generation_attempt: Mapped[int] = mapped_column(Integer, default=0)
    error_json: Mapped[dict | None] = mapped_column(JSON_TYPE, nullable=True)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    lease_owner: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ReportSection(Base):
    """报告章节与阶段五任务、Artifact 之间的可追溯绑定。"""

    __tablename__ = "report_section"
    __table_args__ = (UniqueConstraint("version_id", "section_order", name="uq_report_section_order"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    report_id: Mapped[str] = mapped_column(String(40), index=True)
    version_id: Mapped[str] = mapped_column(String(40), index=True)
    code: Mapped[str] = mapped_column(String(48))
    title: Mapped[str] = mapped_column(String(160))
    section_order: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), default="pending")
    summary: Mapped[str] = mapped_column(Text, default="")
    content_json: Mapped[dict] = mapped_column(JSON_TYPE, default=dict)
    confidence: Mapped[int] = mapped_column(Integer, default=0)
    analysis_task_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    artifact_ids_json: Mapped[list] = mapped_column(JSON_TYPE, default=list)
    error_json: Mapped[dict | None] = mapped_column(JSON_TYPE, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class Message(Base):
    """站内统一消息；周报消息固定指向发布时的版本。"""

    __tablename__ = "message"
    __table_args__ = (
        CheckConstraint(
            "status IN ('published', 'withdrawn', 'expired')",
            name="ck_message_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    message_type: Mapped[str] = mapped_column(String(32), index=True)
    source: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(String(200))
    summary: Mapped[str] = mapped_column(Text, default="")
    related_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    related_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    report_version_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    priority: Mapped[str] = mapped_column(String(16), default="normal")
    is_pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(24), default="published", index=True)
    idempotency_key: Mapped[str] = mapped_column(String(160), unique=True)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    published_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class MessageRecipient(Base):
    """每个用户的消息可见性与已读状态。"""

    __tablename__ = "message_recipient"
    __table_args__ = (
        UniqueConstraint("message_id", "user_id", name="uq_message_recipient_user"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    message_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("message.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[str] = mapped_column(String(40), index=True)
    message_status_snapshot: Mapped[str] = mapped_column(String(24), default="published")
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class Announcement(Base):
    """管理员公告正文与接收范围快照。"""

    __tablename__ = "announcement"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'published', 'withdrawn', 'expired')",
            name="ck_announcement_status",
        ),
        UniqueConstraint("id", "revision", name="uq_announcement_revision"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    supersedes_announcement_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    priority: Mapped[str] = mapped_column(String(16), default="normal")
    is_pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    audience_json: Mapped[dict] = mapped_column(JSON_TYPE, default=dict)
    audience_scope_hash: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(24), default="draft", index=True)
    created_by: Mapped[str] = mapped_column(String(40))
    published_by: Mapped[str | None] = mapped_column(String(40), nullable=True)
    withdrawn_by: Mapped[str | None] = mapped_column(String(40), nullable=True)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    withdrawal_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)


class ReportSubscription(Base):
    """周期报告订阅；历史飞书值仍保持可读。"""

    __tablename__ = "report_subscription"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    template_id: Mapped[str] = mapped_column(String(40), index=True)
    source_id: Mapped[int] = mapped_column(Integer, index=True)
    owner_id: Mapped[str] = mapped_column(String(64), default="local-test-user", index=True)
    cadence: Mapped[str] = mapped_column(String(20), default="weekly")
    weekday: Mapped[int | None] = mapped_column(Integer, nullable=True)
    day_of_month: Mapped[int | None] = mapped_column(Integer, nullable=True)
    send_time: Mapped[str] = mapped_column(String(8), default="10:00")
    timezone: Mapped[str] = mapped_column(String(50), default="Asia/Shanghai")
    channel: Mapped[str] = mapped_column(String(24), default="email")
    publish_policy: Mapped[str] = mapped_column(String(24), default="auto_publish")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class ReportDelivery(Base):
    """报告每一次外部发送的结果与重试记录。"""

    __tablename__ = "report_delivery"
    __table_args__ = (
        UniqueConstraint(
            "message_id",
            "recipient_user_id",
            "channel",
            name="uq_report_delivery_message_user_channel",
        ),
        UniqueConstraint(
            "version_id",
            "recipient_user_id",
            "channel",
            name="uq_report_delivery_version_user_channel",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_report_delivery_attempt_count"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    message_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    report_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    version_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    subscription_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    channel: Mapped[str] = mapped_column(String(24), default="feishu")
    status: Mapped[str] = mapped_column(String(24), default="queued")
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    recipient_user_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    recipient_address_snapshot: Mapped[str | None] = mapped_column(String(254), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(160), nullable=True, unique=True)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    error_category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    attachment_file_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    attachment_omitted: Mapped[bool] = mapped_column(Boolean, default=False)
    lease_owner: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    last_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    error_json: Mapped[dict | None] = mapped_column(JSON_TYPE, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class DeliveryAttempt(Base):
    """一次服务商发送尝试；业务投递可包含多次尝试。"""

    __tablename__ = "delivery_attempt"
    __table_args__ = (
        UniqueConstraint("delivery_id", "attempt_number", name="uq_delivery_attempt_number"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    delivery_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("report_delivery.id", ondelete="CASCADE"), index=True
    )
    attempt_number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24))
    provider_message_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    error_category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_json: Mapped[dict | None] = mapped_column(JSON_TYPE, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class LlmCallAttempt(Base):
    """一次真实模型请求的用量、费用和重试关系。"""

    __tablename__ = "llm_call_attempt"
    __table_args__ = (
        UniqueConstraint("step_scope", "attempt", name="uq_llm_call_step_attempt"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(40), index=True)
    task_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    step_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    step_scope: Mapped[str] = mapped_column(String(120), comment="run/task/step 的稳定组合")
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    retry_of_call_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    provider: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(100))
    model_profile_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider_request_id: Mapped[str | None] = mapped_column(String(120), nullable=True, unique=True)
    input_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    cached_input_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    reasoning_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    context_window: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    context_remaining: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="started")
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    estimated_cost: Mapped[float] = mapped_column(Numeric(18, 8), default=0)
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    price_snapshot_json: Mapped[dict] = mapped_column(JSON_TYPE, default=dict)
    usage_raw_json: Mapped[dict] = mapped_column(JSON_TYPE, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class LlmModelPrice(Base):
    """可版本化模型价格；历史调用会复制价格快照。"""

    __tablename__ = "llm_model_price"
    __table_args__ = (
        UniqueConstraint("provider", "model", "version", name="uq_llm_price_version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(40), index=True)
    model: Mapped[str] = mapped_column(String(100), index=True)
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    input_price: Mapped[float] = mapped_column(Numeric(18, 8), default=0)
    cached_input_price: Mapped[float] = mapped_column(Numeric(18, 8), default=0)
    output_price: Mapped[float] = mapped_column(Numeric(18, 8), default=0)
    reasoning_price: Mapped[float] = mapped_column(Numeric(18, 8), default=0)
    unit_tokens: Mapped[int] = mapped_column(Integer, default=1_000_000)
    version: Mapped[str] = mapped_column(String(32))
    source: Mapped[str] = mapped_column(String(255), default="管理员配置")
    effective_from: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class ModelingTask(Base):
    """建模任务：目标、取数 SQL、特征与候选算法；训练产物登记在 GovernedModel。"""

    __tablename__ = "modeling_task"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    title: Mapped[str] = mapped_column(String(120), comment="任务名，如 质量风险识别")
    source: Mapped[str] = mapped_column(String(10), default="agent", comment="agent / manual")
    source_id: Mapped[int] = mapped_column(Integer, default=1, index=True, comment="数据源")
    owner_id: Mapped[str] = mapped_column(String(64), default="local-test-user", index=True)
    goal: Mapped[str] = mapped_column(Text, comment="业务目标")
    task_type: Mapped[str] = mapped_column(String(20), index=True, comment="regression/classification/clustering/anomaly")
    dataset_name: Mapped[str] = mapped_column(String(100), default="", comment="数据集语义展示名")
    dataset_sql: Mapped[str] = mapped_column(Text, comment="取数 SQL（已过白名单校验与审计）")
    target_field: Mapped[str] = mapped_column(String(64), default="", comment="标签列；聚类/异常检测为空")
    target_label: Mapped[str] = mapped_column(String(100), default="", comment="标签语义描述，如 不良率是否 ≥ 4%")
    features_json: Mapped[list] = mapped_column(JSON_TYPE, default=list, comment="特征列名")
    candidates_json: Mapped[list] = mapped_column(JSON_TYPE, default=list, comment="候选算法 id")
    split_strategy: Mapped[str] = mapped_column(String(16), default="auto", comment="auto/random/time/group")
    time_field: Mapped[str] = mapped_column(String(64), default="", comment="时间留出字段")
    group_field: Mapped[str] = mapped_column(String(64), default="", comment="业务实体/工单分组字段")
    diagnostics_json: Mapped[dict] = mapped_column(JSON_TYPE, default=dict, comment="最近一次训练数据体检")
    diagnosed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    selected_model: Mapped[str | None] = mapped_column(String(30), nullable=True, comment="效果面板推荐模型算法 id")
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True, comment="draft/training/trained/failed")
    error_json: Mapped[dict | None] = mapped_column(JSON_TYPE, nullable=True, comment="训练失败原因")
    run_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True, comment="提案来源 Agent Run")
    modeling_proposal_json: Mapped[dict] = mapped_column(JSON_TYPE, default=dict, comment="Agent 原始提案快照")
    sample_count: Mapped[int] = mapped_column(Integer, default=0, comment="训练样本量")
    metric: Mapped[str] = mapped_column(String(100), default="", comment="展示摘要，如 AUC 0.986")
    trained_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class GovernedModel(Base):
    """模型注册表：训练产物登记与治理（复核/发布/下线）的生命周期。"""

    __tablename__ = "governed_model"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(40), index=True, comment="来源任务")
    name: Mapped[str] = mapped_column(String(120), comment="模型名")
    version: Mapped[int] = mapped_column(Integer, default=1, comment="同名模型按版本递增")
    training_revision: Mapped[int] = mapped_column(Integer, default=1, comment="所属训练批次")
    artifact_format_version: Mapped[int] = mapped_column(Integer, default=2, comment="模型包格式版本")
    artifact_checksum: Mapped[str] = mapped_column(String(64), default="", comment="模型文件 SHA-256")
    decision_threshold: Mapped[float | None] = mapped_column(Numeric(6, 4), nullable=True)
    threshold_policy_json: Mapped[dict] = mapped_column(JSON_TYPE, default=dict)
    tuning_json: Mapped[dict] = mapped_column(JSON_TYPE, default=dict)
    explanation_json: Mapped[dict] = mapped_column(JSON_TYPE, default=dict)
    algorithm: Mapped[str] = mapped_column(String(30), comment="linear/tree/forest/logistic/kmeans/isolation")
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True, comment="pending/reviewing/published/rejected/retired")
    owner_id: Mapped[str] = mapped_column(String(64), default="local-test-user")
    metrics_json: Mapped[dict] = mapped_column(JSON_TYPE, default=dict, comment="主模型指标摘要")
    features_json: Mapped[list] = mapped_column(JSON_TYPE, default=list)
    model_path: Mapped[str] = mapped_column(Text, default="", comment="模型文件路径（joblib）")
    feature_names_json: Mapped[list] = mapped_column(JSON_TYPE, default=list, comment="推理时对齐 X 的列序")
    feature_encoders_json: Mapped[dict] = mapped_column(JSON_TYPE, default=dict, comment="类别列 LabelEncoder 映射")
    feature_importance_json: Mapped[list] = mapped_column(JSON_TYPE, default=list, comment="[{name, importance}]")
    call_count: Mapped[int] = mapped_column(Integer, default=0, comment="推理调用次数")
    reviewed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class ModelingEvaluation(Base):
    """一次训练批次的不可变评估快照；保留重训历史用于比较和回滚。"""

    __tablename__ = "modeling_evaluation"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(40), index=True)
    training_revision: Mapped[int] = mapped_column(Integer, default=1, index=True)
    model_ids_json: Mapped[list] = mapped_column(JSON_TYPE, default=list)
    result_json: Mapped[dict] = mapped_column(JSON_TYPE, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class ModelingInferenceLog(Base):
    """模型每次推理请求的输入输出留痕，用于审计与复核。"""

    __tablename__ = "modeling_inference_log"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(40), index=True)
    model_id: Mapped[str] = mapped_column(String(40), index=True)
    input_json: Mapped[dict] = mapped_column(JSON_TYPE, default=dict)
    output_json: Mapped[dict] = mapped_column(JSON_TYPE, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class PlatformUser(Base):
    """平台用户（登录名统一小写）。表在平台库 forge_sight_app；业务库红线不受影响。"""

    __tablename__ = "platform_user"
    __table_args__ = (
        CheckConstraint(
            "(email IS NULL AND email_canonical IS NULL) OR "
            "(email IS NOT NULL AND email_canonical IS NOT NULL)",
            name="ck_platform_user_email_pair",
        ),
        CheckConstraint(
            "email_verified_at IS NULL OR email IS NOT NULL",
            name="ck_platform_user_verified_email",
        ),
        Index(
            "uq_platform_user_email",
            "email_canonical",
            unique=True,
            postgresql_where=text("email_canonical IS NOT NULL"),
            sqlite_where=text("email_canonical IS NOT NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True, comment="登录名（统一小写）")
    password_hash: Mapped[str] = mapped_column(String(256), comment="scrypt: 算法$n$r$p$salt_hex$hash_hex")
    display_name: Mapped[str] = mapped_column(String(50), default="", comment="展示名")
    role: Mapped[str] = mapped_column(String(20), default="business", comment="business / admin")
    title: Mapped[str] = mapped_column(String(100), default="", comment="岗位展示，如 业务人员")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, comment="停用后无法登录")
    email: Mapped[str | None] = mapped_column(String(254), nullable=True, comment="可投递邮箱")
    email_canonical: Mapped[str | None] = mapped_column(
        String(254), nullable=True, comment="邮箱唯一比较值"
    )
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class OrganizationUnit(Base):
    """企业、区域、部门等可组合的树形组织节点。"""

    __tablename__ = "organization_unit"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    code: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(160))
    node_type: Mapped[str] = mapped_column(String(32), index=True)
    parent_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("organization_unit.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    path: Mapped[str] = mapped_column(String(500), default="", index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class UserMembership(Base):
    """用户的组织、岗位和组织内业务角色。"""

    __tablename__ = "user_membership"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "organization_id", "position_code", name="uq_user_membership_position"
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(40), index=True)
    organization_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("organization_unit.id", ondelete="RESTRICT"), index=True
    )
    position_code: Mapped[str] = mapped_column(String(80), default="member", index=True)
    business_role: Mapped[str] = mapped_column(String(80), default="member", index=True)
    effective_from: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class DataScopePolicy(Base):
    """用户、组织、岗位或业务角色对应的显式数据访问范围。"""

    __tablename__ = "data_scope_policy"
    __table_args__ = (
        UniqueConstraint(
            "subject_type", "subject_id", "business_key", name="uq_data_scope_subject_business"
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    subject_type: Mapped[str] = mapped_column(String(24), index=True)
    subject_id: Mapped[str] = mapped_column(String(80), index=True)
    business_key: Mapped[str] = mapped_column(String(80), index=True)
    effect: Mapped[str] = mapped_column(String(16), default="allow")
    data_source_ids_json: Mapped[list] = mapped_column(JSON_TYPE, default=list)
    dimensions_json: Mapped[dict] = mapped_column(JSON_TYPE, default=dict)
    effective_from: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[str] = mapped_column(String(40), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class DomainEventOutbox(Base):
    """与领域状态同事务写入、可租约恢复的持久化事件。"""

    __tablename__ = "domain_event_outbox"
    __table_args__ = (
        CheckConstraint("attempt_count >= 0", name="ck_outbox_attempt_count"),
        CheckConstraint(
            "status IN ('pending', 'processing', 'retry_wait', 'completed', 'dead_letter')",
            name="ck_outbox_status",
        ),
        Index("ix_outbox_claim", "status", "next_retry_at", "lease_expires_at"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(100), index=True)
    aggregate_id: Mapped[str] = mapped_column(String(80), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(160), unique=True)
    payload_json: Mapped[dict] = mapped_column(JSON_TYPE, default=dict)
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    lease_owner: Mapped[str | None] = mapped_column(String(100), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error_summary: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class EmailVerificationChallenge(Base):
    """单次邮箱所有权验证挑战；每个用户同时最多存在一条活动记录。"""

    __tablename__ = "email_verification_challenge"
    __table_args__ = (
        CheckConstraint(
            "purpose IN ('bind', 'verify', 'change')",
            name="ck_email_challenge_purpose",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_email_challenge_attempt_count"),
        CheckConstraint("send_count >= 0", name="ck_email_challenge_send_count"),
        CheckConstraint(
            "consumed_at IS NOT NULL OR superseded_at IS NOT NULL OR code_hash IS NOT NULL",
            name="ck_email_challenge_active_hash",
        ),
        Index(
            "uq_email_verification_challenge_active",
            "user_id",
            unique=True,
            postgresql_where=text("consumed_at IS NULL AND superseded_at IS NULL"),
            sqlite_where=text("consumed_at IS NULL AND superseded_at IS NULL"),
        ),
        Index("ix_email_verification_challenge_user", "user_id"),
        Index(
            "ix_email_verification_challenge_email_created",
            "email_canonical",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(40),
        ForeignKey("platform_user.id", ondelete="RESTRICT"),
    )
    purpose: Mapped[str] = mapped_column(String(24))
    email: Mapped[str] = mapped_column(String(254), comment="可投递目标邮箱快照")
    email_canonical: Mapped[str] = mapped_column(String(254), comment="邮箱唯一比较值")
    code_hash: Mapped[str | None] = mapped_column(String(256), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    send_count: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    last_sent_at: Mapped[datetime] = mapped_column(DateTime)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class EmailVerificationEvent(Base):
    """邮箱验证的不可变安全事件，是滚动频控窗口的事实源。"""

    __tablename__ = "email_verification_event"
    __table_args__ = (
        CheckConstraint(
            "purpose IN ('bind', 'verify', 'change')",
            name="ck_email_event_purpose",
        ),
        Index(
            "ix_email_verification_event_email_time",
            "email_canonical",
            "created_at",
        ),
        Index("ix_email_verification_event_user_time", "user_id", "created_at"),
        Index("ix_email_verification_event_ip_time", "ip", "created_at"),
        Index(
            "ix_email_verification_event_challenge_time", "challenge_id", "created_at"
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(40),
        ForeignKey("platform_user.id", ondelete="RESTRICT"),
    )
    email_canonical: Mapped[str] = mapped_column(String(254))
    purpose: Mapped[str] = mapped_column(String(24))
    challenge_id: Mapped[str | None] = mapped_column(
        String(40),
        ForeignKey("email_verification_challenge.id", ondelete="SET NULL"),
        nullable=True,
    )
    event_type: Mapped[str] = mapped_column(String(24))
    outcome: Mapped[str] = mapped_column(String(32), default="", server_default=text("''"))
    ip: Mapped[str] = mapped_column(String(45), default="", server_default=text("''"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
