-- Generated from backend/app/db/models.py.
-- Do not edit by hand; regenerate with database/tools/generate_platform_schema.py.
SET client_encoding = 'UTF8';

CREATE TABLE IF NOT EXISTS analysis_artifact (
	id VARCHAR(40) NOT NULL,
	run_id VARCHAR(40) NOT NULL,
	task_id VARCHAR(40),
	step_id VARCHAR(40),
	type VARCHAR(32) NOT NULL,
	schema_version VARCHAR(16) NOT NULL,
	content_json JSONB,
	storage_uri TEXT,
	summary_json JSONB NOT NULL,
	checksum VARCHAR(64) NOT NULL,
	source_fingerprint VARCHAR(64),
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id)
);
CREATE INDEX IF NOT EXISTS ix_analysis_artifact_run_id ON analysis_artifact (run_id);
CREATE INDEX IF NOT EXISTS ix_analysis_artifact_type ON analysis_artifact (type);

CREATE TABLE IF NOT EXISTS analysis_branch (
	id VARCHAR(40) NOT NULL,
	parent_session_id VARCHAR(40) NOT NULL,
	child_session_id VARCHAR(40) NOT NULL,
	fork_checkpoint_id VARCHAR(40) NOT NULL,
	created_by VARCHAR(64) NOT NULL,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (child_session_id)
);
CREATE INDEX IF NOT EXISTS ix_analysis_branch_parent_session_id ON analysis_branch (parent_session_id);

CREATE TABLE IF NOT EXISTS analysis_checkpoint (
	id VARCHAR(40) NOT NULL,
	session_id VARCHAR(40) NOT NULL,
	run_id VARCHAR(40) NOT NULL,
	step_key VARCHAR(64) NOT NULL,
	event_seq INTEGER NOT NULL,
	parent_checkpoint_id VARCHAR(40),
	workflow_version VARCHAR(32) NOT NULL,
	state_schema_version INTEGER NOT NULL,
	langgraph_thread_id VARCHAR(80) NOT NULL,
	langgraph_checkpoint_ns VARCHAR(80) NOT NULL,
	langgraph_checkpoint_id VARCHAR(100) NOT NULL,
	state_summary_json JSONB NOT NULL,
	artifact_refs_json JSONB NOT NULL,
	rollbackable BOOLEAN NOT NULL,
	checksum VARCHAR(64) NOT NULL,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id)
);
CREATE INDEX IF NOT EXISTS ix_analysis_checkpoint_run_id ON analysis_checkpoint (run_id);
CREATE INDEX IF NOT EXISTS ix_analysis_checkpoint_session_id ON analysis_checkpoint (session_id);

CREATE TABLE IF NOT EXISTS analysis_event (
	id VARCHAR(40) NOT NULL,
	session_id VARCHAR(40) NOT NULL,
	run_id VARCHAR(40) NOT NULL,
	task_id VARCHAR(40),
	seq INTEGER NOT NULL,
	event_type VARCHAR(80) NOT NULL,
	step_key VARCHAR(64),
	attempt INTEGER,
	visibility VARCHAR(16) NOT NULL,
	replayable BOOLEAN NOT NULL,
	payload_json JSONB NOT NULL,
	occurred_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_analysis_event_seq UNIQUE (run_id, seq)
);
CREATE INDEX IF NOT EXISTS ix_analysis_event_event_type ON analysis_event (event_type);
CREATE INDEX IF NOT EXISTS ix_analysis_event_run_id ON analysis_event (run_id);
CREATE INDEX IF NOT EXISTS ix_analysis_event_session_id ON analysis_event (session_id);

CREATE TABLE IF NOT EXISTS analysis_interaction (
	id VARCHAR(40) NOT NULL,
	run_id VARCHAR(40) NOT NULL,
	task_id VARCHAR(40),
	kind VARCHAR(24) NOT NULL,
	status VARCHAR(24) NOT NULL,
	prompt_json JSONB NOT NULL,
	response_json JSONB,
	resume_node VARCHAR(64) NOT NULL,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	resolved_at TIMESTAMP WITHOUT TIME ZONE,
	PRIMARY KEY (id)
);
CREATE INDEX IF NOT EXISTS ix_analysis_interaction_run_id ON analysis_interaction (run_id);

CREATE TABLE IF NOT EXISTS analysis_message (
	id VARCHAR(40) NOT NULL,
	session_id VARCHAR(40) NOT NULL,
	run_id VARCHAR(40),
	role VARCHAR(20) NOT NULL,
	content TEXT NOT NULL,
	status VARCHAR(20) NOT NULL,
	sequence_no INTEGER NOT NULL,
	metadata_json JSONB NOT NULL,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_analysis_message_seq UNIQUE (session_id, sequence_no)
);
CREATE INDEX IF NOT EXISTS ix_analysis_message_run_id ON analysis_message (run_id);
CREATE INDEX IF NOT EXISTS ix_analysis_message_session_id ON analysis_message (session_id);

CREATE TABLE IF NOT EXISTS analysis_run (
	id VARCHAR(40) NOT NULL,
	session_id VARCHAR(40) NOT NULL,
	trigger_message_id VARCHAR(40) NOT NULL,
	idempotency_key VARCHAR(100),
	source_id INTEGER NOT NULL,
	status VARCHAR(24) NOT NULL,
	current_step_key VARCHAR(64),
	workflow_version VARCHAR(32) NOT NULL,
	context_version INTEGER NOT NULL,
	last_event_seq INTEGER NOT NULL,
	error_code VARCHAR(64),
	llm_call_count INTEGER NOT NULL,
	retry_call_count INTEGER NOT NULL,
	input_tokens BIGINT NOT NULL,
	output_tokens BIGINT NOT NULL,
	total_tokens BIGINT NOT NULL,
	retry_tokens BIGINT NOT NULL,
	estimated_cost NUMERIC(18, 8) NOT NULL,
	currency VARCHAR(8) NOT NULL,
	budget_snapshot_json JSONB NOT NULL,
	lease_owner VARCHAR(100),
	lease_expires_at TIMESTAMP WITHOUT TIME ZONE,
	started_at TIMESTAMP WITHOUT TIME ZONE,
	finished_at TIMESTAMP WITHOUT TIME ZONE,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_analysis_run_idempotency UNIQUE (session_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS ix_analysis_run_session_id ON analysis_run (session_id);
CREATE INDEX IF NOT EXISTS ix_analysis_run_source_id ON analysis_run (source_id);
CREATE INDEX IF NOT EXISTS ix_analysis_run_status ON analysis_run (status);

CREATE TABLE IF NOT EXISTS analysis_run_step (
	id VARCHAR(40) NOT NULL,
	run_id VARCHAR(40) NOT NULL,
	task_id VARCHAR(40),
	task_scope VARCHAR(40) NOT NULL,
	step_key VARCHAR(64) NOT NULL,
	step_order INTEGER NOT NULL,
	attempt INTEGER NOT NULL,
	status VARCHAR(24) NOT NULL,
	label VARCHAR(200) NOT NULL,
	detail TEXT NOT NULL,
	input_refs_json JSONB NOT NULL,
	output_refs_json JSONB NOT NULL,
	error_json JSONB,
	started_at TIMESTAMP WITHOUT TIME ZONE,
	finished_at TIMESTAMP WITHOUT TIME ZONE,
	PRIMARY KEY (id),
	CONSTRAINT uq_analysis_step_attempt UNIQUE (run_id, task_scope, step_key, attempt)
);
CREATE INDEX IF NOT EXISTS ix_analysis_run_step_run_id ON analysis_run_step (run_id);
CREATE INDEX IF NOT EXISTS ix_analysis_run_step_task_id ON analysis_run_step (task_id);

CREATE TABLE IF NOT EXISTS analysis_session (
	id VARCHAR(40) NOT NULL,
	title VARCHAR(200) NOT NULL,
	user_id VARCHAR(64) NOT NULL,
	source_id INTEGER NOT NULL,
	status VARCHAR(20) NOT NULL,
	parent_session_id VARCHAR(40),
	fork_checkpoint_id VARCHAR(40),
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id)
);
CREATE INDEX IF NOT EXISTS ix_analysis_session_source_id ON analysis_session (source_id);
CREATE INDEX IF NOT EXISTS ix_analysis_session_user_id ON analysis_session (user_id);

CREATE TABLE IF NOT EXISTS analysis_task (
	id VARCHAR(40) NOT NULL,
	run_id VARCHAR(40) NOT NULL,
	task_order INTEGER NOT NULL,
	title VARCHAR(200) NOT NULL,
	normalized_question TEXT NOT NULL,
	mode VARCHAR(24) NOT NULL,
	analysis_type VARCHAR(32) NOT NULL,
	status VARCHAR(24) NOT NULL,
	depends_on_json JSONB NOT NULL,
	intent_json JSONB NOT NULL,
	assumptions_json JSONB NOT NULL,
	started_at TIMESTAMP WITHOUT TIME ZONE,
	finished_at TIMESTAMP WITHOUT TIME ZONE,
	PRIMARY KEY (id),
	CONSTRAINT uq_analysis_task_order UNIQUE (run_id, task_order)
);
CREATE INDEX IF NOT EXISTS ix_analysis_task_run_id ON analysis_task (run_id);

CREATE TABLE IF NOT EXISTS announcement (
	id VARCHAR(40) NOT NULL,
	supersedes_announcement_id VARCHAR(40),
	revision INTEGER NOT NULL,
	title VARCHAR(200) NOT NULL,
	body TEXT NOT NULL,
	priority VARCHAR(16) NOT NULL,
	is_pinned BOOLEAN NOT NULL,
	audience_json JSONB NOT NULL,
	audience_scope_hash VARCHAR(64) NOT NULL,
	status VARCHAR(24) NOT NULL,
	created_by VARCHAR(40) NOT NULL,
	published_by VARCHAR(40),
	withdrawn_by VARCHAR(40),
	valid_from TIMESTAMP WITHOUT TIME ZONE,
	expires_at TIMESTAMP WITHOUT TIME ZONE,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	published_at TIMESTAMP WITHOUT TIME ZONE,
	withdrawn_at TIMESTAMP WITHOUT TIME ZONE,
	PRIMARY KEY (id),
	CONSTRAINT ck_announcement_status CHECK (status IN ('draft', 'published', 'withdrawn', 'expired')),
	CONSTRAINT uq_announcement_revision UNIQUE (id, revision)
);
CREATE INDEX IF NOT EXISTS ix_announcement_audience_scope_hash ON announcement (audience_scope_hash);
CREATE INDEX IF NOT EXISTS ix_announcement_expires_at ON announcement (expires_at);
CREATE INDEX IF NOT EXISTS ix_announcement_status ON announcement (status);

CREATE TABLE IF NOT EXISTS audit_log (
	id SERIAL NOT NULL,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	user_name VARCHAR(50) NOT NULL,
	role VARCHAR(20) NOT NULL,
	action VARCHAR(255) NOT NULL,
	resource VARCHAR(255) NOT NULL,
	result VARCHAR(20) NOT NULL,
	detail TEXT NOT NULL,
	PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS business_object (
	id VARCHAR(50) NOT NULL,
	name VARCHAR(80) NOT NULL,
	topic VARCHAR(50) NOT NULL,
	description VARCHAR(255) NOT NULL,
	relation_count INTEGER NOT NULL,
	enabled BOOLEAN NOT NULL,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (name)
);

CREATE TABLE IF NOT EXISTS business_rule (
	id VARCHAR(20) NOT NULL,
	name VARCHAR(180) NOT NULL,
	rule_type VARCHAR(30) NOT NULL,
	scope VARCHAR(50) NOT NULL,
	content TEXT NOT NULL,
	enabled BOOLEAN NOT NULL,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (name)
);

CREATE TABLE IF NOT EXISTS data_scope_policy (
	id VARCHAR(40) NOT NULL,
	subject_type VARCHAR(24) NOT NULL,
	subject_id VARCHAR(80) NOT NULL,
	business_key VARCHAR(80) NOT NULL,
	effect VARCHAR(16) NOT NULL,
	data_source_ids_json JSONB NOT NULL,
	dimensions_json JSONB NOT NULL,
	effective_from TIMESTAMP WITHOUT TIME ZONE,
	effective_to TIMESTAMP WITHOUT TIME ZONE,
	enabled BOOLEAN NOT NULL,
	created_by VARCHAR(40) NOT NULL,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_data_scope_subject_business UNIQUE (subject_type, subject_id, business_key)
);
CREATE INDEX IF NOT EXISTS ix_data_scope_policy_business_key ON data_scope_policy (business_key);
CREATE INDEX IF NOT EXISTS ix_data_scope_policy_subject_id ON data_scope_policy (subject_id);
CREATE INDEX IF NOT EXISTS ix_data_scope_policy_subject_type ON data_scope_policy (subject_type);

CREATE TABLE IF NOT EXISTS data_source (
	id SERIAL NOT NULL,
	name VARCHAR(100) NOT NULL,
	type VARCHAR(20) NOT NULL,
	host VARCHAR(100) NOT NULL,
	port INTEGER NOT NULL,
	db_name VARCHAR(64) NOT NULL,
	username VARCHAR(64) NOT NULL,
	password VARCHAR(128) NOT NULL,
	status VARCHAR(20) NOT NULL,
	is_default BOOLEAN NOT NULL,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS data_table (
	id SERIAL NOT NULL,
	source_id INTEGER NOT NULL,
	name VARCHAR(64) NOT NULL,
	cn_name VARCHAR(100) NOT NULL,
	topic VARCHAR(20) NOT NULL,
	row_count INTEGER NOT NULL,
	primary_key VARCHAR(64) NOT NULL,
	comment VARCHAR(255) NOT NULL,
	data_updated VARCHAR(20) NOT NULL,
	registered BOOLEAN NOT NULL,
	scanned_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_source_table UNIQUE (source_id, name)
);
CREATE INDEX IF NOT EXISTS ix_data_table_source_id ON data_table (source_id);

CREATE TABLE IF NOT EXISTS domain_event_outbox (
	id VARCHAR(40) NOT NULL,
	event_type VARCHAR(100) NOT NULL,
	aggregate_id VARCHAR(80) NOT NULL,
	idempotency_key VARCHAR(160) NOT NULL,
	payload_json JSONB NOT NULL,
	status VARCHAR(24) NOT NULL,
	lease_owner VARCHAR(100),
	lease_expires_at TIMESTAMP WITHOUT TIME ZONE,
	attempt_count INTEGER NOT NULL,
	next_retry_at TIMESTAMP WITHOUT TIME ZONE,
	acknowledged_at TIMESTAMP WITHOUT TIME ZONE,
	error_summary VARCHAR(500),
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT ck_outbox_attempt_count CHECK (attempt_count >= 0),
	CONSTRAINT ck_outbox_status CHECK (status IN ('pending', 'processing', 'retry_wait', 'completed', 'dead_letter')),
	UNIQUE (idempotency_key)
);
CREATE INDEX IF NOT EXISTS ix_domain_event_outbox_aggregate_id ON domain_event_outbox (aggregate_id);
CREATE INDEX IF NOT EXISTS ix_domain_event_outbox_event_type ON domain_event_outbox (event_type);
CREATE INDEX IF NOT EXISTS ix_domain_event_outbox_status ON domain_event_outbox (status);
CREATE INDEX IF NOT EXISTS ix_outbox_claim ON domain_event_outbox (status, next_retry_at, lease_expires_at);

CREATE TABLE IF NOT EXISTS governed_model (
	id VARCHAR(40) NOT NULL,
	task_id VARCHAR(40) NOT NULL,
	name VARCHAR(120) NOT NULL,
	version INTEGER NOT NULL,
	algorithm VARCHAR(30) NOT NULL,
	status VARCHAR(16) NOT NULL,
	owner_id VARCHAR(64) NOT NULL,
	metrics_json JSONB NOT NULL,
	features_json JSONB NOT NULL,
	model_path TEXT NOT NULL,
	feature_names_json JSONB NOT NULL,
	feature_encoders_json JSONB NOT NULL,
	feature_importance_json JSONB NOT NULL,
	call_count INTEGER NOT NULL,
	reviewed_by VARCHAR(64),
	reviewed_at TIMESTAMP WITHOUT TIME ZONE,
	published_at TIMESTAMP WITHOUT TIME ZONE,
	retired_at TIMESTAMP WITHOUT TIME ZONE,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id)
);
CREATE INDEX IF NOT EXISTS ix_governed_model_status ON governed_model (status);
CREATE INDEX IF NOT EXISTS ix_governed_model_task_id ON governed_model (task_id);

CREATE TABLE IF NOT EXISTS knowledge_metric (
	id VARCHAR(20) NOT NULL,
	name VARCHAR(100) NOT NULL,
	topic VARCHAR(50) NOT NULL,
	definition TEXT NOT NULL,
	formula TEXT NOT NULL,
	synonyms_json TEXT NOT NULL,
	dimensions_json TEXT NOT NULL,
	time_field VARCHAR(64) NOT NULL,
	precision VARCHAR(50) NOT NULL,
	owner VARCHAR(50) NOT NULL,
	status VARCHAR(20) NOT NULL,
	version INTEGER NOT NULL,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	published_at TIMESTAMP WITHOUT TIME ZONE,
	PRIMARY KEY (id),
	UNIQUE (name)
);

CREATE TABLE IF NOT EXISTS knowledge_reference (
	id SERIAL NOT NULL,
	entity_type VARCHAR(30) NOT NULL,
	entity_id VARCHAR(50) NOT NULL,
	consumer_type VARCHAR(30) NOT NULL,
	consumer_id VARCHAR(64) NOT NULL,
	description VARCHAR(255) NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_knowledge_reference UNIQUE (entity_type, entity_id, consumer_type, consumer_id)
);
CREATE INDEX IF NOT EXISTS ix_knowledge_reference_entity_id ON knowledge_reference (entity_id);
CREATE INDEX IF NOT EXISTS ix_knowledge_reference_entity_type ON knowledge_reference (entity_type);

CREATE TABLE IF NOT EXISTS knowledge_topic (
	code VARCHAR(20) NOT NULL,
	name VARCHAR(50) NOT NULL,
	color VARCHAR(20) NOT NULL,
	description VARCHAR(255) NOT NULL,
	coverage INTEGER NOT NULL,
	enabled BOOLEAN NOT NULL,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (code),
	UNIQUE (name)
);

CREATE TABLE IF NOT EXISTS llm_call_attempt (
	id VARCHAR(40) NOT NULL,
	run_id VARCHAR(40) NOT NULL,
	task_id VARCHAR(40),
	step_id VARCHAR(40),
	step_scope VARCHAR(120) NOT NULL,
	attempt INTEGER NOT NULL,
	retry_of_call_id VARCHAR(40),
	provider VARCHAR(40) NOT NULL,
	model VARCHAR(100) NOT NULL,
	model_profile_id VARCHAR(64),
	provider_request_id VARCHAR(120),
	input_tokens BIGINT,
	cached_input_tokens BIGINT,
	output_tokens BIGINT,
	reasoning_tokens BIGINT,
	total_tokens BIGINT,
	context_window BIGINT,
	context_remaining BIGINT,
	latency_ms BIGINT,
	status VARCHAR(20) NOT NULL,
	error_code VARCHAR(64),
	estimated_cost NUMERIC(18, 8) NOT NULL,
	currency VARCHAR(8) NOT NULL,
	price_snapshot_json JSONB NOT NULL,
	usage_raw_json JSONB NOT NULL,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	finished_at TIMESTAMP WITHOUT TIME ZONE,
	PRIMARY KEY (id),
	CONSTRAINT uq_llm_call_step_attempt UNIQUE (step_scope, attempt),
	UNIQUE (provider_request_id)
);
CREATE INDEX IF NOT EXISTS ix_llm_call_attempt_run_id ON llm_call_attempt (run_id);

CREATE TABLE IF NOT EXISTS llm_model_price (
	id SERIAL NOT NULL,
	provider VARCHAR(40) NOT NULL,
	model VARCHAR(100) NOT NULL,
	currency VARCHAR(8) NOT NULL,
	input_price NUMERIC(18, 8) NOT NULL,
	cached_input_price NUMERIC(18, 8) NOT NULL,
	output_price NUMERIC(18, 8) NOT NULL,
	reasoning_price NUMERIC(18, 8) NOT NULL,
	unit_tokens INTEGER NOT NULL,
	version VARCHAR(32) NOT NULL,
	source VARCHAR(255) NOT NULL,
	effective_from TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	effective_to TIMESTAMP WITHOUT TIME ZONE,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_llm_price_version UNIQUE (provider, model, version)
);
CREATE INDEX IF NOT EXISTS ix_llm_model_price_model ON llm_model_price (model);
CREATE INDEX IF NOT EXISTS ix_llm_model_price_provider ON llm_model_price (provider);

CREATE TABLE IF NOT EXISTS message (
	id VARCHAR(40) NOT NULL,
	message_type VARCHAR(32) NOT NULL,
	source VARCHAR(32) NOT NULL,
	title VARCHAR(200) NOT NULL,
	summary TEXT NOT NULL,
	related_type VARCHAR(40),
	related_id VARCHAR(40),
	report_version_id VARCHAR(40),
	priority VARCHAR(16) NOT NULL,
	is_pinned BOOLEAN NOT NULL,
	status VARCHAR(24) NOT NULL,
	idempotency_key VARCHAR(160) NOT NULL,
	valid_from TIMESTAMP WITHOUT TIME ZONE,
	expires_at TIMESTAMP WITHOUT TIME ZONE,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	published_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT ck_message_status CHECK (status IN ('published', 'withdrawn', 'expired')),
	UNIQUE (idempotency_key)
);
CREATE INDEX IF NOT EXISTS ix_message_expires_at ON message (expires_at);
CREATE INDEX IF NOT EXISTS ix_message_message_type ON message (message_type);
CREATE INDEX IF NOT EXISTS ix_message_report_version_id ON message (report_version_id);
CREATE INDEX IF NOT EXISTS ix_message_source ON message (source);
CREATE INDEX IF NOT EXISTS ix_message_status ON message (status);

CREATE TABLE IF NOT EXISTS metric_field_binding (
	id SERIAL NOT NULL,
	binding_id INTEGER NOT NULL,
	semantic_field VARCHAR(80) NOT NULL,
	table_name VARCHAR(64) NOT NULL,
	field_name VARCHAR(64) NOT NULL,
	transform_expression TEXT NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_metric_binding_semantic_field UNIQUE (binding_id, semantic_field)
);
CREATE INDEX IF NOT EXISTS ix_metric_field_binding_binding_id ON metric_field_binding (binding_id);

CREATE TABLE IF NOT EXISTS metric_source_binding (
	id SERIAL NOT NULL,
	metric_id VARCHAR(20) NOT NULL,
	source_id INTEGER NOT NULL,
	physical_formula TEXT NOT NULL,
	time_field VARCHAR(64) NOT NULL,
	filter_expression TEXT NOT NULL,
	sql_dialect VARCHAR(20) NOT NULL,
	status VARCHAR(20) NOT NULL,
	version INTEGER NOT NULL,
	notes TEXT NOT NULL,
	validated_at TIMESTAMP WITHOUT TIME ZONE,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_metric_source UNIQUE (metric_id, source_id)
);
CREATE INDEX IF NOT EXISTS ix_metric_source_binding_metric_id ON metric_source_binding (metric_id);
CREATE INDEX IF NOT EXISTS ix_metric_source_binding_source_id ON metric_source_binding (source_id);

CREATE TABLE IF NOT EXISTS metric_table_binding (
	id SERIAL NOT NULL,
	binding_id INTEGER NOT NULL,
	table_name VARCHAR(64) NOT NULL,
	role VARCHAR(30) NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_metric_binding_table UNIQUE (binding_id, table_name)
);
CREATE INDEX IF NOT EXISTS ix_metric_table_binding_binding_id ON metric_table_binding (binding_id);

CREATE TABLE IF NOT EXISTS modeling_evaluation (
	id VARCHAR(40) NOT NULL,
	task_id VARCHAR(40) NOT NULL,
	result_json JSONB NOT NULL,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_modeling_eval_task UNIQUE (task_id)
);
CREATE INDEX IF NOT EXISTS ix_modeling_evaluation_task_id ON modeling_evaluation (task_id);

CREATE TABLE IF NOT EXISTS modeling_inference_log (
	id VARCHAR(40) NOT NULL,
	task_id VARCHAR(40) NOT NULL,
	model_id VARCHAR(40) NOT NULL,
	input_json JSONB NOT NULL,
	output_json JSONB NOT NULL,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id)
);
CREATE INDEX IF NOT EXISTS ix_modeling_inference_log_model_id ON modeling_inference_log (model_id);
CREATE INDEX IF NOT EXISTS ix_modeling_inference_log_task_id ON modeling_inference_log (task_id);

CREATE TABLE IF NOT EXISTS modeling_task (
	id VARCHAR(40) NOT NULL,
	title VARCHAR(120) NOT NULL,
	source VARCHAR(10) NOT NULL,
	source_id INTEGER NOT NULL,
	owner_id VARCHAR(64) NOT NULL,
	goal TEXT NOT NULL,
	task_type VARCHAR(20) NOT NULL,
	dataset_name VARCHAR(100) NOT NULL,
	dataset_sql TEXT NOT NULL,
	target_field VARCHAR(64) NOT NULL,
	target_label VARCHAR(100) NOT NULL,
	features_json JSONB NOT NULL,
	candidates_json JSONB NOT NULL,
	selected_model VARCHAR(30),
	status VARCHAR(16) NOT NULL,
	error_json JSONB,
	run_id VARCHAR(40),
	modeling_proposal_json JSONB NOT NULL,
	sample_count INTEGER NOT NULL,
	metric VARCHAR(100) NOT NULL,
	trained_at TIMESTAMP WITHOUT TIME ZONE,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id)
);
CREATE INDEX IF NOT EXISTS ix_modeling_task_owner_id ON modeling_task (owner_id);
CREATE INDEX IF NOT EXISTS ix_modeling_task_run_id ON modeling_task (run_id);
CREATE INDEX IF NOT EXISTS ix_modeling_task_source_id ON modeling_task (source_id);
CREATE INDEX IF NOT EXISTS ix_modeling_task_status ON modeling_task (status);
CREATE INDEX IF NOT EXISTS ix_modeling_task_task_type ON modeling_task (task_type);

CREATE TABLE IF NOT EXISTS organization_unit (
	id VARCHAR(40) NOT NULL,
	code VARCHAR(80) NOT NULL,
	name VARCHAR(160) NOT NULL,
	node_type VARCHAR(32) NOT NULL,
	parent_id VARCHAR(40),
	path VARCHAR(500) NOT NULL,
	enabled BOOLEAN NOT NULL,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (code),
	FOREIGN KEY(parent_id) REFERENCES organization_unit (id) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS ix_organization_unit_node_type ON organization_unit (node_type);
CREATE INDEX IF NOT EXISTS ix_organization_unit_parent_id ON organization_unit (parent_id);
CREATE INDEX IF NOT EXISTS ix_organization_unit_path ON organization_unit (path);

CREATE TABLE IF NOT EXISTS platform_user (
	id VARCHAR(40) NOT NULL,
	username VARCHAR(50) NOT NULL,
	password_hash VARCHAR(256) NOT NULL,
	display_name VARCHAR(50) NOT NULL,
	role VARCHAR(20) NOT NULL,
	title VARCHAR(100) NOT NULL,
	enabled BOOLEAN NOT NULL,
	email VARCHAR(254),
	email_canonical VARCHAR(254),
	email_verified_at TIMESTAMP WITHOUT TIME ZONE,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT ck_platform_user_email_pair CHECK ((email IS NULL AND email_canonical IS NULL) OR (email IS NOT NULL AND email_canonical IS NOT NULL)),
	CONSTRAINT ck_platform_user_verified_email CHECK (email_verified_at IS NULL OR email IS NOT NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_platform_user_username ON platform_user (username);
CREATE UNIQUE INDEX IF NOT EXISTS uq_platform_user_email ON platform_user (email_canonical) WHERE email_canonical IS NOT NULL;

CREATE TABLE IF NOT EXISTS report (
	id VARCHAR(40) NOT NULL,
	title VARCHAR(200) NOT NULL,
	template_id VARCHAR(40) NOT NULL,
	source_id INTEGER NOT NULL,
	report_type VARCHAR(24) NOT NULL,
	topic VARCHAR(40) NOT NULL,
	status VARCHAR(32) NOT NULL,
	owner_id VARCHAR(64) NOT NULL,
	reviewer_id VARCHAR(64),
	subscription_id VARCHAR(40),
	schedule_key VARCHAR(80),
	audience_scope_hash VARCHAR(64),
	publish_source VARCHAR(32) NOT NULL,
	period_start TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	period_end TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	scope_json JSONB NOT NULL,
	current_version_id VARCHAR(40),
	analysis_session_id VARCHAR(40),
	analysis_run_id VARCHAR(40),
	error_json JSONB,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_report_schedule_audience UNIQUE (subscription_id, schedule_key, audience_scope_hash)
);
CREATE INDEX IF NOT EXISTS ix_report_analysis_run_id ON report (analysis_run_id);
CREATE INDEX IF NOT EXISTS ix_report_audience_scope_hash ON report (audience_scope_hash);
CREATE INDEX IF NOT EXISTS ix_report_owner_id ON report (owner_id);
CREATE INDEX IF NOT EXISTS ix_report_period_template ON report (period_start, period_end, template_id);
CREATE INDEX IF NOT EXISTS ix_report_schedule_key ON report (schedule_key);
CREATE INDEX IF NOT EXISTS ix_report_source_id ON report (source_id);
CREATE INDEX IF NOT EXISTS ix_report_status ON report (status);
CREATE INDEX IF NOT EXISTS ix_report_subscription_id ON report (subscription_id);
CREATE INDEX IF NOT EXISTS ix_report_template_id ON report (template_id);

CREATE TABLE IF NOT EXISTS report_delivery (
	id VARCHAR(40) NOT NULL,
	message_id VARCHAR(40),
	report_id VARCHAR(40),
	version_id VARCHAR(40),
	subscription_id VARCHAR(40),
	channel VARCHAR(24) NOT NULL,
	status VARCHAR(24) NOT NULL,
	attempt INTEGER NOT NULL,
	attempt_count INTEGER NOT NULL,
	recipient_user_id VARCHAR(40),
	recipient_address_snapshot VARCHAR(254),
	idempotency_key VARCHAR(160),
	next_retry_at TIMESTAMP WITHOUT TIME ZONE,
	error_category VARCHAR(64),
	provider_message_id VARCHAR(160),
	attachment_file_id VARCHAR(40),
	attachment_omitted BOOLEAN NOT NULL,
	lease_owner VARCHAR(100),
	lease_expires_at TIMESTAMP WITHOUT TIME ZONE,
	last_duration_ms INTEGER,
	response_id VARCHAR(120),
	error_json JSONB,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	sent_at TIMESTAMP WITHOUT TIME ZONE,
	PRIMARY KEY (id),
	CONSTRAINT uq_report_delivery_message_user_channel UNIQUE (message_id, recipient_user_id, channel),
	CONSTRAINT uq_report_delivery_version_user_channel UNIQUE (version_id, recipient_user_id, channel),
	CONSTRAINT ck_report_delivery_attempt_count CHECK (attempt_count >= 0),
	UNIQUE (idempotency_key)
);
CREATE INDEX IF NOT EXISTS ix_report_delivery_next_retry_at ON report_delivery (next_retry_at);
CREATE INDEX IF NOT EXISTS ix_report_delivery_message_id ON report_delivery (message_id);
CREATE INDEX IF NOT EXISTS ix_report_delivery_lease_owner ON report_delivery (lease_owner);
CREATE INDEX IF NOT EXISTS ix_report_delivery_lease_expires_at ON report_delivery (lease_expires_at);
CREATE INDEX IF NOT EXISTS ix_report_delivery_recipient_user_id ON report_delivery (recipient_user_id);
CREATE INDEX IF NOT EXISTS ix_report_delivery_report_id ON report_delivery (report_id);
CREATE INDEX IF NOT EXISTS ix_report_delivery_version_id ON report_delivery (version_id);

CREATE TABLE IF NOT EXISTS report_file (
	id VARCHAR(40) NOT NULL,
	report_id VARCHAR(40) NOT NULL,
	version_id VARCHAR(40) NOT NULL,
	format VARCHAR(12) NOT NULL,
	status VARCHAR(24) NOT NULL,
	storage_uri TEXT,
	mime_type VARCHAR(120),
	file_size BIGINT,
	checksum VARCHAR(128),
	source_content_checksum VARCHAR(64),
	generation_attempt INTEGER NOT NULL,
	error_json JSONB,
	next_retry_at TIMESTAMP WITHOUT TIME ZONE,
	lease_owner VARCHAR(80),
	lease_expires_at TIMESTAMP WITHOUT TIME ZONE,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	generated_at TIMESTAMP WITHOUT TIME ZONE,
	PRIMARY KEY (id),
	CONSTRAINT uq_report_file_version_format UNIQUE (version_id, format),
	CONSTRAINT ck_report_file_format CHECK (format IN ('pdf', 'xlsx')),
	CONSTRAINT ck_report_file_status CHECK (status IN ('queued', 'generating', 'ready', 'failed', 'dead_letter'))
);
CREATE INDEX IF NOT EXISTS ix_report_file_report_id ON report_file (report_id);
CREATE INDEX IF NOT EXISTS ix_report_file_status ON report_file (status);
CREATE INDEX IF NOT EXISTS ix_report_file_version_id ON report_file (version_id);
CREATE INDEX IF NOT EXISTS ix_report_file_next_retry_at ON report_file (next_retry_at);
CREATE INDEX IF NOT EXISTS ix_report_file_lease_owner ON report_file (lease_owner);
CREATE INDEX IF NOT EXISTS ix_report_file_lease_expires_at ON report_file (lease_expires_at);

CREATE TABLE IF NOT EXISTS report_section (
	id VARCHAR(40) NOT NULL,
	report_id VARCHAR(40) NOT NULL,
	version_id VARCHAR(40) NOT NULL,
	code VARCHAR(48) NOT NULL,
	title VARCHAR(160) NOT NULL,
	section_order INTEGER NOT NULL,
	status VARCHAR(24) NOT NULL,
	summary TEXT NOT NULL,
	content_json JSONB NOT NULL,
	confidence INTEGER NOT NULL,
	analysis_task_id VARCHAR(40),
	artifact_ids_json JSONB NOT NULL,
	error_json JSONB,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_report_section_order UNIQUE (version_id, section_order)
);
CREATE INDEX IF NOT EXISTS ix_report_section_report_id ON report_section (report_id);
CREATE INDEX IF NOT EXISTS ix_report_section_version_id ON report_section (version_id);

CREATE TABLE IF NOT EXISTS report_subscription (
	id VARCHAR(40) NOT NULL,
	name VARCHAR(120) NOT NULL,
	template_id VARCHAR(40) NOT NULL,
	source_id INTEGER NOT NULL,
	owner_id VARCHAR(64) NOT NULL,
	cadence VARCHAR(20) NOT NULL,
	weekday INTEGER,
	day_of_month INTEGER,
	send_time VARCHAR(8) NOT NULL,
	timezone VARCHAR(50) NOT NULL,
	channel VARCHAR(24) NOT NULL,
	publish_policy VARCHAR(24) NOT NULL,
	enabled BOOLEAN NOT NULL,
	next_run_at TIMESTAMP WITHOUT TIME ZONE,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id)
);
CREATE INDEX IF NOT EXISTS ix_report_subscription_owner_id ON report_subscription (owner_id);
CREATE INDEX IF NOT EXISTS ix_report_subscription_source_id ON report_subscription (source_id);
CREATE INDEX IF NOT EXISTS ix_report_subscription_template_id ON report_subscription (template_id);

CREATE TABLE IF NOT EXISTS report_template (
	id VARCHAR(40) NOT NULL,
	name VARCHAR(100) NOT NULL,
	report_type VARCHAR(24) NOT NULL,
	topic VARCHAR(40) NOT NULL,
	audience VARCHAR(120) NOT NULL,
	description VARCHAR(255) NOT NULL,
	sections_json JSONB NOT NULL,
	min_coverage INTEGER NOT NULL,
	version INTEGER NOT NULL,
	enabled BOOLEAN NOT NULL,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (name)
);

CREATE TABLE IF NOT EXISTS report_version (
	id VARCHAR(40) NOT NULL,
	report_id VARCHAR(40) NOT NULL,
	version INTEGER NOT NULL,
	status VARCHAR(24) NOT NULL,
	supersedes_version_id VARCHAR(40),
	summary TEXT NOT NULL,
	content_json JSONB NOT NULL,
	data_snapshot_json JSONB NOT NULL,
	validation_json JSONB NOT NULL,
	schema_version VARCHAR(32) NOT NULL,
	content_checksum VARCHAR(64),
	confidence_score INTEGER,
	confidence_algorithm_version VARCHAR(40),
	workflow_version VARCHAR(64) NOT NULL,
	model_snapshot_json JSONB NOT NULL,
	data_quality_json JSONB NOT NULL,
	created_by VARCHAR(64) NOT NULL,
	approved_by VARCHAR(64),
	generated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	frozen_at TIMESTAMP WITHOUT TIME ZONE,
	published_at TIMESTAMP WITHOUT TIME ZONE,
	PRIMARY KEY (id),
	CONSTRAINT uq_report_version UNIQUE (report_id, version),
	FOREIGN KEY(supersedes_version_id) REFERENCES report_version (id) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS ix_report_version_report_id ON report_version (report_id);

CREATE TABLE IF NOT EXISTS semantic_index_job (
	id VARCHAR(36) NOT NULL,
	source_id INTEGER NOT NULL,
	mode VARCHAR(20) NOT NULL,
	reason VARCHAR(255) NOT NULL,
	status VARCHAR(20) NOT NULL,
	attempt INTEGER NOT NULL,
	max_attempts INTEGER NOT NULL,
	error TEXT NOT NULL,
	result_json JSONB NOT NULL,
	embedding_model VARCHAR(160) NOT NULL,
	embedding_dimensions INTEGER NOT NULL,
	requested_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	next_attempt_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	started_at TIMESTAMP WITHOUT TIME ZONE,
	finished_at TIMESTAMP WITHOUT TIME ZONE,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	lease_owner VARCHAR(160),
	lease_expires_at TIMESTAMP WITHOUT TIME ZONE,
	PRIMARY KEY (id)
);
CREATE INDEX IF NOT EXISTS ix_semantic_index_job_lease_expires_at ON semantic_index_job (lease_expires_at);
CREATE INDEX IF NOT EXISTS ix_semantic_index_job_next_attempt_at ON semantic_index_job (next_attempt_at);
CREATE INDEX IF NOT EXISTS ix_semantic_index_job_requested_at ON semantic_index_job (requested_at);
CREATE INDEX IF NOT EXISTS ix_semantic_index_job_source_id ON semantic_index_job (source_id);
CREATE INDEX IF NOT EXISTS ix_semantic_index_job_status ON semantic_index_job (status);

CREATE TABLE IF NOT EXISTS table_field (
	id SERIAL NOT NULL,
	source_id INTEGER NOT NULL,
	table_name VARCHAR(64) NOT NULL,
	name VARCHAR(64) NOT NULL,
	cn_name VARCHAR(100) NOT NULL,
	data_type VARCHAR(40) NOT NULL,
	ordinal INTEGER NOT NULL,
	is_primary BOOLEAN NOT NULL,
	ref_table VARCHAR(64) NOT NULL,
	comment VARCHAR(255) NOT NULL,
	sample_value VARCHAR(100) NOT NULL,
	PRIMARY KEY (id)
);
CREATE INDEX IF NOT EXISTS ix_table_field_source_id ON table_field (source_id);
CREATE INDEX IF NOT EXISTS ix_table_field_table_name ON table_field (table_name);

CREATE TABLE IF NOT EXISTS table_relation (
	id SERIAL NOT NULL,
	source_id INTEGER NOT NULL,
	from_table VARCHAR(64) NOT NULL,
	from_field VARCHAR(64) NOT NULL,
	to_table VARCHAR(64) NOT NULL,
	to_field VARCHAR(64) NOT NULL,
	source VARCHAR(20) NOT NULL,
	PRIMARY KEY (id)
);
CREATE INDEX IF NOT EXISTS ix_table_relation_source_id ON table_relation (source_id);

CREATE TABLE IF NOT EXISTS delivery_attempt (
	id VARCHAR(40) NOT NULL,
	delivery_id VARCHAR(40) NOT NULL,
	attempt_number INTEGER NOT NULL,
	status VARCHAR(24) NOT NULL,
	provider_message_id VARCHAR(160),
	error_category VARCHAR(64),
	error_json JSONB,
	duration_ms INTEGER,
	started_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	finished_at TIMESTAMP WITHOUT TIME ZONE,
	PRIMARY KEY (id),
	CONSTRAINT uq_delivery_attempt_number UNIQUE (delivery_id, attempt_number),
	FOREIGN KEY(delivery_id) REFERENCES report_delivery (id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_delivery_attempt_delivery_id ON delivery_attempt (delivery_id);

CREATE TABLE IF NOT EXISTS email_verification_challenge (
	id VARCHAR(40) NOT NULL,
	user_id VARCHAR(40) NOT NULL,
	purpose VARCHAR(24) NOT NULL,
	email VARCHAR(254) NOT NULL,
	email_canonical VARCHAR(254) NOT NULL,
	code_hash VARCHAR(256),
	expires_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	attempt_count INTEGER DEFAULT 0 NOT NULL,
	send_count INTEGER DEFAULT 1 NOT NULL,
	last_sent_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	consumed_at TIMESTAMP WITHOUT TIME ZONE,
	superseded_at TIMESTAMP WITHOUT TIME ZONE,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT ck_email_challenge_purpose CHECK (purpose IN ('bind', 'verify', 'change')),
	CONSTRAINT ck_email_challenge_attempt_count CHECK (attempt_count >= 0),
	CONSTRAINT ck_email_challenge_send_count CHECK (send_count >= 0),
	CONSTRAINT ck_email_challenge_active_hash CHECK (consumed_at IS NOT NULL OR superseded_at IS NOT NULL OR code_hash IS NOT NULL),
	FOREIGN KEY(user_id) REFERENCES platform_user (id) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS ix_email_verification_challenge_email_created ON email_verification_challenge (email_canonical, created_at);
CREATE INDEX IF NOT EXISTS ix_email_verification_challenge_user ON email_verification_challenge (user_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_email_verification_challenge_active ON email_verification_challenge (user_id) WHERE consumed_at IS NULL AND superseded_at IS NULL;

CREATE TABLE IF NOT EXISTS message_recipient (
	id VARCHAR(40) NOT NULL,
	message_id VARCHAR(40) NOT NULL,
	user_id VARCHAR(40) NOT NULL,
	message_status_snapshot VARCHAR(24) NOT NULL,
	is_read BOOLEAN NOT NULL,
	read_at TIMESTAMP WITHOUT TIME ZONE,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_message_recipient_user UNIQUE (message_id, user_id),
	FOREIGN KEY(message_id) REFERENCES message (id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_message_recipient_is_read ON message_recipient (is_read);
CREATE INDEX IF NOT EXISTS ix_message_recipient_message_id ON message_recipient (message_id);
CREATE INDEX IF NOT EXISTS ix_message_recipient_user_id ON message_recipient (user_id);

CREATE TABLE IF NOT EXISTS user_membership (
	id VARCHAR(40) NOT NULL,
	user_id VARCHAR(40) NOT NULL,
	organization_id VARCHAR(40) NOT NULL,
	position_code VARCHAR(80) NOT NULL,
	business_role VARCHAR(80) NOT NULL,
	effective_from TIMESTAMP WITHOUT TIME ZONE,
	effective_to TIMESTAMP WITHOUT TIME ZONE,
	enabled BOOLEAN NOT NULL,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_user_membership_position UNIQUE (user_id, organization_id, position_code),
	FOREIGN KEY(organization_id) REFERENCES organization_unit (id) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS ix_user_membership_business_role ON user_membership (business_role);
CREATE INDEX IF NOT EXISTS ix_user_membership_organization_id ON user_membership (organization_id);
CREATE INDEX IF NOT EXISTS ix_user_membership_position_code ON user_membership (position_code);
CREATE INDEX IF NOT EXISTS ix_user_membership_user_id ON user_membership (user_id);

CREATE TABLE IF NOT EXISTS email_verification_event (
	id VARCHAR(40) NOT NULL,
	user_id VARCHAR(40) NOT NULL,
	email_canonical VARCHAR(254) NOT NULL,
	purpose VARCHAR(24) NOT NULL,
	challenge_id VARCHAR(40),
	event_type VARCHAR(24) NOT NULL,
	outcome VARCHAR(32) DEFAULT '' NOT NULL,
	ip VARCHAR(45) DEFAULT '' NOT NULL,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT ck_email_event_purpose CHECK (purpose IN ('bind', 'verify', 'change')),
	FOREIGN KEY(user_id) REFERENCES platform_user (id) ON DELETE RESTRICT,
	FOREIGN KEY(challenge_id) REFERENCES email_verification_challenge (id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS ix_email_verification_event_challenge_time ON email_verification_event (challenge_id, created_at);
CREATE INDEX IF NOT EXISTS ix_email_verification_event_email_time ON email_verification_event (email_canonical, created_at);
CREATE INDEX IF NOT EXISTS ix_email_verification_event_ip_time ON email_verification_event (ip, created_at);
CREATE INDEX IF NOT EXISTS ix_email_verification_event_user_time ON email_verification_event (user_id, created_at);
