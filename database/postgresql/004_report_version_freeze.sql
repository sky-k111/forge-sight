-- V04: versioned report content, stable checksums, and database immutability.

ALTER TABLE IF EXISTS report_version
    ADD COLUMN IF NOT EXISTS schema_version VARCHAR(32) NOT NULL DEFAULT 'weekly-report/v1',
    ADD COLUMN IF NOT EXISTS content_checksum VARCHAR(64),
    ADD COLUMN IF NOT EXISTS confidence_score INTEGER,
    ADD COLUMN IF NOT EXISTS confidence_algorithm_version VARCHAR(40),
    ADD COLUMN IF NOT EXISTS frozen_at TIMESTAMP WITHOUT TIME ZONE;

ALTER TABLE IF EXISTS report_file
    ADD COLUMN IF NOT EXISTS source_content_checksum VARCHAR(64),
    ADD COLUMN IF NOT EXISTS next_retry_at TIMESTAMP WITHOUT TIME ZONE,
    ADD COLUMN IF NOT EXISTS lease_owner VARCHAR(80),
    ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMP WITHOUT TIME ZONE;

CREATE INDEX IF NOT EXISTS ix_report_file_next_retry_at ON report_file (next_retry_at);
CREATE INDEX IF NOT EXISTS ix_report_file_lease_owner ON report_file (lease_owner);
CREATE INDEX IF NOT EXISTS ix_report_file_lease_expires_at ON report_file (lease_expires_at);

ALTER TABLE IF EXISTS report_file DROP CONSTRAINT IF EXISTS ck_report_file_status;
ALTER TABLE IF EXISTS report_file
    ADD CONSTRAINT ck_report_file_status
    CHECK (status IN ('queued', 'generating', 'ready', 'failed', 'dead_letter'));

CREATE OR REPLACE FUNCTION prevent_published_report_version_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $freeze$
BEGIN
    IF OLD.status = 'published' AND (
        NEW.summary IS DISTINCT FROM OLD.summary
        OR NEW.content_json IS DISTINCT FROM OLD.content_json
        OR NEW.data_snapshot_json IS DISTINCT FROM OLD.data_snapshot_json
        OR NEW.validation_json IS DISTINCT FROM OLD.validation_json
        OR NEW.workflow_version IS DISTINCT FROM OLD.workflow_version
        OR NEW.model_snapshot_json IS DISTINCT FROM OLD.model_snapshot_json
        OR NEW.data_quality_json IS DISTINCT FROM OLD.data_quality_json
        OR NEW.schema_version IS DISTINCT FROM OLD.schema_version
        OR NEW.content_checksum IS DISTINCT FROM OLD.content_checksum
        OR NEW.confidence_score IS DISTINCT FROM OLD.confidence_score
        OR NEW.confidence_algorithm_version IS DISTINCT FROM OLD.confidence_algorithm_version
        OR NEW.frozen_at IS DISTINCT FROM OLD.frozen_at
    ) THEN
        RAISE EXCEPTION 'published report version % is immutable', OLD.id
            USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END
$freeze$;

DROP TRIGGER IF EXISTS trg_report_version_immutable ON report_version;
CREATE TRIGGER trg_report_version_immutable
BEFORE UPDATE ON report_version
FOR EACH ROW
EXECUTE FUNCTION prevent_published_report_version_mutation();
