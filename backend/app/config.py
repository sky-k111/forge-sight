"""ForgeSight 后端配置。所有环境相关取值只允许来自 .env，禁止硬编码。"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    business_db_url: str
    platform_db_url: str
    admin_db_url: str = ""
    # Exact MCP origins approved by the operator, including private enterprise hosts.
    mcp_model_allowed_origins: list[str] = []
    mcp_model_credentials: dict[str, str] = {}

    llm_base_url: str = "https://api.deepseek.com"
    llm_api_key: str = ""
    llm_model: str = "deepseek-chat"
    llm_protocol: str = "openai_compatible"
    llm_auth_mode: str = "auto"
    llm_timeout_seconds: int = 60
    llm_proxy_url: str = ""
    # 瞬时 LLM 失败（空内容/网络/限流）在步骤内做受限重试，降低偶发失败对端到端结果的影响。
    llm_retry_max_attempts: int = 3
    llm_retry_delay_seconds: float = 0.8

    # 语义检索使用独立的 OpenAI-compatible Embeddings 服务。默认关闭，避免在
    # 未建索引或模型服务不可用时影响现有规则检索主链路。
    retrieval_semantic_enabled: bool = False
    embedding_base_url: str = ""
    embedding_api_key: str = ""
    embedding_model: str = "text-embedding-v4"
    embedding_dimensions: int = 1024
    embedding_send_dimensions: bool = True
    embedding_proxy_url: str = ""
    embedding_timeout_seconds: int = 8
    embedding_max_retries: int = 1
    retrieval_semantic_circuit_breaker_seconds: int = 30
    retrieval_semantic_top_k: int = 20
    retrieval_semantic_score_threshold: float = 0.45
    retrieval_rrf_k: int = 60
    semantic_index_worker_enabled: bool = True
    semantic_index_worker_poll_seconds: float = 0.75
    semantic_index_worker_lease_seconds: int = 180
    semantic_index_max_attempts: int = 3

    agent_max_llm_calls: int = 12
    agent_max_retry_calls: int = 3
    agent_max_input_tokens: int = 120000
    agent_max_output_tokens: int = 16000
    agent_max_total_tokens: int = 136000
    agent_max_estimated_cost: float = 10.0
    agent_max_duration_seconds: int = 300
    agent_planner_enabled: bool = True
    agent_planner_model_enabled: bool = True
    # AgentIntent 是正式语义主路径：route/planner/编译/校验共享同一结构化契约。
    # 模型不可用或契约校验失败时才退回确定性规则，安全边界仍由代码执行。
    agent_intent_enabled: bool = True
    # 图表/维度语义判定升级到 LLM（规则拿不准时）。默认开启，因为枚举式规则遇到未覆盖列名
    # （stat_year/stat_week/order_no/line_no 等）会选错横纵轴；开启后由 LLM 在有歧义时纠正。
    visual_contract_llm_enabled: bool = True
    # 按用户稳定分桶，便于同一账号在 Planner/基线间做可复现实验。
    agent_planner_rollout_percent: int = 100
    agent_planner_max_capability_calls: int = 8
    agent_planner_max_database_executions: int = 3
    agent_planner_max_replans: int = 1
    # planner 在确定性规则判“目标完成”后，若结果仍有限制/建议，是否再用一次 LLM 决定是否加一步追问。
    agent_planner_llm_evaluate_enabled: bool = True
    agent_planner_max_async_jobs: int = 1
    agent_worker_enabled: bool = True
    agent_worker_poll_seconds: float = 0.75
    agent_worker_lease_seconds: int = 60
    report_scheduler_enabled: bool = True
    mail_worker_enabled: bool = True

    sql_max_rows: int = 10000
    sql_timeout_seconds: int = 30
    sql_explain_enabled: bool = True
    sql_explain_max_estimated_rows: int = 500000
    sql_explain_max_cost: float = 100000.0

    feishu_webhook_url: str = ""
    feishu_webhook_secret: str = ""
    # 飞书企业自建应用网页 OAuth（动态绑定）；回调地址须在飞书开放平台登记。
    feishu_app_id: str = ""
    feishu_app_secret: str = ""
    feishu_redirect_uri: str = ""
    feishu_authorize_url: str = "https://accounts.feishu.cn/open-apis/authen/v1/authorize"
    feishu_oauth_state_ttl_seconds: int = 600
    # 飞书企业自建应用机器人（单聊双向问析），与上面的群 Webhook 推送是两套能力。
    feishu_bot_enabled: bool = False
    feishu_bot_app_id: str = ""
    feishu_bot_app_secret: str = ""
    # open_id -> {"userId": "业务账号", "sourceId": 1}。未绑定用户默认拒绝访问。
    feishu_bot_bindings_json: str = "{}"
    feishu_bot_poll_seconds: float = 0.75
    feishu_bot_lease_seconds: int = 360
    feishu_bot_max_attempts: int = 3
    feishu_bot_max_concurrency: int = 4
    feishu_bot_result_wait_seconds: int = 30

    # 钉钉企业内部应用：OAuth 负责账号绑定，Stream 负责双向问析。
    dingtalk_client_id: str = ""
    dingtalk_client_secret: str = ""
    dingtalk_agent_id: str = ""
    dingtalk_redirect_uri: str = ""
    dingtalk_authorize_url_template: str = ""
    dingtalk_oauth_state_ttl_seconds: int = 600
    dingtalk_robot_code: str = ""
    dingtalk_bot_poll_seconds: float = 0.75
    dingtalk_bot_lease_seconds: int = 360
    dingtalk_bot_max_attempts: int = 3
    dingtalk_bot_max_concurrency: int = 4
    dingtalk_bot_result_wait_seconds: int = 30
    public_app_url: str = "http://127.0.0.1:5173"
    im_pdf_worker_enabled: bool = True
    im_pdf_max_attempts: int = 3
    im_pdf_worker_lease_seconds: int = 90
    monitoring_worker_enabled: bool = True
    monitoring_worker_lease_seconds: int = 120
    monitoring_delivery_lease_seconds: int = 90

    modeling_storage_dir: str = "modeling_artifacts"
    report_storage_dir: str = "report_artifacts"
    # 四眼原则开关：开启后管理员发布模型也必须先走复核（默认关闭＝允许免审直发）
    require_review_for_admin_publish: bool = False
    modeling_personal_ttl_days: int = 30
    modeling_personal_quota: int = 3
    modeling_artifact_grace_days: int = 90

    # —— 认证 ——
    auth_token_secret: str = "dev-insecure-secret-change-me"  # 生产必须从 .env 覆盖
    auth_token_ttl_seconds: int = 7 * 24 * 3600               # token 有效期 7 天
    auth_admin_username: str = "admin"                        # 种子管理员登录名
    auth_admin_password: str = "admin123"                     # 仅演示默认值，生产必改
    auth_admin_display_name: str = "系统管理员"
    auth_email_gate_enabled: bool = True
    auth_external_identity_gate_enabled: bool = True  # 新注册业务账号须绑定钉钉或飞书
    auth_email_gate_allow_capture: bool = False  # 默认禁止捕获验证码；本地演示需在 .env 显式开启
    demo_mode_enabled: bool = False

    # —— 邮箱验证 ——
    email_code_secret: str = ""                                # 生产必须配置
    email_code_ttl_seconds: int = 600
    email_code_resend_seconds: int = 60
    email_code_max_attempts: int = 5
    email_hourly_limit_email: int = 5
    email_hourly_limit_account: int = 5
    email_hourly_limit_ip: int = 10

    # —— 邮件 Provider 边界（实际 SMTP 由 E 任务实现） ——
    mail_provider: str = "capture"
    mail_from_address: str = ""
    mail_from_name: str = "ForgeSight"
    mail_timeout_seconds: int = 10
    mail_attachment_max_bytes: int = 10 * 1024 * 1024
    mail_max_attempts: int = 3
    mail_worker_lease_seconds: int = 60
    operations_queue_backlog_warning: int = 100
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = True

    cors_origins: str = (
        "http://localhost:3000,http://127.0.0.1:3000,"
        "http://localhost:3001,http://127.0.0.1:3001,"
        "http://localhost:5173,http://127.0.0.1:5173,"
        "http://localhost:5174,http://127.0.0.1:5174"
    )

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()
