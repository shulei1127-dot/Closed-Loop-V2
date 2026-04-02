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
    pts_verify_ssl: bool = True
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
    inspection_real_execution_enabled: bool = False
    inspection_real_base_url: str = ""
    inspection_real_token: str = ""
    inspection_real_token_header: str = "X-Inspection-Token"
    inspection_real_assign_endpoint_template: str = "/inspection-work-orders/{work_order_id}/assign-owner"
    inspection_real_add_member_endpoint_template: str = "/inspection-work-orders/{work_order_id}/add-member"
    inspection_real_upload_endpoint_template: str = "/inspection-work-orders/{work_order_id}/upload-reports"
    inspection_real_complete_endpoint_template: str = "/inspection-work-orders/{work_order_id}/complete"
    inspection_real_final_link_path: str = "data.final_link"
    inspection_real_timeout_seconds: float = 15.0
    inspection_real_verify_ssl: bool = True
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
    inspection_report_root: str = "/Users/shulei/Downloads/巡检报告集合-已审核"
    sync_retry_max_attempts: int = 2
    execute_retry_max_attempts: int = 2

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
