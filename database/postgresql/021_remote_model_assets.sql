-- Remote model weights remain on the provider. Only configuration and call metadata.
CREATE TABLE IF NOT EXISTS remote_model_asset (
    id VARCHAR(40) PRIMARY KEY,
    tenant_id VARCHAR(40) NOT NULL,
    source_id INTEGER NOT NULL,
    owner_id VARCHAR(64) NOT NULL,
    name VARCHAR(120) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'draft',
    config_json JSONB NOT NULL DEFAULT '{}',
    validation_json JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_remote_model_asset_tenant_id ON remote_model_asset(tenant_id);
CREATE INDEX IF NOT EXISTS ix_remote_model_asset_source_id ON remote_model_asset(source_id);
CREATE TABLE IF NOT EXISTS remote_model_call (
    id VARCHAR(40) PRIMARY KEY,
    model_id VARCHAR(40) NOT NULL,
    user_id VARCHAR(64) NOT NULL,
    status VARCHAR(20) NOT NULL,
    record_count INTEGER NOT NULL,
    duration_ms INTEGER NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_remote_model_call_model_id ON remote_model_call(model_id);
