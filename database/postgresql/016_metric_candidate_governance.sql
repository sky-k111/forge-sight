-- Governed metric candidate lifecycle.
-- AI proposals are isolated from published knowledge until an administrator accepts
-- the candidate and explicitly publishes the resulting pending metric.

CREATE TABLE IF NOT EXISTS metric_candidate (
    id VARCHAR(36) PRIMARY KEY,
    source_id INTEGER NOT NULL,
    candidate_kind VARCHAR(24) NOT NULL DEFAULT 'new_metric',
    target_metric_id VARCHAR(20),
    name VARCHAR(100) NOT NULL,
    topic VARCHAR(50) NOT NULL,
    canonical_payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    binding_payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    evidence_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    validation_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    confidence_score INTEGER NOT NULL DEFAULT 0,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    fingerprint VARCHAR(64) NOT NULL,
    generator_version VARCHAR(32) NOT NULL DEFAULT 'metric-candidate-v1',
    review_note TEXT NOT NULL DEFAULT '',
    reviewed_by VARCHAR(64),
    accepted_metric_id VARCHAR(20),
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reviewed_at TIMESTAMP WITHOUT TIME ZONE,
    CONSTRAINT uq_metric_candidate_source_fingerprint UNIQUE (source_id, fingerprint),
    CONSTRAINT ck_metric_candidate_kind CHECK (candidate_kind IN ('new_metric', 'source_binding')),
    CONSTRAINT ck_metric_candidate_status CHECK (status IN ('pending', 'accepted', 'rejected', 'superseded')),
    CONSTRAINT ck_metric_candidate_confidence CHECK (confidence_score BETWEEN 0 AND 100)
);

CREATE INDEX IF NOT EXISTS ix_metric_candidate_source_id ON metric_candidate (source_id);
CREATE INDEX IF NOT EXISTS ix_metric_candidate_status ON metric_candidate (status);
CREATE INDEX IF NOT EXISTS ix_metric_candidate_target_metric_id ON metric_candidate (target_metric_id);
