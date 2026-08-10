"""平台库 ORM 模型。所有表都在 forge_sight_app，业务库 forge_sight 无 ORM（只读裸 SQL）。"""

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, JSON, Numeric, String, Text, UniqueConstraint
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

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    template_id: Mapped[str] = mapped_column(String(40), index=True)
    source_id: Mapped[int] = mapped_column(Integer, index=True)
    report_type: Mapped[str] = mapped_column(String(24), default="weekly")
    topic: Mapped[str] = mapped_column(String(40), default="综合")
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    owner_id: Mapped[str] = mapped_column(String(64), default="local-test-user", index=True)
    reviewer_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
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
    summary: Mapped[str] = mapped_column(Text, default="")
    content_json: Mapped[dict] = mapped_column(JSON_TYPE, default=dict)
    data_snapshot_json: Mapped[dict] = mapped_column(JSON_TYPE, default=dict)
    created_by: Mapped[str] = mapped_column(String(64), default="report-agent")
    approved_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


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


class ReportSubscription(Base):
    """周期报告订阅。第一版外部渠道实现飞书群机器人。"""

    __tablename__ = "report_subscription"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    template_id: Mapped[str] = mapped_column(String(40), index=True)
    source_id: Mapped[int] = mapped_column(Integer, index=True)
    owner_id: Mapped[str] = mapped_column(String(64), default="local-test-user", index=True)
    cadence: Mapped[str] = mapped_column(String(20), default="weekly")
    weekday: Mapped[int | None] = mapped_column(Integer, nullable=True)
    day_of_month: Mapped[int | None] = mapped_column(Integer, nullable=True)
    send_time: Mapped[str] = mapped_column(String(8), default="09:00")
    timezone: Mapped[str] = mapped_column(String(50), default="Asia/Shanghai")
    channel: Mapped[str] = mapped_column(String(24), default="feishu")
    publish_policy: Mapped[str] = mapped_column(String(24), default="after_review")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class ReportDelivery(Base):
    """报告每一次外部发送的结果与重试记录。"""

    __tablename__ = "report_delivery"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    report_id: Mapped[str] = mapped_column(String(40), index=True)
    version_id: Mapped[str] = mapped_column(String(40), index=True)
    subscription_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    channel: Mapped[str] = mapped_column(String(24), default="feishu")
    status: Mapped[str] = mapped_column(String(24), default="queued")
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    response_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    error_json: Mapped[dict | None] = mapped_column(JSON_TYPE, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


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
