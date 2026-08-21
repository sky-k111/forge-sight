-- 飞书企业自建应用机器人：外部单聊会话映射与持久消息收件箱。
-- 幂等脚本，可重复执行；业务数据库保持只读，不增加任何对象。

CREATE TABLE IF NOT EXISTS external_chat_conversation (
    id VARCHAR(40) PRIMARY KEY,
    channel VARCHAR(24) NOT NULL,
    tenant_key VARCHAR(80) NOT NULL DEFAULT '',
    external_chat_id VARCHAR(120) NOT NULL,
    external_user_id VARCHAR(120) NOT NULL,
    platform_user_id VARCHAR(64) NOT NULL,
    source_id INTEGER NOT NULL,
    analysis_session_id VARCHAR(40) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'active',
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_external_chat_conversation
        UNIQUE (channel, tenant_key, external_chat_id)
);

CREATE INDEX IF NOT EXISTS ix_external_chat_conversation_channel
    ON external_chat_conversation (channel);
CREATE INDEX IF NOT EXISTS ix_external_chat_conversation_tenant_key
    ON external_chat_conversation (tenant_key);
CREATE INDEX IF NOT EXISTS ix_external_chat_conversation_external_chat_id
    ON external_chat_conversation (external_chat_id);
CREATE INDEX IF NOT EXISTS ix_external_chat_conversation_external_user_id
    ON external_chat_conversation (external_user_id);
CREATE INDEX IF NOT EXISTS ix_external_chat_conversation_platform_user_id
    ON external_chat_conversation (platform_user_id);
CREATE INDEX IF NOT EXISTS ix_external_chat_conversation_source_id
    ON external_chat_conversation (source_id);
CREATE INDEX IF NOT EXISTS ix_external_chat_conversation_analysis_session_id
    ON external_chat_conversation (analysis_session_id);

CREATE TABLE IF NOT EXISTS external_chat_job (
    id VARCHAR(40) PRIMARY KEY,
    channel VARCHAR(24) NOT NULL,
    tenant_key VARCHAR(80) NOT NULL DEFAULT '',
    external_message_id VARCHAR(120) NOT NULL,
    external_chat_id VARCHAR(120) NOT NULL,
    external_user_id VARCHAR(120) NOT NULL,
    question TEXT NOT NULL,
    status VARCHAR(24) NOT NULL DEFAULT 'pending',
    conversation_id VARCHAR(40),
    analysis_session_id VARCHAR(40),
    run_id VARCHAR(40),
    ack_message_id VARCHAR(120),
    result_message_id VARCHAR(120),
    result_text TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    lease_owner VARCHAR(100),
    lease_expires_at TIMESTAMP WITHOUT TIME ZONE,
    error_summary VARCHAR(500),
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP WITHOUT TIME ZONE,
    CONSTRAINT uq_external_chat_message UNIQUE (channel, external_message_id),
    CONSTRAINT ck_external_chat_attempt_count CHECK (attempt_count >= 0)
);

CREATE INDEX IF NOT EXISTS ix_external_chat_job_channel
    ON external_chat_job (channel);
CREATE INDEX IF NOT EXISTS ix_external_chat_job_tenant_key
    ON external_chat_job (tenant_key);
CREATE INDEX IF NOT EXISTS ix_external_chat_job_external_chat_id
    ON external_chat_job (external_chat_id);
CREATE INDEX IF NOT EXISTS ix_external_chat_job_external_user_id
    ON external_chat_job (external_user_id);
CREATE INDEX IF NOT EXISTS ix_external_chat_job_status
    ON external_chat_job (status);
CREATE INDEX IF NOT EXISTS ix_external_chat_job_conversation_id
    ON external_chat_job (conversation_id);
CREATE INDEX IF NOT EXISTS ix_external_chat_job_analysis_session_id
    ON external_chat_job (analysis_session_id);
CREATE INDEX IF NOT EXISTS ix_external_chat_job_run_id
    ON external_chat_job (run_id);
CREATE INDEX IF NOT EXISTS ix_external_chat_job_claim
    ON external_chat_job (channel, status, lease_expires_at);
