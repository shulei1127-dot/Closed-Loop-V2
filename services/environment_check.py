from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError

from core.config import get_settings
from core.db import SessionLocal, probe_database_connection, safe_database_url
from repositories.module_config_repo import ModuleConfigRepository
from repositories.source_snapshot_repo import SourceSnapshotRepository
from services.module_registry import default_module_configs
from services.pts_browser_profile_session import (
    pts_browser_profile_configured,
    pts_browser_profile_enabled,
    pts_direct_http_enabled,
    resolve_pts_profile_dir,
)
from services.pts_session_service import PtsSessionService
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
        "visit_real_execution_enabled",
        "pts_base_url",
        "pts_cookie_header",
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
        module_health = self.build_module_health_items() if database_ok else []
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
            "task_dispatcher": {
                "worker_count": self.settings.task_dispatcher_worker_count,
                "persistent_state": True,
                "message": "批次状态已持久化到数据库，服务重启后仍可查看历史批次与失败明细。",
            },
            "module_health": module_health,
        }

    def build_module_health_items(self) -> list[dict]:
        pts_session = PtsSessionService().get_status()
        items: list[dict] = []
        with SessionLocal() as db:
            snapshot_repo = SourceSnapshotRepository(db)
            for module_code in MODULE_REAL_ENV_KEYS:
                latest_snapshot = snapshot_repo.latest_for_module(module_code)
                checks: list[dict] = []
                checks.append(self._build_snapshot_check(latest_snapshot))
                checks.append(self._build_sync_auth_check(latest_snapshot))
                checks.append(self._build_real_execution_check(module_code))
                if module_code in {"visit", "inspection", "proactive"}:
                    checks.append(self._build_pts_session_check(pts_session))
                if module_code == "inspection":
                    checks.append(self._build_inspection_report_root_check())

                summary = "；".join(check["status_label"] + ":" + check["label"] for check in checks[:2])
                items.append(
                    {
                        "module_code": module_code,
                        "module_name": self._module_name(module_code),
                        "status": self._overall_status(checks),
                        "status_label": self._overall_status_label(checks),
                        "summary": summary or "待检查",
                        "last_snapshot_time": getattr(latest_snapshot, "sync_time", None),
                        "last_sync_status": getattr(latest_snapshot, "sync_status", None),
                        "checks": checks,
                    }
                )
        return items

    def build_module_health_item(self, module_code: str) -> dict | None:
        for item in self.build_module_health_items():
            if item["module_code"] == module_code:
                return item
        return None

    def _module_report(self, module_code: str) -> dict:
        if module_code in {"visit", "proactive"}:
            missing_fields = []
            if not self.settings.pts_base_url:
                missing_fields.append("pts_base_url")
            browser_profile_enabled = pts_browser_profile_enabled(self.settings)
            browser_profile_configured = pts_browser_profile_configured(self.settings)
            browser_session_available = browser_profile_configured or _find_local_chrome_user_data_dir() is not None
            direct_available = bool(self.settings.pts_cookie_header and pts_direct_http_enabled(self.settings))
            if not browser_profile_configured and not direct_available:
                missing_fields.append("pts_cookie_header")
            if module_code == "visit" and (self.settings.visit_real_base_url or self.settings.visit_real_token):
                if not self.settings.visit_real_base_url:
                    missing_fields.append("visit_real_base_url")
                if not self.settings.visit_real_token:
                    missing_fields.append("visit_real_token")
            return {
                "ok": not missing_fields,
                "missing_fields": missing_fields,
                "browser_session_available": browser_session_available,
                "browser_profile_enabled": browser_profile_enabled,
                "browser_profile_configured": browser_profile_configured,
                "browser_profile_dir": str(resolve_pts_profile_dir(self.settings)),
                "direct_http_enabled": pts_direct_http_enabled(self.settings),
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

    def _module_name(self, module_code: str) -> str:
        mapping = {
            "visit": "交付转售后回访",
            "inspection": "巡检工单闭环",
            "proactive": "超半年主动回访",
        }
        return mapping.get(module_code, module_code)

    def _build_snapshot_check(self, latest_snapshot) -> dict:
        if latest_snapshot is None:
            return {
                "code": "latest_sync",
                "label": "最近同步",
                "status": "warning",
                "status_label": "待同步",
                "detail": "还没有可用快照，请先执行一次同步。",
            }
        sync_status = str(getattr(latest_snapshot, "sync_status", "") or "")
        row_count = int(getattr(latest_snapshot, "row_count", 0) or 0)
        raw_meta = getattr(latest_snapshot, "raw_meta", {}) or {}
        auth_source = str(raw_meta.get("auth_source") or raw_meta.get("browser_auth", {}).get("auth_source") or "").strip()
        detail = f"{latest_snapshot.sync_time.isoformat()} / 状态={sync_status} / 记录数={row_count}"
        if auth_source:
            detail = f"{detail} / auth_source={auth_source}"
        if sync_status == "success":
            return {
                "code": "latest_sync",
                "label": "最近同步",
                "status": "ok",
                "status_label": "正常",
                "detail": detail,
            }
        if sync_status == "partial":
            return {
                "code": "latest_sync",
                "label": "最近同步",
                "status": "warning",
                "status_label": "部分成功",
                "detail": detail,
            }
        return {
            "code": "latest_sync",
            "label": "最近同步",
            "status": "failed",
            "status_label": "失败",
            "detail": detail,
        }

    def _build_sync_auth_check(self, latest_snapshot) -> dict:
        if latest_snapshot is None:
            return {
                "code": "sync_auth_source",
                "label": "同步认证源",
                "status": "warning",
                "status_label": "未知",
                "detail": "尚无快照，无法判断当前同步认证来源。",
            }
        raw_meta = getattr(latest_snapshot, "raw_meta", {}) or {}
        collector_health = raw_meta.get("collector_health") or {}
        auth_source = (
            raw_meta.get("auth_source")
            or collector_health.get("auth_source")
            or raw_meta.get("browser_auth", {}).get("auth_source")
            or "unknown"
        )
        sync_error = str(getattr(latest_snapshot, "sync_error", "") or "").strip()
        detail = f"auth_source={auth_source}"
        if sync_error:
            detail = f"{detail} / 最近错误={sync_error[:160]}"
        return {
            "code": "sync_auth_source",
            "label": "同步认证源",
            "status": "ok" if auth_source not in {"unknown", "disabled"} else "warning",
            "status_label": "可见" if auth_source not in {"unknown", "disabled"} else "未知",
            "detail": detail,
        }

    def _build_real_execution_check(self, module_code: str) -> dict:
        enabled = bool(self.settings.enable_real_execution)
        module_field = f"{module_code}_real_execution_enabled"
        module_enabled = bool(getattr(self.settings, module_field, False))
        if enabled and module_enabled:
            return {
                "code": "real_execution",
                "label": "实时执行",
                "status": "ok",
                "status_label": "已启用",
                "detail": "模块已允许真实执行。",
            }
        return {
            "code": "real_execution",
            "label": "实时执行",
            "status": "warning",
            "status_label": "未启用",
            "detail": "当前模块仍可能走模拟或只读路径。",
        }

    def _build_pts_session_check(self, pts_session: dict) -> dict:
        if pts_session.get("configured"):
            return {
                "code": "pts_session",
                "label": "PTS 会话",
                "status": "ok",
                "status_label": "可用",
                "detail": f"当前来源：{pts_session.get('source') or 'env_file'}",
            }
        return {
            "code": "pts_session",
            "label": "PTS 会话",
            "status": "failed",
            "status_label": "缺失",
            "detail": "当前没有可用的 PTS 会话，执行链会失败。",
        }

    def _build_inspection_report_root_check(self) -> dict:
        root = Path(self.settings.inspection_report_root)
        if root.exists() and root.is_dir():
            return {
                "code": "report_root",
                "label": "巡检报告目录",
                "status": "ok",
                "status_label": "可用",
                "detail": str(root),
            }
        return {
            "code": "report_root",
            "label": "巡检报告目录",
            "status": "failed",
            "status_label": "不可用",
            "detail": f"目录不存在或不可读：{root}",
        }

    @staticmethod
    def _overall_status(checks: list[dict]) -> str:
        statuses = {str(check.get("status") or "") for check in checks}
        if "failed" in statuses:
            return "failed"
        if "warning" in statuses:
            return "warning"
        return "ok"

    @classmethod
    def _overall_status_label(cls, checks: list[dict]) -> str:
        status = cls._overall_status(checks)
        return {
            "ok": "健康",
            "warning": "关注",
            "failed": "异常",
        }.get(status, "未知")
