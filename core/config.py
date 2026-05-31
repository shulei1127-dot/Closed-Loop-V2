from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "closed_loop_v2"
    app_env: str = "development"
    app_debug: bool = False
    api_prefix: str = "/api"
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/closed_loop_v2"
    scheduler_timezone: str = "Asia/Shanghai"
    scheduler_enabled: bool = True
    log_level: str = "INFO"
    dingtalk_default_headers_json: str = "{}"
    dingtalk_default_cookies_json: str = "{}"
    dingtalk_auth_token: str = ""
    dingtalk_request_timeout_seconds: float = 15.0
    dingtalk_verify_ssl: bool = True
    pts_base_url: str = "https://pts.chaitin.net"
    pts_cookie_header: str = ""
    pts_api_token: str = ""  # PTS API 令牌（Bearer 认证），全局默认，各模块可单独覆盖
    pts_visit_api_token: str = ""  # 交付转售后回访 + 超半年主动回访工单闭环，空则回退到 pts_api_token
    pts_proactive_tag_mark_api_token: str = ""  # 超半年主动回访项目打标签，空则回退到 pts_api_token
    pts_review_api_token: str = ""  # 交付转售后审核，空则回退到 pts_api_token
    pts_api_base_url: str = "http://api.in.chaitin.net"  # PTS 内网 API 地址，Bearer token 认证时使用
    pts_verify_ssl: bool = True
    pts_execution_transport: str = "auto"
    pts_direct_http_enabled: bool = True
    pts_browser_profile_enabled: bool = True
    pts_browser_profile_dir: str = ".pts-browser-profile/chrome-profile"
    pts_browser_headless: bool = True
    pts_browser_channel: str = ""
    pts_browser_context_reuse_enabled: bool = True
    enable_real_execution: bool = False
    visit_real_execution_enabled: bool = False
    visit_real_base_url: str = ""
    visit_real_token: str = ""
    visit_real_token_header: str = "X-Visit-Token"
    visit_real_create_endpoint: str = "/visit-work-orders"
    visit_real_assign_endpoint_template: str = "/visit-work-orders/{delivery_id}/assign-owner"
    visit_real_mark_target_endpoint_template: str = "/visit-work-orders/{delivery_id}/mark-target"
    visit_real_fill_feedback_endpoint_template: str = "/visit-work-orders/{delivery_id}/fill-feedback"
    visit_real_complete_endpoint_template: str = "/visit-work-orders/{delivery_id}/complete"
    visit_real_final_link_path: str = "data.final_link"
    visit_real_timeout_seconds: float = 15.0
    visit_real_verify_ssl: bool = True
    visit_prefer_direct_mode: bool = True
    visit_writeback_enabled: bool = False
    visit_writeback_dws_cli_path: str = ""  # DWS CLI 路径，为空则自动检测
    dws_cli_path: str = ""  # DWS CLI 路径（数据采集），为空则自动检测
    dws_cli_timeout_seconds: float = 60.0  # DWS CLI 子进程超时
    dws_cli_page_size: int = 100  # 每页查询记录数
    visit_writeback_aitable_base_id: str = "o14dA3GK8g5LavPaT7dDQqoxV9ekBD76"
    task_dispatcher_worker_count: int = 1
    scheduler_summary_dingtalk_webhook: str = ""
    scheduler_summary_dingtalk_secret: str = ""
    scheduler_summary_dingtalk_timeout_seconds: float = 10.0
    proactive_real_execution_enabled: bool = False
    proactive_real_base_url: str = ""
    proactive_real_token: str = ""
    proactive_real_token_header: str = "X-Proactive-Token"
    proactive_real_create_endpoint: str = "/proactive-work-orders"
    proactive_real_assign_endpoint_template: str = "/proactive-work-orders/{work_order_id}/assign-owner"
    proactive_real_feedback_endpoint_template: str = "/proactive-work-orders/{work_order_id}/fill-feedback"
    proactive_real_final_link_path: str = "data.final_link"
    proactive_real_timeout_seconds: float = 15.0
    proactive_real_verify_ssl: bool = True
    sync_retry_max_attempts: int = 2
    execute_retry_max_attempts: int = 2
    task_plan_latest_by_sql_enabled: bool = True
    ops_read_cache_ttl_seconds: float = 3.0

    # Visit pipeline (sync + execute + writeback)
    visit_pipeline_enabled: bool = False
    visit_pipeline_cron: str = "0 17 * * *"

    # Proactive pipeline (sync + execute + writeback)
    proactive_pipeline_enabled: bool = False
    proactive_pipeline_cron: str = "0 18 * * *"
    proactive_writeback_enabled: bool = False

    # Review pipeline (sync + audit + writeback)
    review_real_execution_enabled: bool = False
    review_writeback_enabled: bool = False
    review_pipeline_enabled: bool = False
    review_pipeline_cron: str = "0 16 * * *"
    pts_review_after_sale_filter_ids: str = ""  # 售后负责人 PTS 用户 ID 过滤，逗号分隔；为空则不过滤

    # Proactive tag mark pipeline (sync + tag mark + writeback), every 15 days
    proactive_tag_mark_pipeline_enabled: bool = False
    proactive_tag_mark_pipeline_cron: str = "0 10 16,28 * *"

    # 19:00 回访工单自动闭环 (visit + proactive), daily 19:00
    combined_pipeline_enabled: bool = False
    combined_pipeline_cron: str = "0 19 * * *"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
