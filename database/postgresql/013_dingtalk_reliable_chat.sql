-- 钉钉可靠问析与通用外部 IM 收件箱扩展。
-- 平台库迁移，可重复执行；业务数据库仍保持只读。

CREATE TABLE IF NOT EXISTS dingtalk_binding (
    id VARCHAR(40) PRIMARY KEY,
    user_id VARCHAR(40) NOT NULL,
    corp_id VARCHAR(64) NOT NULL DEFAULT '',
    union_id VARCHAR(128),
    open_id VARCHAR(128),
    source_id INTEGER,
    mobile VARCHAR(32),
    display_name VARCHAR(100) NOT NULL DEFAULT '',
    avatar TEXT,
    bind_status VARCHAR(24) NOT NULL DEFAULT 'bound',
    last_auth_at TIMESTAMP WITHOUT TIME ZONE,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_dingtalk_binding_user
        FOREIGN KEY (user_id) REFERENCES platform_user (id) ON DELETE CASCADE,
    CONSTRAINT fk_dingtalk_binding_source
        FOREIGN KEY (source_id) REFERENCES data_source (id) ON DELETE RESTRICT,
    CONSTRAINT ck_dingtalk_binding_identity
        CHECK (union_id IS NOT NULL OR open_id IS NOT NULL),
    CONSTRAINT ck_dingtalk_binding_status
        CHECK (bind_status IN ('bound', 'unbound', 'disabled'))
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_dingtalk_binding_user
    ON dingtalk_binding (user_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_dingtalk_binding_corp_union
    ON dingtalk_binding (corp_id, union_id) WHERE union_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_dingtalk_binding_corp_open
    ON dingtalk_binding (corp_id, open_id) WHERE open_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_dingtalk_binding_mobile
    ON dingtalk_binding (mobile);
CREATE INDEX IF NOT EXISTS ix_dingtalk_binding_status
    ON dingtalk_binding (bind_status);
CREATE INDEX IF NOT EXISTS ix_dingtalk_binding_source_id
    ON dingtalk_binding (source_id);

CREATE TABLE IF NOT EXISTS dingtalk_oauth_state (
    id VARCHAR(40) PRIMARY KEY,
    user_id VARCHAR(40),
    state_token VARCHAR(128) NOT NULL,
    corp_id VARCHAR(64),
    redirect_path VARCHAR(255) NOT NULL DEFAULT '',
    purpose VARCHAR(24) NOT NULL DEFAULT 'bind',
    expires_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    consumed_at TIMESTAMP WITHOUT TIME ZONE,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_dingtalk_oauth_state_user
        FOREIGN KEY (user_id) REFERENCES platform_user (id) ON DELETE CASCADE,
    CONSTRAINT uq_dingtalk_oauth_state_token UNIQUE (state_token),
    CONSTRAINT ck_dingtalk_oauth_state_purpose
        CHECK (purpose IN ('bind', 'login'))
);

CREATE INDEX IF NOT EXISTS ix_dingtalk_oauth_state_user
    ON dingtalk_oauth_state (user_id);
CREATE INDEX IF NOT EXISTS ix_dingtalk_oauth_state_expires
    ON dingtalk_oauth_state (expires_at);

-- 飞书已创建通用外部会话表；为钉钉的主动回复与退避重试补充字段。
ALTER TABLE external_chat_job
    ADD COLUMN IF NOT EXISTS reply_target_json JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE external_chat_job
    ADD COLUMN IF NOT EXISTS next_attempt_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP;
ALTER TABLE external_chat_conversation
    ALTER COLUMN external_user_id TYPE VARCHAR(160);
ALTER TABLE external_chat_job
    ALTER COLUMN external_user_id TYPE VARCHAR(160);

DROP INDEX IF EXISTS ix_external_chat_job_claim;
CREATE INDEX ix_external_chat_job_claim
    ON external_chat_job (channel, status, next_attempt_at, lease_expires_at);
