-- ForgeSight multi-tenant foundation.
-- Existing installations are backfilled into one compatibility tenant; new rows are tenant scoped.

CREATE TABLE IF NOT EXISTS tenant (
    id VARCHAR(40) PRIMARY KEY,
    slug VARCHAR(80) NOT NULL UNIQUE,
    name VARCHAR(160) NOT NULL,
    status VARCHAR(24) NOT NULL DEFAULT 'active',
    plan_code VARCHAR(40) NOT NULL DEFAULT 'enterprise',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT ck_tenant_status CHECK (status IN ('active', 'suspended', 'archived'))
);
CREATE INDEX IF NOT EXISTS ix_tenant_slug ON tenant (slug);
CREATE INDEX IF NOT EXISTS ix_tenant_status ON tenant (status);

INSERT INTO tenant (id, slug, name, status, plan_code)
VALUES ('tenant-default', 'forgesight-demo', 'ForgeSight 演示企业', 'active', 'enterprise')
ON CONFLICT (id) DO NOTHING;

ALTER TABLE data_source ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(40);
UPDATE data_source SET tenant_id = 'tenant-default' WHERE tenant_id IS NULL OR tenant_id = '';
ALTER TABLE data_source ALTER COLUMN tenant_id SET DEFAULT 'tenant-default';
ALTER TABLE data_source ALTER COLUMN tenant_id SET NOT NULL;
CREATE INDEX IF NOT EXISTS ix_data_source_tenant_id ON data_source (tenant_id);
DO $$ BEGIN
    ALTER TABLE data_source ADD CONSTRAINT fk_data_source_tenant
        FOREIGN KEY (tenant_id) REFERENCES tenant(id) ON DELETE RESTRICT;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS tenant_membership (
    id VARCHAR(40) PRIMARY KEY,
    tenant_id VARCHAR(40) NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
    user_id VARCHAR(40) NOT NULL REFERENCES platform_user(id) ON DELETE CASCADE,
    member_role VARCHAR(24) NOT NULL DEFAULT 'member',
    is_default BOOLEAN NOT NULL DEFAULT FALSE,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    joined_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_tenant_membership_user UNIQUE (tenant_id, user_id),
    CONSTRAINT ck_tenant_membership_role CHECK (member_role IN ('owner', 'admin', 'member', 'viewer'))
);
CREATE INDEX IF NOT EXISTS ix_tenant_membership_tenant_id ON tenant_membership (tenant_id);
CREATE INDEX IF NOT EXISTS ix_tenant_membership_user_id ON tenant_membership (user_id);
CREATE INDEX IF NOT EXISTS ix_tenant_membership_member_role ON tenant_membership (member_role);

INSERT INTO tenant_membership (id, tenant_id, user_id, member_role, is_default, enabled)
SELECT 'tm_' || substr(md5('tenant-default:' || id), 1, 24),
       'tenant-default', id,
       CASE WHEN role = 'admin' THEN 'owner' ELSE 'member' END,
       TRUE, TRUE
FROM platform_user
ON CONFLICT (tenant_id, user_id) DO NOTHING;

ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(40) NOT NULL DEFAULT 'tenant-default';
CREATE INDEX IF NOT EXISTS ix_audit_log_tenant_id ON audit_log (tenant_id);

ALTER TABLE analysis_session ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(40) NOT NULL DEFAULT 'tenant-default';
UPDATE analysis_session AS s
SET tenant_id = d.tenant_id
FROM data_source AS d
WHERE d.id = s.source_id;
CREATE INDEX IF NOT EXISTS ix_analysis_session_tenant_id ON analysis_session (tenant_id);

ALTER TABLE knowledge_topic ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(40) NOT NULL DEFAULT 'tenant-default';
ALTER TABLE business_object ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(40) NOT NULL DEFAULT 'tenant-default';
ALTER TABLE business_rule ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(40) NOT NULL DEFAULT 'tenant-default';
ALTER TABLE knowledge_metric ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(40) NOT NULL DEFAULT 'tenant-default';
CREATE INDEX IF NOT EXISTS ix_knowledge_topic_tenant_id ON knowledge_topic (tenant_id);
CREATE INDEX IF NOT EXISTS ix_business_object_tenant_id ON business_object (tenant_id);
CREATE INDEX IF NOT EXISTS ix_business_rule_tenant_id ON business_rule (tenant_id);
CREATE INDEX IF NOT EXISTS ix_knowledge_metric_tenant_id ON knowledge_metric (tenant_id);

-- Stable codes are only guaranteed unique inside one tenant, so the primary keys
-- become (tenant_id, code/id). Pre-migration rows were already globally unique,
-- so rebuilding the key cannot produce duplicates.
ALTER TABLE knowledge_topic DROP CONSTRAINT IF EXISTS knowledge_topic_pkey;
ALTER TABLE knowledge_topic ADD CONSTRAINT knowledge_topic_pkey PRIMARY KEY (tenant_id, code);
ALTER TABLE business_object DROP CONSTRAINT IF EXISTS business_object_pkey;
ALTER TABLE business_object ADD CONSTRAINT business_object_pkey PRIMARY KEY (tenant_id, id);
ALTER TABLE business_rule DROP CONSTRAINT IF EXISTS business_rule_pkey;
ALTER TABLE business_rule ADD CONSTRAINT business_rule_pkey PRIMARY KEY (tenant_id, id);
ALTER TABLE knowledge_metric DROP CONSTRAINT IF EXISTS knowledge_metric_pkey;
ALTER TABLE knowledge_metric ADD CONSTRAINT knowledge_metric_pkey PRIMARY KEY (tenant_id, id);

ALTER TABLE knowledge_topic DROP CONSTRAINT IF EXISTS knowledge_topic_name_key;
ALTER TABLE business_object DROP CONSTRAINT IF EXISTS business_object_name_key;
ALTER TABLE business_rule DROP CONSTRAINT IF EXISTS business_rule_name_key;
ALTER TABLE knowledge_metric DROP CONSTRAINT IF EXISTS knowledge_metric_name_key;
ALTER TABLE knowledge_topic DROP CONSTRAINT IF EXISTS uq_knowledge_topic_tenant_name;
ALTER TABLE business_object DROP CONSTRAINT IF EXISTS uq_business_object_tenant_name;
ALTER TABLE business_rule DROP CONSTRAINT IF EXISTS uq_business_rule_tenant_name;
ALTER TABLE knowledge_metric DROP CONSTRAINT IF EXISTS uq_knowledge_metric_tenant_name;
ALTER TABLE knowledge_topic ADD CONSTRAINT uq_knowledge_topic_tenant_name UNIQUE (tenant_id, name);
ALTER TABLE business_object ADD CONSTRAINT uq_business_object_tenant_name UNIQUE (tenant_id, name);
ALTER TABLE business_rule ADD CONSTRAINT uq_business_rule_tenant_name UNIQUE (tenant_id, name);
ALTER TABLE knowledge_metric ADD CONSTRAINT uq_knowledge_metric_tenant_name UNIQUE (tenant_id, name);

ALTER TABLE data_scope_policy ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(40) NOT NULL DEFAULT 'tenant-default';
CREATE INDEX IF NOT EXISTS ix_data_scope_policy_tenant_id ON data_scope_policy (tenant_id);
ALTER TABLE data_scope_policy DROP CONSTRAINT IF EXISTS uq_data_scope_subject_business;
ALTER TABLE data_scope_policy DROP CONSTRAINT IF EXISTS uq_data_scope_tenant_subject_business;
ALTER TABLE data_scope_policy ADD CONSTRAINT uq_data_scope_tenant_subject_business
    UNIQUE (tenant_id, subject_type, subject_id, business_key);

-- Memory existed before the tenant root used the legacy literal "default".
-- Normalize it so web, Feishu and DingTalk share the same enterprise boundary.
UPDATE agent_memory SET tenant_id = 'tenant-default' WHERE tenant_id = 'default';
ALTER TABLE agent_memory ALTER COLUMN tenant_id SET DEFAULT 'tenant-default';
UPDATE agent_memory_policy SET tenant_id = 'tenant-default' WHERE tenant_id = 'default';
ALTER TABLE agent_memory_policy ALTER COLUMN tenant_id SET DEFAULT 'tenant-default';

-- Knowledge reference index gains the same tenant boundary as its entities.
ALTER TABLE knowledge_reference ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(40) NOT NULL DEFAULT 'tenant-default';
CREATE INDEX IF NOT EXISTS ix_knowledge_reference_tenant_id ON knowledge_reference (tenant_id);
ALTER TABLE knowledge_reference DROP CONSTRAINT IF EXISTS uq_knowledge_reference;
ALTER TABLE knowledge_reference ADD CONSTRAINT uq_knowledge_reference
    UNIQUE (tenant_id, entity_type, entity_id, consumer_type, consumer_id);
