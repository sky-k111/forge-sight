-- Competition-grade AutoML round two: data readiness, tuning provenance,
-- business threshold and explanation snapshots.

ALTER TABLE modeling_task
    ADD COLUMN IF NOT EXISTS diagnostics_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS diagnosed_at TIMESTAMP;

ALTER TABLE governed_model
    ADD COLUMN IF NOT EXISTS decision_threshold NUMERIC(6,4),
    ADD COLUMN IF NOT EXISTS threshold_policy_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS tuning_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS explanation_json JSONB NOT NULL DEFAULT '{}'::jsonb;

CREATE INDEX IF NOT EXISTS ix_modeling_task_diagnosed_at
    ON modeling_task (diagnosed_at);
