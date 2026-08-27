-- 智能异常监控与警报：规则、可靠任务、观测、警报、证据和投递。
-- 仅负责监控与通知，不包含人工确认、指派、行动或业务验收。

CREATE TABLE IF NOT EXISTS monitoring_rule_template (
    id VARCHAR(40) PRIMARY KEY,
    code VARCHAR(80) NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    name VARCHAR(160) NOT NULL,
    topic VARCHAR(80) NOT NULL DEFAULT 'production',
    description TEXT NOT NULL DEFAULT '',
    default_condition_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    default_quality_gate_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    status VARCHAR(24) NOT NULL DEFAULT 'published',
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_monitoring_template_version UNIQUE (code, version)
);

CREATE TABLE IF NOT EXISTS monitoring_rule (
    id VARCHAR(40) PRIMARY KEY,
    code VARCHAR(80) NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    template_code VARCHAR(80) NOT NULL,
    source_id INTEGER NOT NULL REFERENCES data_source(id) ON DELETE RESTRICT,
    name VARCHAR(160) NOT NULL,
    metric_name VARCHAR(120) NOT NULL,
    entity_type VARCHAR(80) NOT NULL DEFAULT 'source',
    grain VARCHAR(24) NOT NULL DEFAULT 'day',
    query_plan_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    condition_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    quality_gate_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    severity_policy_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    notification_policy_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    schedule_seconds INTEGER NOT NULL DEFAULT 86400,
    status VARCHAR(24) NOT NULL DEFAULT 'draft',
    created_by VARCHAR(40) NOT NULL DEFAULT 'system',
    published_by VARCHAR(40),
    published_at TIMESTAMP WITHOUT TIME ZONE,
    last_scheduled_at TIMESTAMP WITHOUT TIME ZONE,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_monitoring_rule_version UNIQUE (code, version, source_id),
    CONSTRAINT ck_monitoring_rule_status CHECK (status IN ('draft', 'published', 'paused', 'retired'))
);
CREATE INDEX IF NOT EXISTS ix_monitoring_rule_status ON monitoring_rule(status);
CREATE INDEX IF NOT EXISTS ix_monitoring_rule_source_id ON monitoring_rule(source_id);
CREATE INDEX IF NOT EXISTS ix_monitoring_rule_template_ref ON monitoring_rule(template_code);

CREATE TABLE IF NOT EXISTS monitoring_cursor (
    id VARCHAR(40) PRIMARY KEY,
    rule_id VARCHAR(40) NOT NULL REFERENCES monitoring_rule(id) ON DELETE CASCADE,
    source_id INTEGER NOT NULL,
    logical_period VARCHAR(80) NOT NULL DEFAULT '',
    watermark_at TIMESTAMP WITHOUT TIME ZONE,
    task_id VARCHAR(40),
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_monitoring_cursor_rule UNIQUE (rule_id)
);
CREATE INDEX IF NOT EXISTS ix_monitoring_cursor_rule_id ON monitoring_cursor(rule_id);
CREATE INDEX IF NOT EXISTS ix_monitoring_cursor_source_id ON monitoring_cursor(source_id);

CREATE TABLE IF NOT EXISTS monitoring_task (
    id VARCHAR(40) PRIMARY KEY,
    rule_id VARCHAR(40) NOT NULL REFERENCES monitoring_rule(id) ON DELETE CASCADE,
    rule_version INTEGER NOT NULL,
    logical_period VARCHAR(80) NOT NULL,
    window_start TIMESTAMP WITHOUT TIME ZONE,
    window_end TIMESTAMP WITHOUT TIME ZONE,
    status VARCHAR(24) NOT NULL DEFAULT 'queued',
    idempotency_key VARCHAR(160) NOT NULL UNIQUE,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    next_attempt_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    lease_owner VARCHAR(100),
    lease_expires_at TIMESTAMP WITHOUT TIME ZONE,
    error_summary VARCHAR(500),
    result_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP WITHOUT TIME ZONE,
    finished_at TIMESTAMP WITHOUT TIME ZONE,
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_monitoring_task_claim
    ON monitoring_task(status, next_attempt_at, lease_expires_at);

CREATE TABLE IF NOT EXISTS monitoring_observation (
    id VARCHAR(40) PRIMARY KEY,
    rule_id VARCHAR(40) NOT NULL REFERENCES monitoring_rule(id) ON DELETE CASCADE,
    rule_version INTEGER NOT NULL,
    source_id INTEGER NOT NULL,
    logical_period VARCHAR(80) NOT NULL,
    entity_type VARCHAR(80) NOT NULL,
    entity_id VARCHAR(160) NOT NULL,
    entity_name VARCHAR(200) NOT NULL DEFAULT '',
    current_value NUMERIC(24, 8),
    baseline_value NUMERIC(24, 8),
    lower_bound NUMERIC(24, 8),
    upper_bound NUMERIC(24, 8),
    sample_size INTEGER NOT NULL DEFAULT 0,
    quality_status VARCHAR(24) NOT NULL DEFAULT 'ok',
    evaluation_state VARCHAR(24) NOT NULL,
    data_cutoff_at TIMESTAMP WITHOUT TIME ZONE,
    details_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    idempotency_key VARCHAR(160) NOT NULL UNIQUE,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_monitoring_observation_rule_period
    ON monitoring_observation(rule_id, logical_period);

CREATE TABLE IF NOT EXISTS monitoring_alert (
    id VARCHAR(40) PRIMARY KEY,
    rule_id VARCHAR(40) NOT NULL REFERENCES monitoring_rule(id) ON DELETE RESTRICT,
    rule_version INTEGER NOT NULL,
    source_id INTEGER NOT NULL,
    metric_name VARCHAR(120) NOT NULL,
    entity_type VARCHAR(80) NOT NULL,
    entity_id VARCHAR(160) NOT NULL,
    entity_name VARCHAR(200) NOT NULL DEFAULT '',
    title VARCHAR(240) NOT NULL,
    severity VARCHAR(16) NOT NULL DEFAULT 'medium',
    status VARCHAR(24) NOT NULL DEFAULT 'firing',
    correlation_key VARCHAR(160) NOT NULL UNIQUE,
    current_value NUMERIC(24, 8),
    baseline_value NUMERIC(24, 8),
    sample_size INTEGER NOT NULL DEFAULT 0,
    pending_count INTEGER NOT NULL DEFAULT 0,
    recovery_count INTEGER NOT NULL DEFAULT 0,
    recurrence_count INTEGER NOT NULL DEFAULT 0,
    first_seen_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolved_at TIMESTAMP WITHOUT TIME ZONE,
    latest_observation_id VARCHAR(40),
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT ck_monitoring_alert_status CHECK (status IN ('firing', 'recovering', 'resolved'))
);
CREATE INDEX IF NOT EXISTS ix_monitoring_alert_filter
    ON monitoring_alert(status, severity, last_seen_at);
CREATE INDEX IF NOT EXISTS ix_monitoring_alert_entity_id ON monitoring_alert(entity_id);

CREATE TABLE IF NOT EXISTS monitoring_alert_evidence (
    id VARCHAR(40) PRIMARY KEY,
    alert_id VARCHAR(40) NOT NULL REFERENCES monitoring_alert(id) ON DELETE CASCADE,
    observation_id VARCHAR(40) NOT NULL REFERENCES monitoring_observation(id) ON DELETE CASCADE,
    evidence_type VARCHAR(32) NOT NULL DEFAULT 'observation',
    snapshot_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_monitoring_alert_evidence UNIQUE (alert_id, observation_id)
);

CREATE TABLE IF NOT EXISTS monitoring_alert_delivery (
    id VARCHAR(40) PRIMARY KEY,
    alert_id VARCHAR(40) NOT NULL REFERENCES monitoring_alert(id) ON DELETE CASCADE,
    event_type VARCHAR(80) NOT NULL,
    channel VARCHAR(24) NOT NULL,
    recipient_user_id VARCHAR(40) NOT NULL,
    recipient_address VARCHAR(200),
    status VARCHAR(24) NOT NULL DEFAULT 'queued',
    idempotency_key VARCHAR(160) NOT NULL UNIQUE,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    next_attempt_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    lease_owner VARCHAR(100),
    lease_expires_at TIMESTAMP WITHOUT TIME ZONE,
    provider_message_id VARCHAR(200),
    error_summary VARCHAR(500),
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    sent_at TIMESTAMP WITHOUT TIME ZONE,
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_monitoring_delivery_claim
    ON monitoring_alert_delivery(status, next_attempt_at, lease_expires_at);

CREATE TABLE IF NOT EXISTS monitoring_source_contract (
    id VARCHAR(40) PRIMARY KEY,
    source_id INTEGER NOT NULL REFERENCES data_source(id) ON DELETE CASCADE,
    table_name VARCHAR(160) NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    ingestion_mode VARCHAR(24) NOT NULL DEFAULT 'append',
    business_time_field VARCHAR(160) NOT NULL,
    updated_time_field VARCHAR(160),
    primary_key_fields_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    allowed_lateness_seconds INTEGER NOT NULL DEFAULT 0,
    lookback_seconds INTEGER NOT NULL DEFAULT 0,
    reconciliation_seconds INTEGER NOT NULL DEFAULT 86400,
    status VARCHAR(24) NOT NULL DEFAULT 'draft',
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_monitoring_source_contract UNIQUE (source_id, table_name, version)
);
