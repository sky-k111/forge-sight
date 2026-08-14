-- Incremental, non-destructive domain model migration for weekly reports,
-- messages, announcements, delivery, organization scope, and Outbox.

ALTER TABLE IF EXISTS report
    ADD COLUMN IF NOT EXISTS subscription_id VARCHAR(40),
    ADD COLUMN IF NOT EXISTS schedule_key VARCHAR(80),
    ADD COLUMN IF NOT EXISTS audience_scope_hash VARCHAR(64),
    ADD COLUMN IF NOT EXISTS publish_source VARCHAR(32);

CREATE INDEX IF NOT EXISTS ix_report_subscription_id ON report (subscription_id);
CREATE INDEX IF NOT EXISTS ix_report_schedule_key ON report (schedule_key);
CREATE INDEX IF NOT EXISTS ix_report_audience_scope_hash ON report (audience_scope_hash);
CREATE INDEX IF NOT EXISTS ix_report_period_template
    ON report (period_start, period_end, template_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_report_schedule_audience
    ON report (subscription_id, schedule_key, audience_scope_hash);

ALTER TABLE IF EXISTS report_version
    ADD COLUMN IF NOT EXISTS supersedes_version_id VARCHAR(40),
    ADD COLUMN IF NOT EXISTS validation_json JSONB,
    ADD COLUMN IF NOT EXISTS workflow_version VARCHAR(64),
    ADD COLUMN IF NOT EXISTS model_snapshot_json JSONB,
    ADD COLUMN IF NOT EXISTS data_quality_json JSONB;

DO $migration$
BEGIN
    IF to_regclass('report_version') IS NOT NULL
       AND NOT EXISTS (
           SELECT 1 FROM pg_constraint
           WHERE conname = 'fk_report_version_supersedes'
             AND conrelid = 'report_version'::regclass
       ) THEN
        ALTER TABLE report_version
            ADD CONSTRAINT fk_report_version_supersedes
            FOREIGN KEY (supersedes_version_id)
            REFERENCES report_version (id) ON DELETE RESTRICT;
    END IF;
END
$migration$;

CREATE TABLE IF NOT EXISTS report_file (
    id VARCHAR(40) PRIMARY KEY,
    report_id VARCHAR(40) NOT NULL,
    version_id VARCHAR(40) NOT NULL,
    format VARCHAR(12) NOT NULL,
    status VARCHAR(24) NOT NULL,
    storage_uri TEXT,
    mime_type VARCHAR(120),
    file_size BIGINT,
    checksum VARCHAR(128),
    generation_attempt INTEGER NOT NULL,
    error_json JSONB,
    next_retry_at TIMESTAMP WITHOUT TIME ZONE,
    lease_owner VARCHAR(80),
    lease_expires_at TIMESTAMP WITHOUT TIME ZONE,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    generated_at TIMESTAMP WITHOUT TIME ZONE,
    CONSTRAINT uq_report_file_version_format UNIQUE (version_id, format),
    CONSTRAINT ck_report_file_format CHECK (format IN ('pdf', 'xlsx')),
    CONSTRAINT ck_report_file_status
        CHECK (status IN ('queued', 'generating', 'ready', 'failed', 'dead_letter'))
);
-- 早期版本已经存在精简 report_file 表。CREATE TABLE IF NOT EXISTS 不会为其补列，
-- 因此必须先做增量 ALTER，再创建依赖这些列的索引。
ALTER TABLE IF EXISTS report_file
    ADD COLUMN IF NOT EXISTS next_retry_at TIMESTAMP WITHOUT TIME ZONE,
    ADD COLUMN IF NOT EXISTS lease_owner VARCHAR(80),
    ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMP WITHOUT TIME ZONE;
CREATE INDEX IF NOT EXISTS ix_report_file_report_id ON report_file (report_id);
CREATE INDEX IF NOT EXISTS ix_report_file_version_id ON report_file (version_id);
CREATE INDEX IF NOT EXISTS ix_report_file_status ON report_file (status);
CREATE INDEX IF NOT EXISTS ix_report_file_next_retry_at ON report_file (next_retry_at);
CREATE INDEX IF NOT EXISTS ix_report_file_lease_owner ON report_file (lease_owner);
CREATE INDEX IF NOT EXISTS ix_report_file_lease_expires_at ON report_file (lease_expires_at);

CREATE TABLE IF NOT EXISTS message (
    id VARCHAR(40) PRIMARY KEY,
    message_type VARCHAR(32) NOT NULL,
    source VARCHAR(32) NOT NULL,
    title VARCHAR(200) NOT NULL,
    summary TEXT NOT NULL,
    related_type VARCHAR(40),
    related_id VARCHAR(40),
    report_version_id VARCHAR(40),
    priority VARCHAR(16) NOT NULL,
    is_pinned BOOLEAN NOT NULL,
    status VARCHAR(24) NOT NULL,
    idempotency_key VARCHAR(160) NOT NULL UNIQUE,
    valid_from TIMESTAMP WITHOUT TIME ZONE,
    expires_at TIMESTAMP WITHOUT TIME ZONE,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    published_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    CONSTRAINT ck_message_status
        CHECK (status IN ('published', 'withdrawn', 'expired'))
);
CREATE INDEX IF NOT EXISTS ix_message_message_type ON message (message_type);
CREATE INDEX IF NOT EXISTS ix_message_source ON message (source);
CREATE INDEX IF NOT EXISTS ix_message_report_version_id ON message (report_version_id);
CREATE INDEX IF NOT EXISTS ix_message_status ON message (status);
CREATE INDEX IF NOT EXISTS ix_message_expires_at ON message (expires_at);

CREATE TABLE IF NOT EXISTS message_recipient (
    id VARCHAR(40) PRIMARY KEY,
    message_id VARCHAR(40) NOT NULL,
    user_id VARCHAR(40) NOT NULL,
    message_status_snapshot VARCHAR(24) NOT NULL,
    is_read BOOLEAN NOT NULL,
    read_at TIMESTAMP WITHOUT TIME ZONE,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    CONSTRAINT uq_message_recipient_user UNIQUE (message_id, user_id),
    CONSTRAINT fk_message_recipient_message
        FOREIGN KEY (message_id) REFERENCES message (id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_message_recipient_message_id
    ON message_recipient (message_id);
CREATE INDEX IF NOT EXISTS ix_message_recipient_user_id
    ON message_recipient (user_id);
CREATE INDEX IF NOT EXISTS ix_message_recipient_is_read
    ON message_recipient (is_read);

CREATE TABLE IF NOT EXISTS announcement (
    id VARCHAR(40) PRIMARY KEY,
    supersedes_announcement_id VARCHAR(40),
    revision INTEGER NOT NULL,
    title VARCHAR(200) NOT NULL,
    body TEXT NOT NULL,
    priority VARCHAR(16) NOT NULL,
    is_pinned BOOLEAN NOT NULL,
    audience_json JSONB NOT NULL,
    audience_scope_hash VARCHAR(64) NOT NULL,
    status VARCHAR(24) NOT NULL,
    created_by VARCHAR(40) NOT NULL,
    published_by VARCHAR(40),
    withdrawn_by VARCHAR(40),
    valid_from TIMESTAMP WITHOUT TIME ZONE,
    expires_at TIMESTAMP WITHOUT TIME ZONE,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    published_at TIMESTAMP WITHOUT TIME ZONE,
    withdrawn_at TIMESTAMP WITHOUT TIME ZONE,
    CONSTRAINT uq_announcement_revision UNIQUE (id, revision),
    CONSTRAINT ck_announcement_status
        CHECK (status IN ('draft', 'published', 'withdrawn', 'expired'))
);
CREATE INDEX IF NOT EXISTS ix_announcement_audience_scope_hash
    ON announcement (audience_scope_hash);
CREATE INDEX IF NOT EXISTS ix_announcement_status ON announcement (status);
CREATE INDEX IF NOT EXISTS ix_announcement_expires_at ON announcement (expires_at);

ALTER TABLE IF EXISTS report_delivery
    ADD COLUMN IF NOT EXISTS attempt_count INTEGER,
    ADD COLUMN IF NOT EXISTS recipient_user_id VARCHAR(40),
    ADD COLUMN IF NOT EXISTS recipient_address_snapshot VARCHAR(254),
    ADD COLUMN IF NOT EXISTS idempotency_key VARCHAR(160),
    ADD COLUMN IF NOT EXISTS next_retry_at TIMESTAMP WITHOUT TIME ZONE,
    ADD COLUMN IF NOT EXISTS error_category VARCHAR(64),
    ADD COLUMN IF NOT EXISTS provider_message_id VARCHAR(160);

CREATE INDEX IF NOT EXISTS ix_report_delivery_recipient_user_id
    ON report_delivery (recipient_user_id);
CREATE INDEX IF NOT EXISTS ix_report_delivery_next_retry_at
    ON report_delivery (next_retry_at);
CREATE UNIQUE INDEX IF NOT EXISTS uq_report_delivery_idempotency_key
    ON report_delivery (idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_report_delivery_version_user_channel
    ON report_delivery (version_id, recipient_user_id, channel)
    WHERE recipient_user_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS delivery_attempt (
    id VARCHAR(40) PRIMARY KEY,
    delivery_id VARCHAR(40) NOT NULL,
    attempt_number INTEGER NOT NULL,
    status VARCHAR(24) NOT NULL,
    provider_message_id VARCHAR(160),
    error_category VARCHAR(64),
    error_json JSONB,
    started_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    finished_at TIMESTAMP WITHOUT TIME ZONE,
    CONSTRAINT uq_delivery_attempt_number UNIQUE (delivery_id, attempt_number),
    CONSTRAINT fk_delivery_attempt_delivery
        FOREIGN KEY (delivery_id) REFERENCES report_delivery (id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_delivery_attempt_delivery_id
    ON delivery_attempt (delivery_id);

CREATE TABLE IF NOT EXISTS organization_unit (
    id VARCHAR(40) PRIMARY KEY,
    code VARCHAR(80) NOT NULL UNIQUE,
    name VARCHAR(160) NOT NULL,
    node_type VARCHAR(32) NOT NULL,
    parent_id VARCHAR(40),
    path VARCHAR(500) NOT NULL,
    enabled BOOLEAN NOT NULL,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    CONSTRAINT fk_organization_unit_parent
        FOREIGN KEY (parent_id) REFERENCES organization_unit (id) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS ix_organization_unit_node_type
    ON organization_unit (node_type);
CREATE INDEX IF NOT EXISTS ix_organization_unit_parent_id
    ON organization_unit (parent_id);
CREATE INDEX IF NOT EXISTS ix_organization_unit_path ON organization_unit (path);

CREATE TABLE IF NOT EXISTS user_membership (
    id VARCHAR(40) PRIMARY KEY,
    user_id VARCHAR(40) NOT NULL,
    organization_id VARCHAR(40) NOT NULL,
    position_code VARCHAR(80) NOT NULL,
    business_role VARCHAR(80) NOT NULL,
    effective_from TIMESTAMP WITHOUT TIME ZONE,
    effective_to TIMESTAMP WITHOUT TIME ZONE,
    enabled BOOLEAN NOT NULL,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    CONSTRAINT uq_user_membership_position
        UNIQUE (user_id, organization_id, position_code),
    CONSTRAINT fk_user_membership_organization
        FOREIGN KEY (organization_id) REFERENCES organization_unit (id) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS ix_user_membership_user_id ON user_membership (user_id);
CREATE INDEX IF NOT EXISTS ix_user_membership_organization_id
    ON user_membership (organization_id);
CREATE INDEX IF NOT EXISTS ix_user_membership_position_code
    ON user_membership (position_code);
CREATE INDEX IF NOT EXISTS ix_user_membership_business_role
    ON user_membership (business_role);

CREATE TABLE IF NOT EXISTS data_scope_policy (
    id VARCHAR(40) PRIMARY KEY,
    subject_type VARCHAR(24) NOT NULL,
    subject_id VARCHAR(80) NOT NULL,
    business_key VARCHAR(80) NOT NULL,
    effect VARCHAR(16) NOT NULL,
    data_source_ids_json JSONB NOT NULL,
    dimensions_json JSONB NOT NULL,
    effective_from TIMESTAMP WITHOUT TIME ZONE,
    effective_to TIMESTAMP WITHOUT TIME ZONE,
    enabled BOOLEAN NOT NULL,
    created_by VARCHAR(40) NOT NULL,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    CONSTRAINT uq_data_scope_subject_business
        UNIQUE (subject_type, subject_id, business_key)
);
CREATE INDEX IF NOT EXISTS ix_data_scope_policy_subject_type
    ON data_scope_policy (subject_type);
CREATE INDEX IF NOT EXISTS ix_data_scope_policy_subject_id
    ON data_scope_policy (subject_id);
CREATE INDEX IF NOT EXISTS ix_data_scope_policy_business_key
    ON data_scope_policy (business_key);

CREATE TABLE IF NOT EXISTS domain_event_outbox (
    id VARCHAR(40) PRIMARY KEY,
    event_type VARCHAR(100) NOT NULL,
    aggregate_id VARCHAR(80) NOT NULL,
    idempotency_key VARCHAR(160) NOT NULL UNIQUE,
    payload_json JSONB NOT NULL,
    status VARCHAR(24) NOT NULL,
    lease_owner VARCHAR(100),
    lease_expires_at TIMESTAMP WITHOUT TIME ZONE,
    attempt_count INTEGER NOT NULL,
    next_retry_at TIMESTAMP WITHOUT TIME ZONE,
    acknowledged_at TIMESTAMP WITHOUT TIME ZONE,
    error_summary VARCHAR(500),
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    CONSTRAINT ck_outbox_attempt_count CHECK (attempt_count >= 0),
    CONSTRAINT ck_outbox_status CHECK (
        status IN ('pending', 'processing', 'retry_wait', 'completed', 'dead_letter')
    )
);
CREATE INDEX IF NOT EXISTS ix_domain_event_outbox_event_type
    ON domain_event_outbox (event_type);
CREATE INDEX IF NOT EXISTS ix_domain_event_outbox_aggregate_id
    ON domain_event_outbox (aggregate_id);
CREATE INDEX IF NOT EXISTS ix_domain_event_outbox_status
    ON domain_event_outbox (status);
CREATE INDEX IF NOT EXISTS ix_outbox_claim
    ON domain_event_outbox (status, next_retry_at, lease_expires_at);
