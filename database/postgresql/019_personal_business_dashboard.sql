-- U 业务看板：按企业、账号和数据源隔离的个人布局。
-- 只保存受版本控制的组件定义，不保存 SQL、查询结果或凭据。

CREATE TABLE IF NOT EXISTS dashboard_preference (
    tenant_id VARCHAR(40) NOT NULL,
    user_id VARCHAR(64) NOT NULL,
    source_id INTEGER NOT NULL,
    config_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT pk_dashboard_preference PRIMARY KEY (tenant_id, user_id, source_id),
    CONSTRAINT fk_dashboard_preference_tenant
        FOREIGN KEY (tenant_id) REFERENCES tenant(id) ON DELETE CASCADE,
    CONSTRAINT fk_dashboard_preference_source
        FOREIGN KEY (source_id) REFERENCES data_source(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_dashboard_preference_tenant_id
    ON dashboard_preference (tenant_id);
CREATE INDEX IF NOT EXISTS ix_dashboard_preference_user_id
    ON dashboard_preference (user_id);
CREATE INDEX IF NOT EXISTS ix_dashboard_preference_source_id
    ON dashboard_preference (source_id);

ALTER TABLE dashboard_preference ALTER COLUMN config_json SET DEFAULT '{}'::jsonb;
ALTER TABLE dashboard_preference ALTER COLUMN version SET DEFAULT 1;
ALTER TABLE dashboard_preference ALTER COLUMN created_at SET DEFAULT CURRENT_TIMESTAMP;
ALTER TABLE dashboard_preference ALTER COLUMN updated_at SET DEFAULT CURRENT_TIMESTAMP;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'dashboard_preference'::regclass
          AND confrelid = 'tenant'::regclass
          AND contype = 'f'
    ) THEN
        ALTER TABLE dashboard_preference ADD CONSTRAINT fk_dashboard_preference_tenant
            FOREIGN KEY (tenant_id) REFERENCES tenant(id) ON DELETE CASCADE;
    END IF;
END $$;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'dashboard_preference'::regclass
          AND confrelid = 'data_source'::regclass
          AND contype = 'f'
    ) THEN
        ALTER TABLE dashboard_preference ADD CONSTRAINT fk_dashboard_preference_source
            FOREIGN KEY (source_id) REFERENCES data_source(id) ON DELETE CASCADE;
    END IF;
END $$;
