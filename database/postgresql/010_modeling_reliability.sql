-- Stage 7 reliability: leakage-safe split metadata, immutable training history,
-- self-contained artifact versioning, and production model traceability.

ALTER TABLE modeling_task
    ADD COLUMN IF NOT EXISTS split_strategy VARCHAR(16) NOT NULL DEFAULT 'auto',
    ADD COLUMN IF NOT EXISTS time_field VARCHAR(64) NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS group_field VARCHAR(64) NOT NULL DEFAULT '';

ALTER TABLE governed_model
    ADD COLUMN IF NOT EXISTS training_revision INTEGER NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS artifact_format_version INTEGER NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS artifact_checksum VARCHAR(64) NOT NULL DEFAULT '';

ALTER TABLE modeling_evaluation
    DROP CONSTRAINT IF EXISTS uq_modeling_eval_task;

ALTER TABLE modeling_evaluation
    ADD COLUMN IF NOT EXISTS training_revision INTEGER NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS model_ids_json JSONB NOT NULL DEFAULT '[]'::jsonb;

-- Existing installations had one evaluation per task. Backfill its model links
-- without rewriting historical metrics.
UPDATE modeling_evaluation evaluation
SET model_ids_json = COALESCE((
    SELECT jsonb_agg(model.id ORDER BY model.created_at)
    FROM governed_model model
    WHERE model.task_id = evaluation.task_id
), '[]'::jsonb)
WHERE model_ids_json = '[]'::jsonb;

-- Old builds reused version=1 on every retrain. Normalize historical rows
-- deterministically before enforcing concurrency-safe uniqueness.
WITH ranked AS (
    SELECT id,
           ROW_NUMBER() OVER (
               PARTITION BY task_id, algorithm
               ORDER BY created_at, id
           ) AS normalized_version
    FROM governed_model
)
UPDATE governed_model model
SET version = ranked.normalized_version
FROM ranked
WHERE model.id = ranked.id
  AND model.version <> ranked.normalized_version;

CREATE INDEX IF NOT EXISTS ix_modeling_evaluation_training_revision
    ON modeling_evaluation (training_revision);
CREATE INDEX IF NOT EXISTS ix_governed_model_training_revision
    ON governed_model (training_revision);
CREATE UNIQUE INDEX IF NOT EXISTS uq_governed_model_task_algorithm_version
    ON governed_model (task_id, algorithm, version);
CREATE UNIQUE INDEX IF NOT EXISTS uq_modeling_evaluation_task_revision
    ON modeling_evaluation (task_id, training_revision);
