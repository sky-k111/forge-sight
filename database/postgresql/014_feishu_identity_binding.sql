-- 飞书账号 OAuth 绑定与新注册账号外部身份门禁。
-- 平台库增量迁移，可重复执行；OAuth token 不落库。

ALTER TABLE platform_user
    ADD COLUMN IF NOT EXISTS external_identity_required BOOLEAN NOT NULL DEFAULT FALSE;

CREATE TABLE IF NOT EXISTS feishu_binding (
    id VARCHAR(40) PRIMARY KEY,
    user_id VARCHAR(40) NOT NULL,
    tenant_key VARCHAR(80) NOT NULL DEFAULT '',
    union_id VARCHAR(128),
    open_id VARCHAR(128),
    employee_id VARCHAR(128),
    source_id INTEGER,
    mobile VARCHAR(32),
    email VARCHAR(254),
    display_name VARCHAR(100) NOT NULL DEFAULT '',
    avatar TEXT,
    bind_status VARCHAR(24) NOT NULL DEFAULT 'bound',
    last_auth_at TIMESTAMP WITHOUT TIME ZONE,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_feishu_binding_user
        FOREIGN KEY (user_id) REFERENCES platform_user (id) ON DELETE CASCADE,
    CONSTRAINT fk_feishu_binding_source
        FOREIGN KEY (source_id) REFERENCES data_source (id) ON DELETE RESTRICT,
    CONSTRAINT ck_feishu_binding_identity
        CHECK (union_id IS NOT NULL OR open_id IS NOT NULL),
    CONSTRAINT ck_feishu_binding_status
        CHECK (bind_status IN ('bound', 'unbound', 'disabled'))
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_feishu_binding_user
    ON feishu_binding (user_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_feishu_binding_tenant_union
    ON feishu_binding (tenant_key, union_id)
    WHERE union_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_feishu_binding_tenant_open
    ON feishu_binding (tenant_key, open_id)
    WHERE open_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_feishu_binding_source_id
    ON feishu_binding (source_id);
CREATE INDEX IF NOT EXISTS ix_feishu_binding_status
    ON feishu_binding (bind_status);

CREATE TABLE IF NOT EXISTS feishu_oauth_state (
    id VARCHAR(40) PRIMARY KEY,
    user_id VARCHAR(40),
    state_token VARCHAR(128) NOT NULL,
    redirect_path VARCHAR(255) NOT NULL DEFAULT '',
    purpose VARCHAR(24) NOT NULL DEFAULT 'bind',
    expires_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    consumed_at TIMESTAMP WITHOUT TIME ZONE,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_feishu_oauth_state_user
        FOREIGN KEY (user_id) REFERENCES platform_user (id) ON DELETE CASCADE,
    CONSTRAINT uq_feishu_oauth_state_token UNIQUE (state_token),
    CONSTRAINT ck_feishu_oauth_state_purpose
        CHECK (purpose IN ('bind', 'login'))
);
