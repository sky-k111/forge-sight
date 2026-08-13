-- E: message-scoped, resumable email delivery and immutable attempt history.

ALTER TABLE IF EXISTS report_delivery
    ADD COLUMN IF NOT EXISTS message_id VARCHAR(40),
    ADD COLUMN IF NOT EXISTS attachment_file_id VARCHAR(40),
    ADD COLUMN IF NOT EXISTS attachment_omitted BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS lease_owner VARCHAR(100),
    ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMP WITHOUT TIME ZONE,
    ADD COLUMN IF NOT EXISTS last_duration_ms INTEGER,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITHOUT TIME ZONE;

ALTER TABLE IF EXISTS report_delivery
    ALTER COLUMN report_id DROP NOT NULL,
    ALTER COLUMN version_id DROP NOT NULL;

UPDATE report_delivery
SET updated_at = COALESCE(updated_at, sent_at, created_at)
WHERE updated_at IS NULL;

ALTER TABLE IF EXISTS report_delivery
    ALTER COLUMN updated_at SET NOT NULL;

CREATE INDEX IF NOT EXISTS ix_report_delivery_message_id
    ON report_delivery (message_id);
CREATE INDEX IF NOT EXISTS ix_report_delivery_lease_owner
    ON report_delivery (lease_owner);
CREATE INDEX IF NOT EXISTS ix_report_delivery_lease_expires_at
    ON report_delivery (lease_expires_at);
CREATE UNIQUE INDEX IF NOT EXISTS uq_report_delivery_message_user_channel
    ON report_delivery (message_id, recipient_user_id, channel)
    WHERE message_id IS NOT NULL AND recipient_user_id IS NOT NULL;

ALTER TABLE IF EXISTS delivery_attempt
    ADD COLUMN IF NOT EXISTS duration_ms INTEGER;
