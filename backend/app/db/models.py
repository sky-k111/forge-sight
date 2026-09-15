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
    PrimaryKeyConstraint,
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
    tenant_id: Mapped[str] = mapped_column(String(40), default="tenant-default", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, comment="发生时间")
    user_name: Mapped[str] = mapped_column(String(50), default="演示用户", comment="操作人")
    role: Mapped[str] = mapped_column(String(20), default="业务人员", comment="角色")
    action: Mapped[str] = mapped_column(String(255), comment="执行动作，如 查询工序良率 / DELETE FROM ...")
    resource: Mapped[str] = mapped_column(String(255), default="", comment="涉及数据资源")
    result: Mapped[str] = mapped_column(String(20), comment="允许 / 已拦截 / 已记录")
    detail: Mapped[str] = mapped_column(Text, default="", comment="详情（SQL 摘要、拦截原因等）")
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, default=None, comment="拦截记录复核时间，未复核为空")
    reviewed_by: Mapped[str] = mapped_column(String(50), default="", comment="复核人")


# 结果 → 前端展示的 tone（与前端 StatusPill 对齐）
RESULT_TONES = {"允许": "green", "已拦截": "red", "已记录": "blue"}


class Tenant(Base):
    """SaaS 租户根节点；组织树、数据源和业务资产都必须归属于一个租户。"""

    __tablename__ = "tenant"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'suspended', 'archived')", name="ck_tenant_status"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(24), default="active", index=True)
    plan_code: Mapped[str] = mapped_column(String(40), default="enterprise")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class DataSource(Base):
    """Tenant-scoped business datasource and connector configuration."""

    __tablename__ = "data_source"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("tenant.id", ondelete="RESTRICT"), default="tenant-default", index=True
    )
    name: Mapped[str] = mapped_column(String(100), comment="数据源名称，如 华东制造数据底座")
    type: Mapped[str] = mapped_column(String(20), comment="mysql / postgresql / mariadb / sqlserver / oracle")
    host: Mapped[str] = mapped_column(String(100), default="127.0.0.1")
    port: Mapped[int] = mapped_column(Integer, default=3306)
    db_name: Mapped[str] = mapped_column(String(64), comment="数据库名")
    username: Mapped[str] = mapped_column(String(64), comment="只读账号")
    password: Mapped[str] = mapped_column(String(128), default="")
    schema_names: Mapped[list] = mapped_column("schema_names_json", JSON_TYPE, default=list)
    config_json: Mapped[dict] = mapped_column(JSON_TYPE, default=dict)
    credential_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    connection_revision: Mapped[int] = mapped_column(Integer, default=1)
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
    __table_args__ = (UniqueConstraint("source_id", "schema_name", "name", name="uq_source_schema_table"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[int] = mapped_column(Integer, default=1, index=True, comment="所属数据源")
    schema_name: Mapped[str] = mapped_column(String(128), default="", index=True, comment="物理 Schema")
    name: Mapped[str] = mapped_column(String(64), comment="物理表名")
    object_type: Mapped[str] = mapped_column(String(20), default="table", comment="table / view / materialized_view")
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
    schema_name: Mapped[str] = mapped_column(String(128), default="", index=True, comment="所属 Schema")
    table_name: Mapped[str] = mapped_column(String(64), index=True, comment="所属表")
    name: Mapped[str] = mapped_column(String(64), comment="字段名")
    cn_name: Mapped[str] = mapped_column(String(100), default="", comment="中文名（来自字段注释，可修正）")
    data_type: Mapped[str] = mapped_column(String(40), default="", comment="数据类型")
    ordinal: Mapped[int] = mapped_column(Integer, default=0, comment="字段顺序")
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否主键")
    ref_schema: Mapped[str] = mapped_column(String(128), default="", comment="外键指向的 Schema")
    ref_table: Mapped[str] = mapped_column(String(64), default="", comment="外键指向的表")
    comment: Mapped[str] = mapped_column(String(255), default="", comment="字段注释")
    sample_value: Mapped[str] = mapped_column(String(100), default="", comment="样例值")


class TableRelation(Base):
    """表间关联关系（外键扫描 + 命名约定推导）。"""

    __tablename__ = "table_relation"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[int] = mapped_column(Integer, default=1, index=True, comment="所属数据源")
    from_schema: Mapped[str] = mapped_column(String(128), default="", comment="来源 Schema")
    from_table: Mapped[str] = mapped_column(String(64), comment="来源表")
    from_field: Mapped[str] = mapped_column(String(64), comment="来源字段")
    to_schema: Mapped[str] = mapped_column(String(128), default="", comment="目标 Schema")
    to_table: Mapped[str] = mapped_column(String(64), comment="目标表")
    to_field: Mapped[str] = mapped_column(String(64), comment="目标字段")
    constraint_name: Mapped[str] = mapped_column(String(160), default="", comment="数据库约束名")
    source: Mapped[str] = mapped_column(String(20), default="扫描", comment="来源：扫描 / 推导 / 人工")


class KnowledgeTopic(Base):
    """分析主题：组织指标、对象和规则的顶层业务分类。"""

    __tablename__ = "knowledge_topic"
    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "code", name="pk_knowledge_topic"),
        UniqueConstraint("tenant_id", "name", name="uq_knowledge_topic_tenant_name"),
    )

    code: Mapped[str] = mapped_column(String(20), comment="稳定编码，如 QUAL")
    tenant_id: Mapped[str] = mapped_column(String(40), default="tenant-default", index=True)
    name: Mapped[str] = mapped_column(String(50), comment="主题名称")
    color: Mapped[str] = mapped_column(String(20), default="blue")
    description: Mapped[str] = mapped_column(String(255), default="")
    coverage: Mapped[int] = mapped_column(Integer, default=0, comment="语义覆盖率，仅用于治理展示")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class BusinessObject(Base):
    """业务对象：产品、工单、工序、设备等企业语义实体。"""

    __tablename__ = "business_object"
    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "id", name="pk_business_object"),
        UniqueConstraint("tenant_id", "name", name="uq_business_object_tenant_name"),
    )

    id: Mapped[str] = mapped_column(String(50), comment="稳定英文标识")
    tenant_id: Mapped[str] = mapped_column(String(40), default="tenant-default", index=True)
    name: Mapped[str] = mapped_column(String(80))
    topic: Mapped[str] = mapped_column(String(50), default="全局")
    description: Mapped[str] = mapped_column(String(255), default="")
    relation_count: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class BusinessRule(Base):
    """业务规则：指标计算、数据校验、异常识别与查询约束。"""

    __tablename__ = "business_rule"
    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "id", name="pk_business_rule"),
        UniqueConstraint("tenant_id", "name", name="uq_business_rule_tenant_name"),
    )

    id: Mapped[str] = mapped_column(String(20))
    tenant_id: Mapped[str] = mapped_column(String(40), default="tenant-default", index=True)
    name: Mapped[str] = mapped_column(String(180))
    rule_type: Mapped[str] = mapped_column(String(30), default="业务规则")
    scope: Mapped[str] = mapped_column(String(50), default="全局")
    content: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class KnowledgeMetric(Base):
    """企业统一指标语义；这里只描述业务含义，不出现某个数据源的物理字段。"""

    __tablename__ = "knowledge_metric"
    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "id", name="pk_knowledge_metric"),
        UniqueConstraint("tenant_id", "name", name="uq_knowledge_metric_tenant_name"),
    )

    id: Mapped[str] = mapped_column(String(20))
    tenant_id: Mapped[str] = mapped_column(String(40), default="tenant-default", index=True)
    name: Mapped[str] = mapped_column(String(100))
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
    table_name: Mapped[str] = mapped_column(String(260), comment="schema.table 限定名；单 schema 可保留裸表名")
    role: Mapped[str] = mapped_column(String(30), default="事实表", comment="事实表 / 维表 / 明细表")


class MetricFieldBinding(Base):
    """统一语义字段到单个数据源物理字段的映射。"""

    __tablename__ = "metric_field_binding"
    __table_args__ = (UniqueConstraint("binding_id", "semantic_field", name="uq_metric_binding_semantic_field"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    binding_id: Mapped[int] = mapped_column(Integer, index=True)
    semantic_field: Mapped[str] = mapped_column(String(80), comment="如 合格数量、投入数量、统计时间")
    table_name: Mapped[str] = mapped_column(String(260), comment="schema.table 限定名；单 schema 可保留裸表名")
    field_name: Mapped[str] = mapped_column(String(64))
    transform_expression: Mapped[str] = mapped_column(Text, default="", comment="单位换算或枚举归一表达式")


class MetricCandidate(Base):
    """AI 生成的指标/数据源绑定候选；候选永远不能直接成为已发布口径。"""

    __tablename__ = "metric_candidate"
    __table_args__ = (
        UniqueConstraint("source_id", "fingerprint", name="uq_metric_candidate_source_fingerprint"),
        CheckConstraint("candidate_kind IN ('new_metric', 'source_binding')", name="ck_metric_candidate_kind"),
        CheckConstraint(
            "status IN ('pending', 'accepted', 'rejected', 'superseded')",
            name="ck_metric_candidate_status",
        ),
        CheckConstraint("confidence_score BETWEEN 0 AND 100", name="ck_metric_candidate_confidence"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_id: Mapped[int] = mapped_column(Integer, index=True)
    candidate_kind: Mapped[str] = mapped_column(
        String(24), default="new_metric", comment="new_metric / source_binding"
    )
    target_metric_id: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(100))
    topic: Mapped[str] = mapped_column(String(50))
    canonical_payload_json: Mapped[dict] = mapped_column(JSON_TYPE, default=dict)
    binding_payload_json: Mapped[dict] = mapped_column(JSON_TYPE, default=dict)
    evidence_json: Mapped[dict] = mapped_column(JSON_TYPE, default=dict)
    validation_json: Mapped[dict] = mapped_column(JSON_TYPE, default=dict)
    confidence_score: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(
        String(20), default="pending", index=True, comment="pending / accepted / rejected / superseded"
    )
    fingerprint: Mapped[str] = mapped_column(String(64))
    generator_version: Mapped[str] = mapped_column(String(32), default="metric-candidate-v1")
    review_note: Mapped[str] = mapped_column(Text, default="")
    reviewed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    accepted_metric_id: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class KnowledgeReference(Base):
    """知识引用索引，用于阻止删除仍被分析模板、任务或其他知识项引用的内容。"""

    __tablename__ = "knowledge_reference"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "entity_type",
            "entity_id",
            "consumer_type",
            "consumer_id",
            name="uq_knowledge_reference",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(40), default="tenant-default", index=True)
    entity_type: Mapped[str] = mapped_column(String(30), index=True, comment="topic / object / rule / metric")
    entity_id: Mapped[str] = mapped_column(String(50), index=True)
    consumer_type: Mapped[str] = mapped_column(String(30), comment="metric / analysis_template / report_template")
    consumer_id: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(String(255), default="")


class AnalysisSession(Base):
    """连续业务分析会话；分支会创建新会话，不改写父会话。"""

    __tablename__ = "analysis_session"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(40), default="tenant-default", index=True)
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
    memory_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    memory_experiment_variant: Mapped[str] = mapped_column(String(16), default="off")
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
    """外部 IM 会话与 ForgeSight 分析会话的稳定映射。"""

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
    external_user_id: Mapped[str] = mapped_column(String(160), index=True)
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


class AgentMemory(Base):
    """受治理的长期记忆。

    记忆与企业知识分开存储：知识描述企业事实与口径，记忆描述用户偏好、
    已发生的调查以及可复用经验。所有检索必须先按作用域过滤，再做相关性排序。
    """

    __tablename__ = "agent_memory"
    __table_args__ = (
        Index("ix_agent_memory_scope", "tenant_id", "source_id", "user_id", "status"),
        Index("ix_agent_memory_kind_status", "memory_kind", "status"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_agent_memory_confidence"),
        CheckConstraint("importance >= 0 AND importance <= 1", name="ck_agent_memory_importance"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), default="tenant-default", index=True)
    source_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    user_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    role_scope: Mapped[str | None] = mapped_column(String(32), nullable=True)
    session_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    channel: Mapped[str] = mapped_column(String(24), default="web")
    memory_kind: Mapped[str] = mapped_column(String(24), index=True, comment="semantic / episodic / preference / procedural")
    status: Mapped[str] = mapped_column(String(24), default="candidate", index=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    content: Mapped[str] = mapped_column(Text)
    normalized_content: Mapped[str] = mapped_column(Text, default="")
    keywords_json: Mapped[list] = mapped_column(JSON_TYPE, default=list)
    metadata_json: Mapped[dict] = mapped_column(JSON_TYPE, default=dict)
    confidence: Mapped[float] = mapped_column(Numeric(5, 4), default=0.7)
    importance: Mapped[float] = mapped_column(Numeric(5, 4), default=0.5)
    explicit: Mapped[bool] = mapped_column(Boolean, default=False)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    last_accessed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    access_count: Mapped[int] = mapped_column(Integer, default=0)
    supersedes_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_by: Mapped[str] = mapped_column(String(64), default="system")
    reviewed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class AgentMemoryEvidence(Base):
    """记忆的来源与可追溯证据，不保存模型隐式思维链。"""

    __tablename__ = "agent_memory_evidence"
    __table_args__ = (Index("ix_agent_memory_evidence_memory", "memory_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    memory_id: Mapped[str] = mapped_column(String(40), index=True)
    evidence_type: Mapped[str] = mapped_column(String(32), comment="message / run / feedback / admin")
    reference_id: Mapped[str] = mapped_column(String(80), index=True)
    excerpt: Mapped[str] = mapped_column(Text, default="")
    metadata_json: Mapped[dict] = mapped_column(JSON_TYPE, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class AgentMemoryRelation(Base):
    """记忆间的纠正、重复、因果与技能提升关系。"""

    __tablename__ = "agent_memory_relation"
    __table_args__ = (
        UniqueConstraint("from_memory_id", "to_memory_id", "relation_type", name="uq_agent_memory_relation"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    from_memory_id: Mapped[str] = mapped_column(String(40), index=True)
    to_memory_id: Mapped[str] = mapped_column(String(40), index=True)
    relation_type: Mapped[str] = mapped_column(String(32), comment="supersedes / duplicate / supports / promotes")
    metadata_json: Mapped[dict] = mapped_column(JSON_TYPE, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class AgentMemoryAccessLog(Base):
    """召回审计与 A/B 评估事实。"""

    __tablename__ = "agent_memory_access_log"
    __table_args__ = (Index("ix_agent_memory_access_run", "run_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    memory_id: Mapped[str] = mapped_column(String(40), index=True)
    run_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    source_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    query_text: Mapped[str] = mapped_column(Text, default="")
    relevance_score: Mapped[float] = mapped_column(Numeric(8, 6), default=0)
    used_in_context: Mapped[bool] = mapped_column(Boolean, default=False)
    variant: Mapped[str] = mapped_column(String(16), default="off")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class AgentMemoryFeedback(Base):
    """对单条记忆的确认、纠正、否定与遗忘反馈。"""

    __tablename__ = "agent_memory_feedback"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    memory_id: Mapped[str] = mapped_column(String(40), index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    action: Mapped[str] = mapped_column(String(24), comment="confirm / correct / reject / forget")
    comment: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class AgentMemoryJob(Base):
    """后台抽取、合并、过期与重建向量的持久化任务。"""

    __tablename__ = "agent_memory_job"
    __table_args__ = (
        UniqueConstraint("job_type", "reference_id", name="uq_agent_memory_job_reference"),
        Index("ix_agent_memory_job_claim", "status", "lease_expires_at"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    job_type: Mapped[str] = mapped_column(String(32), comment="capture_run / consolidate / rebuild_embedding")
    reference_id: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    payload_json: Mapped[dict] = mapped_column(JSON_TYPE, default=dict)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    error: Mapped[str] = mapped_column(Text, default="")
    lease_owner: Mapped[str | None] = mapped_column(String(100), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class AgentMemoryPolicy(Base):
    """企业级保留、自动激活与脱敏策略。"""

    __tablename__ = "agent_memory_policy"
    __table_args__ = (UniqueConstraint("tenant_id", "policy_key", name="uq_agent_memory_policy"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), default="tenant-default", index=True)
    policy_key: Mapped[str] = mapped_column(String(64))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
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
    """外部消息的持久化收件箱与可恢复投递状态。"""

    __tablename__ = "external_chat_job"
    __table_args__ = (
        UniqueConstraint("channel", "external_message_id", name="uq_external_chat_message"),
        CheckConstraint("attempt_count >= 0", name="ck_external_chat_attempt_count"),
        Index(
            "ix_external_chat_job_claim",
            "channel",
            "status",
            "next_attempt_at",
            "lease_expires_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    channel: Mapped[str] = mapped_column(String(24), index=True)
    tenant_key: Mapped[str] = mapped_column(String(80), default="", index=True)
    external_message_id: Mapped[str] = mapped_column(String(120))
    external_chat_id: Mapped[str] = mapped_column(String(120), index=True)
    external_user_id: Mapped[str] = mapped_column(String(160), index=True)
    question: Mapped[str] = mapped_column(Text)
    reply_target_json: Mapped[dict] = mapped_column(JSON_TYPE, default=dict)
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
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    error_summary: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class DingTalkBinding(Base):
    """ForgeSight 用户与钉钉身份、默认数据源的受控绑定。"""

    __tablename__ = "dingtalk_binding"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_dingtalk_binding_user"),
        Index(
            "uq_dingtalk_binding_corp_union",
            "corp_id",
            "union_id",
            unique=True,
            postgresql_where=text("union_id IS NOT NULL"),
            sqlite_where=text("union_id IS NOT NULL"),
        ),
        Index(
            "uq_dingtalk_binding_corp_open",
            "corp_id",
            "open_id",
            unique=True,
            postgresql_where=text("open_id IS NOT NULL"),
            sqlite_where=text("open_id IS NOT NULL"),
        ),
        Index("ix_dingtalk_binding_mobile", "mobile"),
        Index("ix_dingtalk_binding_status", "bind_status"),
        CheckConstraint(
            "union_id IS NOT NULL OR open_id IS NOT NULL",
            name="ck_dingtalk_binding_identity",
        ),
        CheckConstraint(
            "bind_status IN ('bound', 'unbound', 'disabled')",
            name="ck_dingtalk_binding_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(40),
        ForeignKey("platform_user.id", ondelete="CASCADE"),
        index=True,
    )
    corp_id: Mapped[str] = mapped_column(String(64), default="")
    union_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    open_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("data_source.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    mobile: Mapped[str | None] = mapped_column(String(32), nullable=True)
    display_name: Mapped[str] = mapped_column(String(100), default="")
    avatar: Mapped[str | None] = mapped_column(Text, nullable=True)
    bind_status: Mapped[str] = mapped_column(String(24), default="bound")
    last_auth_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class DingTalkOAuthState(Base):
    """钉钉 OAuth 绑定过程中的一次性 state。"""

    __tablename__ = "dingtalk_oauth_state"
    __table_args__ = (
        UniqueConstraint("state_token", name="uq_dingtalk_oauth_state_token"),
        Index("ix_dingtalk_oauth_state_user", "user_id"),
        Index("ix_dingtalk_oauth_state_expires", "expires_at"),
        CheckConstraint(
            "purpose IN ('bind', 'login')",
            name="ck_dingtalk_oauth_state_purpose",
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str | None] = mapped_column(
        String(40),
        ForeignKey("platform_user.id", ondelete="CASCADE"),
        nullable=True,
    )
    state_token: Mapped[str] = mapped_column(String(128))
    corp_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    redirect_path: Mapped[str] = mapped_column(String(255), default="")
    purpose: Mapped[str] = mapped_column(String(24), default="bind")
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class PersonalReportTemplate(Base):
    """Versioned personal report composition, isolated by tenant/account/source."""

    __tablename__ = "personal_report_template"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("data_source.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    config_json: Mapped[dict] = mapped_column(JSON_TYPE, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


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
    external_identity_required: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        comment="新注册业务账号必须绑定钉钉或飞书；历史账号默认豁免",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class TenantMembership(Base):
    """平台账号在租户内的成员身份；平台角色与租户角色分开管理。"""

    __tablename__ = "tenant_membership"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", name="uq_tenant_membership_user"),
        CheckConstraint(
            "member_role IN ('owner', 'admin', 'member', 'viewer')",
            name="ck_tenant_membership_role",
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("tenant.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("platform_user.id", ondelete="CASCADE"), index=True
    )
    member_role: Mapped[str] = mapped_column(String(24), default="member", index=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    joined_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


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
            "tenant_id",
            "subject_type",
            "subject_id",
            "business_key",
            name="uq_data_scope_tenant_subject_business",
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(40), default="tenant-default", index=True)
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


class DashboardPreference(Base):
    """Personal dashboard configuration inside a tenant and data-source boundary."""

    __tablename__ = "dashboard_preference"
    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "user_id", "source_id", name="pk_dashboard_preference"),
    )

    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("data_source.id", ondelete="CASCADE"), index=True)
    config_json: Mapped[dict] = mapped_column(JSON_TYPE, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1)
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

class FeishuBinding(Base):
    """系统用户与飞书身份的绑定关系；不持久化 OAuth token。"""

    __tablename__ = "feishu_binding"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_feishu_binding_user"),
        Index(
            "uq_feishu_binding_tenant_union",
            "tenant_key",
            "union_id",
            unique=True,
            postgresql_where=text("union_id IS NOT NULL"),
            sqlite_where=text("union_id IS NOT NULL"),
        ),
        Index(
            "uq_feishu_binding_tenant_open",
            "tenant_key",
            "open_id",
            unique=True,
            postgresql_where=text("open_id IS NOT NULL"),
            sqlite_where=text("open_id IS NOT NULL"),
        ),
        Index("ix_feishu_binding_status", "bind_status"),
        CheckConstraint(
            "union_id IS NOT NULL OR open_id IS NOT NULL",
            name="ck_feishu_binding_identity",
        ),
        CheckConstraint(
            "bind_status IN ('bound', 'unbound', 'disabled')",
            name="ck_feishu_binding_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(40),
        ForeignKey("platform_user.id", ondelete="CASCADE"),
        index=True,
    )
    tenant_key: Mapped[str] = mapped_column(String(80), default="")
    union_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    open_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    employee_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("data_source.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    mobile: Mapped[str | None] = mapped_column(String(32), nullable=True)
    email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    display_name: Mapped[str] = mapped_column(String(100), default="")
    avatar: Mapped[str | None] = mapped_column(Text, nullable=True)
    bind_status: Mapped[str] = mapped_column(String(24), default="bound")
    last_auth_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class FeishuOAuthState(Base):
    """飞书 OAuth state 防 CSRF、防重放记录。"""

    __tablename__ = "feishu_oauth_state"
    __table_args__ = (
        UniqueConstraint("state_token", name="uq_feishu_oauth_state_token"),
        Index("ix_feishu_oauth_state_user", "user_id"),
        Index("ix_feishu_oauth_state_expires", "expires_at"),
        CheckConstraint(
            "purpose IN ('bind', 'login')",
            name="ck_feishu_oauth_state_purpose",
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str | None] = mapped_column(
        String(40),
        ForeignKey("platform_user.id", ondelete="CASCADE"),
        nullable=True,
    )
    state_token: Mapped[str] = mapped_column(String(128))
    redirect_path: Mapped[str] = mapped_column(String(255), default="")
    purpose: Mapped[str] = mapped_column(String(24), default="bind")
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class MonitoringRuleTemplate(Base):
    """Versioned semantic monitoring template, independent of physical source fields."""

    __tablename__ = "monitoring_rule_template"
    __table_args__ = (
        UniqueConstraint("code", "version", name="uq_monitoring_template_version"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    code: Mapped[str] = mapped_column(String(80), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    name: Mapped[str] = mapped_column(String(160))
    topic: Mapped[str] = mapped_column(String(80), default="production")
    description: Mapped[str] = mapped_column(Text, default="")
    default_condition_json: Mapped[dict] = mapped_column(JSON_TYPE, default=dict)
    default_quality_gate_json: Mapped[dict] = mapped_column(JSON_TYPE, default=dict)
    status: Mapped[str] = mapped_column(String(24), default="published", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class MonitoringRule(Base):
    """Published rule instance bound to one source and a structured query plan."""

    __tablename__ = "monitoring_rule"
    __table_args__ = (
        UniqueConstraint("code", "version", "source_id", name="uq_monitoring_rule_version"),
        Index("ix_monitoring_rule_template_ref", "template_code"),
        CheckConstraint(
            "status IN ('draft', 'published', 'paused', 'retired')",
            name="ck_monitoring_rule_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    code: Mapped[str] = mapped_column(String(80), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    template_code: Mapped[str] = mapped_column(String(80))
    source_id: Mapped[int] = mapped_column(Integer, ForeignKey("data_source.id", ondelete="RESTRICT"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    metric_name: Mapped[str] = mapped_column(String(120))
    entity_type: Mapped[str] = mapped_column(String(80), default="source")
    grain: Mapped[str] = mapped_column(String(24), default="day")
    query_plan_json: Mapped[dict] = mapped_column(JSON_TYPE, default=dict)
    condition_json: Mapped[dict] = mapped_column(JSON_TYPE, default=dict)
    quality_gate_json: Mapped[dict] = mapped_column(JSON_TYPE, default=dict)
    severity_policy_json: Mapped[dict] = mapped_column(JSON_TYPE, default=dict)
    notification_policy_json: Mapped[dict] = mapped_column(JSON_TYPE, default=dict)
    schedule_seconds: Mapped[int] = mapped_column(Integer, default=86400)
    status: Mapped[str] = mapped_column(String(24), default="draft", index=True)
    created_by: Mapped[str] = mapped_column(String(40), default="system")
    published_by: Mapped[str | None] = mapped_column(String(40), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_scheduled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class MonitoringCursor(Base):
    """Latest completed window per published rule for restart/reconciliation visibility."""

    __tablename__ = "monitoring_cursor"
    __table_args__ = (
        UniqueConstraint("rule_id", name="uq_monitoring_cursor_rule"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    rule_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("monitoring_rule.id", ondelete="CASCADE"), index=True
    )
    source_id: Mapped[int] = mapped_column(Integer, index=True)
    logical_period: Mapped[str] = mapped_column(String(80), default="")
    watermark_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    task_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class MonitoringTask(Base):
    """Durable execution task claimed through a lease."""

    __tablename__ = "monitoring_task"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_monitoring_task_idempotency"),
        Index("ix_monitoring_task_claim", "status", "next_attempt_at", "lease_expires_at"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    rule_id: Mapped[str] = mapped_column(String(40), ForeignKey("monitoring_rule.id", ondelete="CASCADE"), index=True)
    rule_version: Mapped[int] = mapped_column(Integer)
    logical_period: Mapped[str] = mapped_column(String(80))
    window_start: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    window_end: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    idempotency_key: Mapped[str] = mapped_column(String(160))
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)
    lease_owner: Mapped[str | None] = mapped_column(String(100), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    error_summary: Mapped[str | None] = mapped_column(String(500), nullable=True)
    result_json: Mapped[dict] = mapped_column(JSON_TYPE, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class MonitoringObservation(Base):
    """Immutable-per-revision aggregate used to explain a rule evaluation."""

    __tablename__ = "monitoring_observation"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_monitoring_observation_idempotency"),
        Index("ix_monitoring_observation_rule_period", "rule_id", "logical_period"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    rule_id: Mapped[str] = mapped_column(String(40), ForeignKey("monitoring_rule.id", ondelete="CASCADE"), index=True)
    rule_version: Mapped[int] = mapped_column(Integer)
    source_id: Mapped[int] = mapped_column(Integer, index=True)
    logical_period: Mapped[str] = mapped_column(String(80))
    entity_type: Mapped[str] = mapped_column(String(80))
    entity_id: Mapped[str] = mapped_column(String(160))
    entity_name: Mapped[str] = mapped_column(String(200), default="")
    current_value: Mapped[float | None] = mapped_column(Numeric(24, 8), nullable=True)
    baseline_value: Mapped[float | None] = mapped_column(Numeric(24, 8), nullable=True)
    lower_bound: Mapped[float | None] = mapped_column(Numeric(24, 8), nullable=True)
    upper_bound: Mapped[float | None] = mapped_column(Numeric(24, 8), nullable=True)
    sample_size: Mapped[int] = mapped_column(Integer, default=0)
    quality_status: Mapped[str] = mapped_column(String(24), default="ok")
    evaluation_state: Mapped[str] = mapped_column(String(24), index=True)
    data_cutoff_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    details_json: Mapped[dict] = mapped_column(JSON_TYPE, default=dict)
    idempotency_key: Mapped[str] = mapped_column(String(160))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class MonitoringAlert(Base):
    """Long-lived alert fact; lifecycle is exclusively rule-driven."""

    __tablename__ = "monitoring_alert"
    __table_args__ = (
        UniqueConstraint("correlation_key", name="uq_monitoring_alert_correlation"),
        CheckConstraint(
            "status IN ('firing', 'recovering', 'resolved')",
            name="ck_monitoring_alert_status",
        ),
        Index("ix_monitoring_alert_filter", "status", "severity", "last_seen_at"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    rule_id: Mapped[str] = mapped_column(String(40), ForeignKey("monitoring_rule.id", ondelete="RESTRICT"), index=True)
    rule_version: Mapped[int] = mapped_column(Integer)
    source_id: Mapped[int] = mapped_column(Integer, index=True)
    metric_name: Mapped[str] = mapped_column(String(120))
    entity_type: Mapped[str] = mapped_column(String(80))
    entity_id: Mapped[str] = mapped_column(String(160), index=True)
    entity_name: Mapped[str] = mapped_column(String(200), default="")
    title: Mapped[str] = mapped_column(String(240))
    severity: Mapped[str] = mapped_column(String(16), default="medium", index=True)
    status: Mapped[str] = mapped_column(String(24), default="firing", index=True)
    correlation_key: Mapped[str] = mapped_column(String(160))
    current_value: Mapped[float | None] = mapped_column(Numeric(24, 8), nullable=True)
    baseline_value: Mapped[float | None] = mapped_column(Numeric(24, 8), nullable=True)
    sample_size: Mapped[int] = mapped_column(Integer, default=0)
    pending_count: Mapped[int] = mapped_column(Integer, default=0)
    recovery_count: Mapped[int] = mapped_column(Integer, default=0)
    recurrence_count: Mapped[int] = mapped_column(Integer, default=0)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    latest_observation_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class AlertEvidence(Base):
    """Frozen reference from an alert to its aggregate observation."""

    __tablename__ = "monitoring_alert_evidence"
    __table_args__ = (
        UniqueConstraint("alert_id", "observation_id", name="uq_monitoring_alert_evidence"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    alert_id: Mapped[str] = mapped_column(String(40), ForeignKey("monitoring_alert.id", ondelete="CASCADE"), index=True)
    observation_id: Mapped[str] = mapped_column(String(40), ForeignKey("monitoring_observation.id", ondelete="CASCADE"), index=True)
    evidence_type: Mapped[str] = mapped_column(String(32), default="observation")
    snapshot_json: Mapped[dict] = mapped_column(JSON_TYPE, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class AlertDelivery(Base):
    """Delivery lifecycle separated from the alert business state."""

    __tablename__ = "monitoring_alert_delivery"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_monitoring_alert_delivery_idempotency"),
        Index("ix_monitoring_delivery_claim", "status", "next_attempt_at", "lease_expires_at"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    alert_id: Mapped[str] = mapped_column(String(40), ForeignKey("monitoring_alert.id", ondelete="CASCADE"), index=True)
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    channel: Mapped[str] = mapped_column(String(24), index=True)
    recipient_user_id: Mapped[str] = mapped_column(String(40), index=True)
    recipient_address: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    idempotency_key: Mapped[str] = mapped_column(String(160))
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    lease_owner: Mapped[str | None] = mapped_column(String(100), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class MonitoringSourceContract(Base):
    """Versioned ingestion semantics for incremental and reconciliation reads."""

    __tablename__ = "monitoring_source_contract"
    __table_args__ = (
        UniqueConstraint("source_id", "table_name", "version", name="uq_monitoring_source_contract"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    source_id: Mapped[int] = mapped_column(Integer, ForeignKey("data_source.id", ondelete="CASCADE"), index=True)
    table_name: Mapped[str] = mapped_column(String(160))
    version: Mapped[int] = mapped_column(Integer, default=1)
    ingestion_mode: Mapped[str] = mapped_column(String(24), default="append")
    business_time_field: Mapped[str] = mapped_column(String(160))
    updated_time_field: Mapped[str | None] = mapped_column(String(160), nullable=True)
    primary_key_fields_json: Mapped[list] = mapped_column(JSON_TYPE, default=list)
    allowed_lateness_seconds: Mapped[int] = mapped_column(Integer, default=0)
    lookback_seconds: Mapped[int] = mapped_column(Integer, default=0)
    reconciliation_seconds: Mapped[int] = mapped_column(Integer, default=86400)
    status: Mapped[str] = mapped_column(String(24), default="draft", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
