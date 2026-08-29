-- W 多数据库适配：Schema 资产身份、扩展连接配置与连接池修订。
-- 幂等增量迁移；不删除历史数据，不回写或暴露任何凭据。

ALTER TABLE data_source ADD COLUMN IF NOT EXISTS schema_names_json JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE data_source ADD COLUMN IF NOT EXISTS config_json JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE data_source ADD COLUMN IF NOT EXISTS credential_ref VARCHAR(255);
ALTER TABLE data_source ADD COLUMN IF NOT EXISTS connection_revision INTEGER NOT NULL DEFAULT 1;

UPDATE data_source
SET schema_names_json = CASE
    WHEN type = 'postgresql' THEN '["public"]'::jsonb
    WHEN type IN ('mysql', 'mariadb') AND COALESCE(db_name, '') <> '' THEN jsonb_build_array(db_name)
    WHEN type = 'sqlserver' THEN '["dbo"]'::jsonb
    WHEN type = 'oracle' AND COALESCE(username, '') <> '' THEN jsonb_build_array(UPPER(username))
    ELSE '[]'::jsonb
END
WHERE schema_names_json = '[]'::jsonb;

ALTER TABLE data_table ADD COLUMN IF NOT EXISTS schema_name VARCHAR(128) NOT NULL DEFAULT '';
ALTER TABLE data_table ADD COLUMN IF NOT EXISTS object_type VARCHAR(20) NOT NULL DEFAULT 'table';
ALTER TABLE table_field ADD COLUMN IF NOT EXISTS schema_name VARCHAR(128) NOT NULL DEFAULT '';
ALTER TABLE table_field ADD COLUMN IF NOT EXISTS ref_schema VARCHAR(128) NOT NULL DEFAULT '';
ALTER TABLE table_relation ADD COLUMN IF NOT EXISTS from_schema VARCHAR(128) NOT NULL DEFAULT '';
ALTER TABLE table_relation ADD COLUMN IF NOT EXISTS to_schema VARCHAR(128) NOT NULL DEFAULT '';
ALTER TABLE table_relation ADD COLUMN IF NOT EXISTS constraint_name VARCHAR(160) NOT NULL DEFAULT '';
ALTER TABLE metric_table_binding ALTER COLUMN table_name TYPE VARCHAR(260);
ALTER TABLE metric_field_binding ALTER COLUMN table_name TYPE VARCHAR(260);

UPDATE data_table AS t
SET schema_name = CASE
    WHEN s.type = 'postgresql' THEN 'public'
    WHEN s.type IN ('mysql', 'mariadb') THEN COALESCE(s.db_name, '')
    WHEN s.type = 'sqlserver' THEN 'dbo'
    WHEN s.type = 'oracle' THEN UPPER(COALESCE(s.username, ''))
    ELSE ''
END
FROM data_source AS s
WHERE t.source_id = s.id AND t.schema_name = '';

UPDATE table_field AS f
SET schema_name = t.schema_name
FROM data_table AS t
WHERE f.source_id = t.source_id
  AND f.table_name = t.name
  AND f.schema_name = '';

UPDATE table_relation AS r
SET from_schema = ft.schema_name,
    to_schema = tt.schema_name
FROM data_table AS ft, data_table AS tt
WHERE r.source_id = ft.source_id
  AND r.from_table = ft.name
  AND r.source_id = tt.source_id
  AND r.to_table = tt.name
  AND (r.from_schema = '' OR r.to_schema = '');

ALTER TABLE data_table DROP CONSTRAINT IF EXISTS uq_source_table;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'uq_source_schema_table'
    ) THEN
        ALTER TABLE data_table
            ADD CONSTRAINT uq_source_schema_table UNIQUE (source_id, schema_name, name);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS ix_data_table_source_schema ON data_table(source_id, schema_name);
CREATE INDEX IF NOT EXISTS ix_table_field_source_schema_table ON table_field(source_id, schema_name, table_name);
CREATE INDEX IF NOT EXISTS ix_table_relation_source_from ON table_relation(source_id, from_schema, from_table);
CREATE INDEX IF NOT EXISTS ix_table_relation_source_to ON table_relation(source_id, to_schema, to_table);
