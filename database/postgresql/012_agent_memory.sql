-- Governed agent memory: scoped long-term memories, evidence, lifecycle and A/B observation.

ALTER TABLE analysis_run
    ADD COLUMN IF NOT EXISTS memory_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS memory_experiment_variant VARCHAR(16) NOT NULL DEFAULT 'off';

CREATE TABLE IF NOT EXISTS agent_memory (
    id VARCHAR(40) PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL DEFAULT 'default',
    source_id INTEGER,
    user_id VARCHAR(64),
    role_scope VARCHAR(32),
    session_id VARCHAR(40),
    channel VARCHAR(24) NOT NULL DEFAULT 'web',
    memory_kind VARCHAR(24) NOT NULL,
    status VARCHAR(24) NOT NULL DEFAULT 'candidate',
    title VARCHAR(200) NOT NULL DEFAULT '',
    content TEXT NOT NULL,
    normalized_content TEXT NOT NULL DEFAULT '',
    keywords_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    confidence NUMERIC(5,4) NOT NULL DEFAULT 0.7 CHECK (confidence >= 0 AND confidence <= 1),
    importance NUMERIC(5,4) NOT NULL DEFAULT 0.5 CHECK (importance >= 0 AND importance <= 1),
    explicit BOOLEAN NOT NULL DEFAULT FALSE,
    valid_from TIMESTAMP,
    valid_until TIMESTAMP,
    last_accessed_at TIMESTAMP,
    access_count INTEGER NOT NULL DEFAULT 0,
    supersedes_id VARCHAR(40),
    created_by VARCHAR(64) NOT NULL DEFAULT 'system',
    reviewed_by VARCHAR(64),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_agent_memory_scope ON agent_memory (tenant_id, source_id, user_id, status);
CREATE INDEX IF NOT EXISTS ix_agent_memory_kind_status ON agent_memory (memory_kind, status);
CREATE INDEX IF NOT EXISTS ix_agent_memory_valid_until ON agent_memory (valid_until);

CREATE TABLE IF NOT EXISTS agent_memory_evidence (
    id VARCHAR(40) PRIMARY KEY, memory_id VARCHAR(40) NOT NULL, evidence_type VARCHAR(32) NOT NULL,
    reference_id VARCHAR(80) NOT NULL, excerpt TEXT NOT NULL DEFAULT '', metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_agent_memory_evidence_memory ON agent_memory_evidence (memory_id, created_at);

CREATE TABLE IF NOT EXISTS agent_memory_relation (
    id VARCHAR(40) PRIMARY KEY, from_memory_id VARCHAR(40) NOT NULL, to_memory_id VARCHAR(40) NOT NULL,
    relation_type VARCHAR(32) NOT NULL, metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_agent_memory_relation UNIQUE (from_memory_id, to_memory_id, relation_type)
);

CREATE TABLE IF NOT EXISTS agent_memory_access_log (
    id VARCHAR(40) PRIMARY KEY, memory_id VARCHAR(40) NOT NULL, run_id VARCHAR(40), user_id VARCHAR(64) NOT NULL,
    source_id INTEGER, query_text TEXT NOT NULL DEFAULT '', relevance_score NUMERIC(8,6) NOT NULL DEFAULT 0,
    used_in_context BOOLEAN NOT NULL DEFAULT FALSE, variant VARCHAR(16) NOT NULL DEFAULT 'off',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_agent_memory_access_run ON agent_memory_access_log (run_id, created_at);

CREATE TABLE IF NOT EXISTS agent_memory_feedback (
    id VARCHAR(40) PRIMARY KEY, memory_id VARCHAR(40) NOT NULL, user_id VARCHAR(64) NOT NULL,
    action VARCHAR(24) NOT NULL, comment TEXT NOT NULL DEFAULT '', created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS agent_memory_job (
    id VARCHAR(40) PRIMARY KEY, job_type VARCHAR(32) NOT NULL, reference_id VARCHAR(80) NOT NULL,
    status VARCHAR(24) NOT NULL DEFAULT 'queued', payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    attempt_count INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL DEFAULT 3, error TEXT NOT NULL DEFAULT '',
    lease_owner VARCHAR(100), lease_expires_at TIMESTAMP, created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_agent_memory_job_reference UNIQUE (job_type, reference_id)
);
CREATE INDEX IF NOT EXISTS ix_agent_memory_job_claim ON agent_memory_job (status, lease_expires_at);

CREATE TABLE IF NOT EXISTS agent_memory_policy (
    id VARCHAR(40) PRIMARY KEY, tenant_id VARCHAR(64) NOT NULL DEFAULT 'default', policy_key VARCHAR(64) NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE, config_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_by VARCHAR(64) NOT NULL DEFAULT 'system', updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_agent_memory_policy UNIQUE (tenant_id, policy_key)
);

-- The vector is physically separated so SQLite tests and metadata-only deployments remain usable.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS agent_memory_embedding (
    memory_id VARCHAR(40) PRIMARY KEY, tenant_id VARCHAR(64) NOT NULL, source_id INTEGER, user_id VARCHAR(64),
    embedding vector(1024) NOT NULL, embedding_model VARCHAR(160) NOT NULL, content_hash VARCHAR(64) NOT NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_agent_memory_embedding_scope ON agent_memory_embedding (tenant_id, source_id, user_id);

INSERT INTO platform_feature_flag (key, enabled, config_json, updated_by, updated_at)
VALUES (
    'agent_memory', FALSE,
    '{"variant":"off","topK":6,"minimumScore":0.16,"captureEpisodes":true,"captureExplicitPreferences":true,"retentionDays":180}'::jsonb,
    'migration', CURRENT_TIMESTAMP
)
ON CONFLICT (key) DO NOTHING;
