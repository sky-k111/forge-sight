-- Governed Experience Learning Loop: procedural analysis skills and A/B switch.

ALTER TABLE analysis_run
    ADD COLUMN IF NOT EXISTS skill_enhancement_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS skill_experiment_variant VARCHAR(16) NOT NULL DEFAULT 'off';

CREATE TABLE IF NOT EXISTS platform_feature_flag (
    key VARCHAR(64) PRIMARY KEY,
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    config_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_by VARCHAR(64) NOT NULL DEFAULT 'system',
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS analysis_skill (
    id VARCHAR(40) PRIMARY KEY,
    source_id INTEGER NULL,
    name VARCHAR(160) NOT NULL,
    slug VARCHAR(120) NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    topic VARCHAR(50) NOT NULL DEFAULT '通用分析',
    status VARCHAR(20) NOT NULL DEFAULT 'candidate',
    version INTEGER NOT NULL DEFAULT 1,
    trigger_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    preconditions_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    procedure_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    output_policy_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    constraints_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    evidence_run_ids_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    confidence NUMERIC(5,4) NOT NULL DEFAULT 0,
    created_by VARCHAR(64) NOT NULL DEFAULT 'system',
    reviewed_by VARCHAR(64),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    published_at TIMESTAMP,
    retired_at TIMESTAMP,
    CONSTRAINT uq_analysis_skill_version UNIQUE (slug, source_id, version)
);
CREATE INDEX IF NOT EXISTS ix_analysis_skill_source_id ON analysis_skill(source_id);
CREATE INDEX IF NOT EXISTS ix_analysis_skill_status ON analysis_skill(status);
CREATE INDEX IF NOT EXISTS ix_analysis_skill_slug ON analysis_skill(slug);

CREATE TABLE IF NOT EXISTS learning_feedback (
    id VARCHAR(40) PRIMARY KEY,
    run_id VARCHAR(40) NOT NULL,
    user_id VARCHAR(64) NOT NULL DEFAULT 'local-test-user',
    rating VARCHAR(24) NOT NULL,
    correction_type VARCHAR(40) NOT NULL DEFAULT '',
    comment TEXT NOT NULL DEFAULT '',
    candidate_skill_id VARCHAR(40),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_learning_feedback_run_id ON learning_feedback(run_id);

CREATE TABLE IF NOT EXISTS analysis_skill_usage (
    id VARCHAR(40) PRIMARY KEY,
    run_id VARCHAR(40) NOT NULL,
    task_id VARCHAR(40),
    skill_id VARCHAR(40) NOT NULL,
    variant VARCHAR(16) NOT NULL DEFAULT 'on',
    relevance_score NUMERIC(8,6) NOT NULL DEFAULT 0,
    applied BOOLEAN NOT NULL DEFAULT TRUE,
    outcome VARCHAR(24),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMP,
    CONSTRAINT uq_analysis_skill_usage UNIQUE (run_id, task_id, skill_id)
);
CREATE INDEX IF NOT EXISTS ix_analysis_skill_usage_run_id ON analysis_skill_usage(run_id);
CREATE INDEX IF NOT EXISTS ix_analysis_skill_usage_skill_id ON analysis_skill_usage(skill_id);

INSERT INTO platform_feature_flag(key, enabled, config_json, updated_by)
VALUES (
    'analysis_skill_enhancement', FALSE,
    '{"topK":2,"minimumScore":0.18,"candidateOnPositiveFeedback":true}'::jsonb,
    'migration'
)
ON CONFLICT (key) DO NOTHING;
