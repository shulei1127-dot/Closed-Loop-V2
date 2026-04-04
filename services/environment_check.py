from __future__ import annotations

from sqlalchemy.exc import SQLAlchemyError

from core.config import get_settings
from core.db import SessionLocal, probe_database_connection, safe_database_url
from repositories.module_config_repo import ModuleConfigRepository
from services.module_registry import default_module_configs
from services.recognizers.visit_delivery_backfill import _find_local_chrome_user_data_dir


MODULE_REAL_ENV_KEYS = {
    "visit": (
        "visit_real_execution_enabled",
        "visit_real_base_url",
        "visit_real_token",
        "pts_base_url",
        "pts_cookie_header",
    ),
    "inspection": (
        "inspection_real_execution_enabled",
        "inspection_real_base_url",
        "inspection_real_token",
        "inspection_report_root",
    ),
    "proactive": (
        "proactive_real_execution_enabled",
        "proactive_real_base_url",
        "proactive_real_token",
    ),
}


class EnvironmentCheckService:
    def __init__(self) -> None:
        self.settings = get_settings()

    def build_report(self) -> dict:
        database_ok, database_error = probe_database_connection()
        scheduler_report = self._scheduler_module_config_report() if database_ok else {
            "ok": False,
            "message": "数据库不可达，无法检查 scheduler/module config。",
            "error_type": "database_unavailable",
        }
        return {
            "ok": database_ok and scheduler_report["ok"],
            "app_env": self.settings.app_env,
            "app_debug": self.settings.app_debug,
            "database": {
                "ok": database_ok,
                "database_url": safe_database_url(),
                "message": "数据库连接正常" if database_ok else "数据库不可达",
                "error_type": None if database_ok else "database_unavailable",
                "details": None if database_ok else database_error,
            },
            "real_execution": {
                "enabled": self.settings.enable_real_execution,
                "modules": {
                    module_code: self._module_report(module_code)
                    for module_code in MODULE_REAL_ENV_KEYS
                },
            },
            "scheduler": {
                "enabled": self.settings.scheduler_enabled,
                **scheduler_report,
            },
        }

    def _module_report(self, module_code: str) -> dict:
        if module_code == "visit":
            missing_fields = []
            if not self.settings.pts_base_url:
                missing_fields.append("pts_base_url")
            browser_session_available = _find_local_chrome_user_data_dir() is not None
            if not browser_session_available and not self.settings.pts_cookie_header:
                missing_fields.append("pts_cookie_header")
            if self.settings.visit_real_base_url or self.settings.visit_real_token:
                if not self.settings.visit_real_base_url:
                    missing_fields.append("visit_real_base_url")
                if not self.settings.visit_real_token:
                    missing_fields.append("visit_real_token")
            return {
                "ok": not missing_fields,
                "missing_fields": missing_fields,
                "browser_session_available": browser_session_available,
            }
        missing_fields = [
            field_name
            for field_name in MODULE_REAL_ENV_KEYS[module_code]
            if not getattr(self.settings, field_name)
        ]
        return {
            "ok": not missing_fields,
            "missing_fields": missing_fields,
        }

    def _scheduler_module_config_report(self) -> dict:
        try:
            with SessionLocal() as db:
                repo = ModuleConfigRepository(db)
                repo.upsert_defaults(default_module_configs())
                db.rollback()
            return {
                "ok": True,
                "message": "scheduler 与 module config 可加载",
                "error_type": None,
            }
        except SQLAlchemyError as exc:
            return {
                "ok": False,
                "message": "scheduler 读取 module config 失败",
                "error_type": "module_config_unavailable",
                "details": str(exc),
            }
