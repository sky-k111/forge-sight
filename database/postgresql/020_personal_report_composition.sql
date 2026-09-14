-- Personal compositions do not replace or mutate shared report templates.
CREATE TABLE IF NOT EXISTS personal_report_template (
    id VARCHAR(40) PRIMARY KEY,
    tenant_id VARCHAR(40) NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
    user_id VARCHAR(64) NOT NULL,
    source_id INTEGER NOT NULL REFERENCES data_source(id) ON DELETE CASCADE,
    version INTEGER NOT NULL DEFAULT 1,
    config_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_personal_report_template_scope
ON personal_report_template (tenant_id, user_id, source_id);
