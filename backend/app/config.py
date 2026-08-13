"""ForgeSight 后端配置。所有环境相关取值只允许来自 .env，禁止硬编码。"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    business_db_url: str
    platform_db_url: str
    admin_db_url: str = ""

    llm_base_url: str = "https://api.deepseek.com"
    llm_api_key: str = ""
    llm_model: str = "deepseek-chat"
    llm_timeout_seconds: int = 60
    llm_proxy_url: str = ""

    agent_max_llm_calls: int = 12
    agent_max_retry_calls: int = 3
    agent_max_input_tokens: int = 120000
    agent_max_output_tokens: int = 16000
    agent_max_total_tokens: int = 136000
    agent_max_estimated_cost: float = 10.0
    agent_max_duration_seconds: int = 300
    agent_worker_enabled: bool = True
    agent_worker_poll_seconds: float = 0.75
    agent_worker_lease_seconds: int = 60

    sql_max_rows: int = 10000
    sql_timeout_seconds: int = 30
    sql_explain_enabled: bool = True
    sql_explain_max_estimated_rows: int = 500000
    sql_explain_max_cost: float = 100000.0

    feishu_webhook_url: str = ""
    feishu_webhook_secret: str = ""
    public_app_url: str = "http://127.0.0.1:3001"

    modeling_storage_dir: str = "modeling_artifacts"
    report_storage_dir: str = "report_artifacts"

    # —— 认证 ——
    auth_token_secret: str = "dev-insecure-secret-change-me"  # 生产必须从 .env 覆盖
    auth_token_ttl_seconds: int = 7 * 24 * 3600               # token 有效期 7 天
    auth_admin_username: str = "admin"                        # 种子管理员登录名
    auth_admin_password: str = "admin123"                     # 仅演示默认值，生产必改
    auth_admin_display_name: str = "系统管理员"
    auth_email_gate_enabled: bool = True
    auth_email_gate_allow_capture: bool = True
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
