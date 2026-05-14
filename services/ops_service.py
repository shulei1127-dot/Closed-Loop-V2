from __future__ import annotations

from datetime import datetime, timezone
from threading import RLock
from time import monotonic
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from core.config import get_settings
from core.runtime_state import runtime_state
from models.normalized_record import NormalizedRecord
from models.task_plan import TaskPlan
from models.task_run import TaskRun
from repositories.module_config_repo import ModuleConfigRepository
from repositories.normalized_record_repo import NormalizedRecordRepository
from repositories.task_plan_repo import TaskPlanRepository
from repositories.task_run_repo import TaskRunRepository
from repositories.source_snapshot_repo import SourceSnapshotRepository
from schemas.ops import (
    OpsEventItem,
    OpsOverviewItem,
    PendingTaskItem,
    RecentInspectionClosureItem,
    RecentVisitLinkItem,
)
from services.report_matching.matcher import InspectionReportMatcher
from services.report_matching.scanner import InspectionReportScanner
from services.ops_copy import build_run_view, status_label
from services.sync_service import SyncService

_OPS_READ_CACHE: dict[str, tuple[float, object]] = {}
_OPS_READ_CACHE_LOCK = RLock()
_INSPECTION_TRUSTED_STAGE_SOURCES = {"pts_browser_structured", "pts_local_chrome_profile", "pts_browser_session"}


def clear_ops_read_cache(*, module_code: str | None = None) -> None:
    """Clear short-lived console read cache after writes/syncs.

    The cache is only a UI speed-up. Sync and execute paths must invalidate it
    so the next module refresh reads the latest snapshot/task state.
    """
    with _OPS_READ_CACHE_LOCK:
        if module_code is None:
            _OPS_READ_CACHE.clear()
            return
        prefixes = (
            f"summary:{module_code}",
            f"pending:{module_code}:",
            f"recent:{module_code}:",
        )
        exact_keys = {
            "dashboard:summary",
        }
        if module_code == "visit":
            exact_keys.add("visit:owners")
        if module_code == "proactive":
            exact_keys.add("proactive:owners")
        for key in list(_OPS_READ_CACHE):
            if key in exact_keys or any(key.startswith(prefix) for prefix in prefixes):
                _OPS_READ_CACHE.pop(key, None)


class OpsService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.sync_service = SyncService(db)
        self.settings = get_settings()
        self.module_repo = ModuleConfigRepository(db)
        self.snapshot_repo = SourceSnapshotRepository(db)
        self.record_repo = NormalizedRecordRepository(db)
        self.task_repo = TaskPlanRepository(db)
        self.task_run_repo = TaskRunRepository(db)
        self.inspection_report_scanner = InspectionReportScanner(self.settings.inspection_report_root)
        self.inspection_report_matcher = InspectionReportMatcher(required_file_types=("word",))
        self._inspection_report_files_cache = None
        self._inspection_report_match_cache: dict[str, object] = {}
        self._pending_task_groups_cache: dict[
            tuple[str | None, str | None, str | None], list[dict[str, object]]
        ] = {}
        self._active_pending_snapshot_id_cache: dict[str, object | None] = {}

    def _cache_get(self, key: str) -> object | None:
        ttl = max(float(self.settings.ops_read_cache_ttl_seconds or 0), 0.0)
        if ttl <= 0:
            return None
        now = monotonic()
        with _OPS_READ_CACHE_LOCK:
            cached = _OPS_READ_CACHE.get(key)
            if cached is None:
                return None
            expires_at, value = cached
            if expires_at <= now:
                _OPS_READ_CACHE.pop(key, None)
                return None
            return value

    def _cache_get_with_hit(self, key: str) -> tuple[bool, object | None]:
        value = self._cache_get(key)
        if value is None:
            return False, None
        return True, value

    def _cache_set(self, key: str, value: object) -> object:
        ttl = max(float(self.settings.ops_read_cache_ttl_seconds or 0), 0.0)
        if ttl <= 0:
            return value
        with _OPS_READ_CACHE_LOCK:
            _OPS_READ_CACHE[key] = (monotonic() + ttl, value)
        return value

    def _served_at(self) -> datetime:
        return datetime.now(timezone.utc)

    def _cache_resolve(self, key: str, loader) -> tuple[object, bool]:
        hit, cached_value = self._cache_get_with_hit(key)
        if hit:
            return cached_value, True
        value = loader()
        self._cache_set(key, value)
        return value, False

    def build_overview_cached_meta(self) -> tuple[list[OpsOverviewItem], bool, datetime]:
        cache_key = "dashboard:summary"
        value, cached = self._cache_resolve(cache_key, self.build_overview)
        return value, cached, self._served_at()  # type: ignore[return-value]

    def list_failures_cached_meta(self, *, limit: int = 20) -> tuple[list[OpsEventItem], bool, datetime]:
        cache_key = f"dashboard:failures:{limit}"
        value, cached = self._cache_resolve(cache_key, lambda: self.list_failures(limit=limit))
        return value, cached, self._served_at()  # type: ignore[return-value]

    def list_manual_required_cached_meta(self, *, limit: int = 20) -> tuple[list[OpsEventItem], bool, datetime]:
        cache_key = f"dashboard:manual:{limit}"
        value, cached = self._cache_resolve(cache_key, lambda: self.list_manual_required(limit=limit))
        return value, cached, self._served_at()  # type: ignore[return-value]

    def get_module_summary_cached_meta(self, module_code: str) -> tuple[OpsOverviewItem | None, bool, datetime]:
        cache_key = f"summary:{module_code}"
        value, cached = self._cache_resolve(
            cache_key,
            lambda: next((current for current in self.build_overview() if current.module_code == module_code), None),
        )
        return value, cached, self._served_at()  # type: ignore[return-value]

    def get_module_summary_cached(self, module_code: str) -> OpsOverviewItem | None:
        item, _, _ = self.get_module_summary_cached_meta(module_code)
        return item

    def list_pending_tasks_cached_meta(
        self,
        *,
        module_code: str,
        limit: int,
        month: str | None = None,
        visit_owner: str | None = None,
    ) -> tuple[list[PendingTaskItem], bool, datetime]:
        cache_key = f"pending:{module_code}:{month or '-'}:{(visit_owner or '').strip() or '-'}:{limit}"
        value, cached = self._cache_resolve(
            cache_key,
            lambda: self.list_pending_tasks(
                module_code=module_code,
                limit=limit,
                month=month,
                visit_owner=visit_owner,
            ),
        )
        return value, cached, self._served_at()  # type: ignore[return-value]

    def list_pending_tasks_cached(
        self,
        *,
        module_code: str,
        limit: int,
        month: str | None = None,
        visit_owner: str | None = None,
    ) -> list[PendingTaskItem]:
        items, _, _ = self.list_pending_tasks_cached_meta(
            module_code=module_code,
            limit=limit,
            month=month,
            visit_owner=visit_owner,
        )
        return items

    def list_recent_visit_links_cached_meta(
        self,
        *,
        module_code: str = "visit",
        limit: int | None = 10,
    ) -> tuple[list[RecentVisitLinkItem], bool, datetime]:
        cache_key = f"recent:{module_code}:{limit if limit is not None else 'all'}"
        value, cached = self._cache_resolve(
            cache_key,
            lambda: self.list_recent_visit_links(module_code=module_code, limit=limit),
        )
        return value, cached, self._served_at()  # type: ignore[return-value]

    def list_recent_visit_links_cached(
        self,
        *,
        module_code: str = "visit",
        limit: int | None = 10,
    ) -> list[RecentVisitLinkItem]:
        items, _, _ = self.list_recent_visit_links_cached_meta(module_code=module_code, limit=limit)
        return items

    def list_recent_inspection_closures_cached_meta(
        self,
        *,
        month: str | None = None,
        limit: int | None = 10,
    ) -> tuple[list[RecentInspectionClosureItem], bool, datetime]:
        cache_key = f"recent:inspection:{month or '-'}:{limit if limit is not None else 'all'}"
        value, cached = self._cache_resolve(
            cache_key,
            lambda: self.list_recent_inspection_closures(month=month, limit=limit),
        )
        return value, cached, self._served_at()  # type: ignore[return-value]

    def list_recent_inspection_closures_cached(
        self,
        *,
        month: str | None = None,
        limit: int | None = 10,
    ) -> list[RecentInspectionClosureItem]:
        items, _, _ = self.list_recent_inspection_closures_cached_meta(month=month, limit=limit)
        return items

    def list_inspection_reviewed_tasks_cached_meta(
        self,
        *,
        month: str | None = None,
        limit: int = 100,
    ) -> tuple[list[PendingTaskItem], bool, datetime]:
        cache_key = f"inspection:reviewed:{month or '-'}:{limit}"
        value, cached = self._cache_resolve(
            cache_key,
            lambda: self.list_inspection_reviewed_tasks(month=month, limit=limit),
        )
        return value, cached, self._served_at()  # type: ignore[return-value]

    def list_inspection_reviewed_tasks_cached(
        self,
        *,
        month: str | None = None,
        limit: int = 100,
    ) -> list[PendingTaskItem]:
        items, _, _ = self.list_inspection_reviewed_tasks_cached_meta(month=month, limit=limit)
        return items

    def list_inspection_no_action_tasks_cached_meta(
        self,
        *,
        month: str | None = None,
        limit: int = 100,
    ) -> tuple[list[PendingTaskItem], bool, datetime]:
        cache_key = f"inspection:no_action:{month or '-'}:{limit}"
        value, cached = self._cache_resolve(
            cache_key,
            lambda: self.list_inspection_no_action_tasks(month=month, limit=limit),
        )
        return value, cached, self._served_at()  # type: ignore[return-value]

    def list_inspection_no_action_tasks_cached(
        self,
        *,
        month: str | None = None,
        limit: int = 100,
    ) -> list[PendingTaskItem]:
        items, _, _ = self.list_inspection_no_action_tasks_cached_meta(month=month, limit=limit)
        return items

    def list_visit_owners_cached_meta(self) -> tuple[list[str], bool, datetime]:
        cache_key = "visit:owners"
        value, cached = self._cache_resolve(cache_key, self.list_visit_owners)
        return value, cached, self._served_at()  # type: ignore[return-value]

    def list_visit_owners_cached(self) -> list[str]:
        owners, _, _ = self.list_visit_owners_cached_meta()
        return owners

    def list_proactive_owners_cached_meta(self) -> tuple[list[str], bool, datetime]:
        cache_key = "proactive:owners"
        value, cached = self._cache_resolve(cache_key, self.list_proactive_owners)
        return value, cached, self._served_at()  # type: ignore[return-value]

    def list_proactive_owners_cached(self) -> list[str]:
        owners, _, _ = self.list_proactive_owners_cached_meta()
        return owners

    def build_overview(self) -> list[OpsOverviewItem]:
        module_summaries = self.sync_service.build_module_summaries()
        configs = {item.module_code: item for item in self.module_repo.list_all()}
        runtime_snapshot = runtime_state.snapshot()
        running_modules = set(runtime_snapshot["running_sync_modules"])

        items: list[OpsOverviewItem] = []
        for summary in module_summaries:
            latest_execute_status = None
            latest_execute_time = None
            latest_execute_explanation = None
            failed_task_count = 0
            manual_required_count = 0
            retryable_task_count = 0
            tasks = self.task_repo.list_latest_by_business_key(module_code=summary.module_code, status=None)
            latest_runs_by_task_id = self._latest_runs_by_task_ids(tasks)
            pending_month = self._current_month_string() if summary.module_code == "inspection" else None
            pending_task_count = len(
                self._collect_pending_task_groups(module_code=summary.module_code, month=pending_month)
            )
            for task in tasks:
                latest_run = latest_runs_by_task_id.get(task.id)
                if latest_run is None:
                    continue
                if latest_execute_time is None or latest_run.run_time > latest_execute_time:
                    latest_execute_status = latest_run.run_status
                    latest_execute_time = latest_run.run_time
                    latest_execute_explanation = build_run_view(
                        run_status=latest_run.run_status,
                        result_payload=latest_run.result_payload,
                        manual_required=latest_run.manual_required,
                        retryable=self._resolve_retryable(latest_run),
                        error_message=latest_run.error_message,
                        task_plan_id=str(task.id),
                        task_run_id=str(latest_run.id),
                    )["business_explanation"]
                if latest_run.manual_required:
                    manual_required_count += 1
                retryable = self._resolve_retryable(latest_run)
                if latest_run.run_status in {"failed", "precheck_failed"}:
                    failed_task_count += 1
                if retryable:
                    retryable_task_count += 1

            config = configs.get(summary.module_code)
            schedule_type = None
            schedule_value = None
            schedule_enabled = False
            if config and config.enabled:
                if config.sync_cron:
                    schedule_type = "cron"
                    schedule_value = config.sync_cron
                    schedule_enabled = True
                elif (config.extra_config or {}).get("schedule_type") == "interval":
                    schedule_type = "interval"
                    schedule_value = f"{config.extra_config.get('schedule_interval_minutes', 0)}m"
                    schedule_enabled = True

            items.append(
                OpsOverviewItem(
                    module_code=summary.module_code,
                    module_name=summary.module_name,
                    latest_snapshot_time=summary.latest_snapshot_time,
                    latest_sync_status=summary.sync_status,
                    latest_sync_status_label=status_label(summary.sync_status),
                    latest_execute_status=latest_execute_status,
                    latest_execute_status_label=status_label(latest_execute_status),
                    latest_execute_explanation=latest_execute_explanation,
                    row_count=summary.row_count,
                    planned_tasks=pending_task_count,
                    skipped_tasks=summary.skipped_tasks,
                    manual_required_count=manual_required_count,
                    failed_task_count=failed_task_count,
                    retryable_task_count=retryable_task_count,
                    sync_running=summary.module_code in running_modules,
                    schedule_enabled=schedule_enabled,
                    schedule_type=schedule_type,
                    schedule_value=schedule_value,
                )
            )
        return items

    def list_pending_tasks(
        self,
        module_code: str | None = None,
        limit: int = 20,
        month: str | None = None,
        visit_owner: str | None = None,
    ) -> list[PendingTaskItem]:
        items: list[PendingTaskItem] = []
        runtime_snapshot = runtime_state.snapshot()
        running_task_ids = set(runtime_snapshot["running_task_ids"])
        queued_task_ids = set(runtime_snapshot.get("queued_task_ids", []))
        groups = self._collect_pending_task_groups(module_code=module_code, month=month, visit_owner=visit_owner)
        inspection_link_state_map = self._inspection_latest_trusted_state_by_link(
            [
                self._normalize_work_order_link(
                    ((getattr(group.get("record"), "normalized_data", {}) or {}) if group.get("record") else {}).get(
                        "work_order_link"
                    )
                )
                for group in groups
                if (getattr(group.get("task"), "module_code", None) == "inspection")
            ]
        )
        for group in groups:
            task = group["task"]
            latest_run = group["latest_run"]
            record = group["record"]
            customer_name = (
                getattr(record, "customer_name", None)
                or (getattr(record, "normalized_data", {}) or {}).get("customer_name")
                or (task.planned_payload or {}).get("customer_name")
            )
            latest_run_view = (
                build_run_view(
                    run_status=latest_run.run_status,
                    result_payload=latest_run.result_payload,
                    manual_required=latest_run.manual_required,
                    retryable=self._resolve_retryable(latest_run),
                    error_message=latest_run.error_message,
                    customer_name=customer_name,
                    task_plan_id=str(task.id),
                    task_run_id=str(latest_run.id),
                )
                if latest_run
                else None
            )
            normalized_data = (getattr(record, "normalized_data", {}) or {}) if record else {}
            report_word_file = self._resolve_report_word_file(task, normalized_data)
            state = self._resolve_pending_state(
                task=task,
                latest_run=latest_run,
                normalized_data=normalized_data,
                normalized_record_updated_at=getattr(record, "updated_at", None),
                running_task_ids=running_task_ids,
                queued_task_ids=queued_task_ids,
                has_report_word_file=bool(report_word_file),
                inspection_trusted_state_by_link=inspection_link_state_map,
            )
            technical_state = self._resolve_technical_state(
                task=task,
                latest_run=latest_run,
                running_task_ids=running_task_ids,
                queued_task_ids=queued_task_ids,
            )
            if task.module_code == "inspection" and state["code"] in {"reviewed_no_action", "no_action"}:
                # Keep reviewed/no-action items out of the pending list by default.
                continue
            can_execute = state["code"] in {"actionable", "failed", "upload_failed", "complete_failed"}
            if task.module_code == "inspection" and not report_word_file:
                can_execute = False
            if task.module_code == "inspection" and state["code"] not in {"actionable", "upload_failed", "complete_failed"}:
                can_execute = False
            if task.module_code == "visit" and state["code"] in {"pending_confirmation"}:
                can_execute = True
            items.append(
                PendingTaskItem(
                    task_plan_id=str(task.id),
                    module_code=task.module_code,
                    task_type=task.task_type,
                    customer_name=customer_name,
                    delivery_id=normalized_data.get("delivery_id"),
                    visit_type=normalized_data.get("visit_type"),
                    visit_owner=normalized_data.get("visit_owner"),
                    inspection_month=normalized_data.get("inspection_month"),
                    executor_name=normalized_data.get("executor_name"),
                    work_order_link=normalized_data.get("work_order_link"),
                    work_order_closed=normalized_data.get("work_order_closed"),
                    report_word_file=report_word_file,
                    planned_payload=task.planned_payload or {},
                    latest_run_status=latest_run.run_status if latest_run else None,
                    latest_run_status_label=latest_run_view["display_status"] if latest_run_view else None,
                    latest_run_time=latest_run.run_time if latest_run else None,
                    business_explanation=latest_run_view["business_explanation"] if latest_run_view else "等待执行",
                    business_state_code=state["code"],
                    business_state_label=state["label"],
                    business_state_tone=state["tone"],
                    technical_state_code=technical_state["code"],
                    technical_state_label=technical_state["label"],
                    technical_state_tone=technical_state["tone"],
                    technical_detail=technical_state.get("detail"),
                    state_code=state["code"],
                    state_label=state["label"],
                    state_tone=state["tone"],
                    can_execute=can_execute,
                    detail_url=f"/console/tasks?module_code={task.module_code}&status=planned&task_id={task.id}",
                )
            )
        items.sort(
            key=lambda item: (
                item.latest_run_time is not None,
                item.latest_run_time.isoformat() if item.latest_run_time else "",
                item.task_plan_id,
            ),
            reverse=True,
        )
        return items[:limit]

    def list_inspection_reviewed_tasks(
        self,
        *,
        month: str | None = None,
        limit: int = 100,
    ) -> list[PendingTaskItem]:
        runtime_snapshot = runtime_state.snapshot()
        running_task_ids = set(runtime_snapshot["running_task_ids"])
        queued_task_ids = set(runtime_snapshot.get("queued_task_ids", []))
        items: list[PendingTaskItem] = []
        tasks = self.task_repo.list_latest_by_business_key(module_code="inspection", status=None)
        latest_runs_by_task_id = self._latest_runs_by_task_ids(tasks)
        records_by_id = self.record_repo.get_by_ids([task.normalized_record_id for task in tasks])
        groups: list[dict[str, object]] = []
        for task in tasks:
            if task.plan_status != "planned":
                continue
            record = records_by_id.get(task.normalized_record_id)
            normalized_data = (getattr(record, "normalized_data", {}) or {}) if record else {}
            if month and normalized_data.get("inspection_month") != month:
                continue
            groups.append(
                {
                    "task": task,
                    "record": record,
                    "latest_run": latest_runs_by_task_id.get(task.id),
                }
            )
        deduped_groups = self._dedupe_inspection_groups_by_work_order_link(groups)
        inspection_link_state_map = self._inspection_latest_trusted_state_by_link(
            [
                self._normalize_work_order_link(
                    ((getattr(group.get("record"), "normalized_data", {}) or {}) if group.get("record") else {}).get(
                        "work_order_link"
                    )
                )
                for group in deduped_groups
            ]
        )
        for group in deduped_groups:
            task = group["task"]
            latest_run = group["latest_run"]
            record = group["record"]
            normalized_data = (getattr(record, "normalized_data", {}) or {}) if record else {}
            state = self._resolve_pending_state(
                task=task,
                latest_run=latest_run,
                normalized_data=normalized_data,
                normalized_record_updated_at=getattr(record, "updated_at", None),
                running_task_ids=running_task_ids,
                queued_task_ids=queued_task_ids,
                has_report_word_file=bool(self._resolve_report_word_file(task, normalized_data)),
                inspection_trusted_state_by_link=inspection_link_state_map,
            )
            if state["code"] != "reviewed_no_action":
                continue
            customer_name = (
                getattr(record, "customer_name", None)
                or normalized_data.get("customer_name")
                or (task.planned_payload or {}).get("customer_name")
            )
            latest_run_view = (
                build_run_view(
                    run_status=latest_run.run_status,
                    result_payload=latest_run.result_payload,
                    manual_required=latest_run.manual_required,
                    retryable=self._resolve_retryable(latest_run),
                    error_message=latest_run.error_message,
                    customer_name=customer_name,
                    task_plan_id=str(task.id),
                    task_run_id=str(latest_run.id),
                )
                if latest_run
                else None
            )
            items.append(
                PendingTaskItem(
                    task_plan_id=str(task.id),
                    module_code=task.module_code,
                    task_type=task.task_type,
                    customer_name=customer_name,
                    inspection_month=normalized_data.get("inspection_month"),
                    executor_name=normalized_data.get("executor_name"),
                    work_order_link=normalized_data.get("work_order_link"),
                    work_order_closed=normalized_data.get("work_order_closed"),
                    report_word_file=self._resolve_report_word_file(task, normalized_data),
                    planned_payload=task.planned_payload or {},
                    latest_run_status=latest_run.run_status if latest_run else None,
                    latest_run_status_label=latest_run_view["display_status"] if latest_run_view else None,
                    latest_run_time=latest_run.run_time if latest_run else None,
                    business_explanation=latest_run_view["business_explanation"] if latest_run_view else "无需处理",
                    state_code=state["code"],
                    state_label=state["label"],
                    state_tone=state["tone"],
                    can_execute=False,
                    detail_url=f"/console/tasks?module_code={task.module_code}&status=planned&task_id={task.id}",
                )
            )
        items.sort(
            key=lambda item: (
                item.latest_run_time is not None,
                item.latest_run_time.isoformat() if item.latest_run_time else "",
                item.task_plan_id,
            ),
            reverse=True,
        )
        return items[:limit]

    def list_inspection_no_action_tasks(
        self,
        *,
        month: str | None = None,
        limit: int = 100,
    ) -> list[PendingTaskItem]:
        items: list[PendingTaskItem] = []
        tasks = self.task_repo.list_latest_by_business_key(module_code="inspection", status=None)
        latest_runs_by_task_id = self._latest_runs_by_task_ids(tasks)
        records_by_id = self.record_repo.get_by_ids([task.normalized_record_id for task in tasks])
        groups: list[dict[str, object]] = []
        for task in tasks:
            record = records_by_id.get(task.normalized_record_id)
            normalized_data = (getattr(record, "normalized_data", {}) or {}) if record else {}
            if month and normalized_data.get("inspection_month") != month:
                continue
            groups.append(
                {
                    "task": task,
                    "record": record,
                    "latest_run": latest_runs_by_task_id.get(task.id),
                }
            )
        deduped_groups = self._dedupe_inspection_groups_by_work_order_link(groups)
        for group in deduped_groups:
            task = group["task"]
            latest_run = group["latest_run"]
            record = group["record"]
            normalized_data = (getattr(record, "normalized_data", {}) or {}) if record else {}
            if normalized_data.get("inspection_done") is True:
                continue
            service_type = str(normalized_data.get("service_type") or "")
            executor_name = str(normalized_data.get("executor_name") or "")
            if "巡检" not in service_type or executor_name != "舒磊":
                continue
            customer_name = (
                getattr(record, "customer_name", None)
                or normalized_data.get("customer_name")
                or (task.planned_payload or {}).get("customer_name")
            )
            latest_run_view = (
                build_run_view(
                    run_status=latest_run.run_status,
                    result_payload=latest_run.result_payload,
                    manual_required=latest_run.manual_required,
                    retryable=self._resolve_retryable(latest_run),
                    error_message=latest_run.error_message,
                    customer_name=customer_name,
                    task_plan_id=str(task.id),
                    task_run_id=str(latest_run.id),
                )
                if latest_run
                else None
            )
            items.append(
                PendingTaskItem(
                    task_plan_id=str(task.id),
                    module_code=task.module_code,
                    task_type=task.task_type,
                    customer_name=customer_name,
                    inspection_month=normalized_data.get("inspection_month"),
                    executor_name=normalized_data.get("executor_name"),
                    work_order_link=normalized_data.get("work_order_link"),
                    work_order_closed=normalized_data.get("work_order_closed"),
                    report_word_file=self._resolve_report_word_file(task, normalized_data),
                    planned_payload=task.planned_payload or {},
                    latest_run_status=latest_run.run_status if latest_run else None,
                    latest_run_status_label=latest_run_view["display_status"] if latest_run_view else None,
                    latest_run_time=latest_run.run_time if latest_run else None,
                    business_explanation=latest_run_view["business_explanation"] if latest_run_view else "无需处理",
                    state_code="no_action",
                    state_label="无需处理",
                    state_tone="success",
                    can_execute=False,
                    detail_url=f"/console/tasks?module_code={task.module_code}&status=all&task_id={task.id}",
                )
            )
        items.sort(
            key=lambda item: (
                item.latest_run_time is not None,
                item.latest_run_time.isoformat() if item.latest_run_time else "",
                item.task_plan_id,
            ),
            reverse=True,
        )
        return items[:limit]

    def list_visit_owners(self) -> list[str]:
        owners: set[str] = set()
        tasks = self.task_repo.list_latest_by_business_key(module_code="visit", status=None)
        records = self.record_repo.get_by_ids([task.normalized_record_id for task in tasks])
        for task in tasks:
            record = records.get(task.normalized_record_id)
            normalized_data = (getattr(record, "normalized_data", {}) or {}) if record else {}
            if str(normalized_data.get("visit_status") or "").strip() != "已回访":
                continue
            if str(normalized_data.get("visit_link") or "").strip():
                continue
            owner = str(normalized_data.get("visit_owner") or "").strip()
            if owner:
                owners.add(owner)
        return sorted(owners)

    def list_proactive_owners(self) -> list[str]:
        owners: set[str] = set()
        tasks = self.task_repo.list_latest_by_business_key(module_code="proactive", status=None)
        records = self.record_repo.get_by_ids([task.normalized_record_id for task in tasks])
        for task in tasks:
            record = records.get(task.normalized_record_id)
            normalized_data = (getattr(record, "normalized_data", {}) or {}) if record else {}
            if str(normalized_data.get("liaison_status") or "").strip() != "已建联":
                continue
            if str(normalized_data.get("visit_link") or "").strip():
                continue
            if not str(normalized_data.get("feedback_note") or "").strip():
                continue
            owner = str(normalized_data.get("visit_owner") or "").strip()
            if owner:
                owners.add(owner)
        return sorted(owners)

    def list_pending_inspection_months(self) -> list[str]:
        months: set[str] = set()
        for group in self._collect_pending_task_groups(module_code="inspection", month=None):
            record = group["record"]
            normalized_data = (getattr(record, "normalized_data", {}) or {}) if record else {}
            month = normalized_data.get("inspection_month")
            if isinstance(month, str) and month:
                months.add(month)
        return sorted(months, reverse=True)

    def list_known_inspection_months(self) -> list[str]:
        months: set[str] = set()
        tasks = self.task_repo.list_latest_by_business_key(module_code="inspection", status=None)
        records = self.record_repo.get_by_ids([task.normalized_record_id for task in tasks])
        for task in tasks:
            record = records.get(task.normalized_record_id)
            normalized_data = (getattr(record, "normalized_data", {}) or {}) if record else {}
            month = normalized_data.get("inspection_month")
            if isinstance(month, str) and month:
                months.add(month)
        return sorted(months, reverse=True)

    def list_recent_visit_links(
        self,
        *,
        module_code: str = "visit",
        limit: int | None = 10,
    ) -> list[RecentVisitLinkItem]:
        items: list[RecentVisitLinkItem] = []
        seen_links: set[str] = set()
        recent_limit = max((limit or 100) * 10, 50)
        for task_run in self.task_run_repo.list_recent(limit=recent_limit):
            task_plan = self.task_repo.get_by_id(task_run.task_plan_id)
            if task_plan is None or task_plan.module_code != module_code:
                continue
            record = self.record_repo.get_by_id(task_plan.normalized_record_id)
            normalized_data = (getattr(record, "normalized_data", {}) or {}) if record else {}
            if module_code == "visit":
                is_closed = self._visit_run_counts_as_closed(task_run, normalized_data)
            elif module_code == "proactive":
                is_closed = self._proactive_run_counts_as_closed(task_run, normalized_data)
            else:
                is_closed = False
            if not is_closed:
                continue
            result_payload = task_run.result_payload or {}
            final_link = result_payload.get("final_link") or getattr(task_run, "final_link", None)
            if not final_link or final_link in seen_links:
                continue
            customer_name = self._resolve_customer_name(result_payload, record)
            visit_type = normalized_data.get("visit_type") or (task_plan.planned_payload or {}).get("visit_type")
            items.append(
                RecentVisitLinkItem(
                    customer_name=customer_name,
                    visit_type=visit_type,
                    final_link=str(final_link),
                    occurred_at=task_run.run_time,
                    detail_url=f"/console/task-runs/{task_run.id}",
                    task_plan_id=str(task_plan.id),
                    task_run_id=str(task_run.id),
                )
            )
            seen_links.add(str(final_link))
            if limit is not None and len(items) >= limit:
                break
        return items

    def list_recent_inspection_closures(
        self,
        *,
        month: str | None = None,
        limit: int | None = 10,
    ) -> list[RecentInspectionClosureItem]:
        items: list[RecentInspectionClosureItem] = []
        seen_links: set[str] = set()
        recent_limit = max((limit or 100) * 10, 50)
        latest_tasks = self.task_repo.list_latest_by_business_key(module_code="inspection", status=None)
        latest_records_by_id = self.record_repo.get_by_ids([task.normalized_record_id for task in latest_tasks])
        latest_state_by_business_key: dict[tuple[str, str], tuple[object, dict[str, object], str | None]] = {}
        latest_state_by_work_order_link: dict[str, dict[str, object]] = {}
        for latest_task in latest_tasks:
            latest_record = latest_records_by_id.get(latest_task.normalized_record_id)
            source_row_id = getattr(latest_record, "source_row_id", None) or str(latest_task.normalized_record_id)
            latest_normalized_data = (getattr(latest_record, "normalized_data", {}) or {}) if latest_record else {}
            latest_state_by_business_key[(str(source_row_id), latest_task.task_type)] = (
                latest_record,
                latest_normalized_data,
                latest_normalized_data.get("inspection_month"),
            )
            normalized_link = self._normalize_work_order_link(latest_normalized_data.get("work_order_link"))
            if normalized_link:
                latest_state_by_work_order_link[normalized_link] = latest_normalized_data
        for task_run in self.task_run_repo.list_recent(limit=recent_limit):
            if task_run.run_status != "success" or task_run.manual_required:
                continue
            task_plan = self.task_repo.get_by_id(task_run.task_plan_id)
            if task_plan is None or task_plan.module_code != "inspection":
                continue
            record = self.record_repo.get_by_id(task_plan.normalized_record_id)
            source_row_id = getattr(record, "source_row_id", None) or str(task_plan.normalized_record_id)
            latest_record, normalized_data, inspection_month = latest_state_by_business_key.get(
                (str(source_row_id), task_plan.task_type),
                (
                    record,
                    (getattr(record, "normalized_data", {}) or {}) if record else {},
                    ((getattr(record, "normalized_data", {}) or {}) if record else {}).get("inspection_month"),
                ),
            )
            if month and inspection_month != month:
                continue
            if not self._inspection_run_counts_as_closed(task_run, normalized_data):
                continue
            final_link = (
                (task_run.result_payload or {}).get("final_link")
                or getattr(task_run, "final_link", None)
                or normalized_data.get("work_order_link")
            )
            if not final_link or str(final_link) in seen_links:
                continue
            normalized_final_link = self._normalize_work_order_link(final_link)
            latest_link_state = latest_state_by_work_order_link.get(normalized_final_link)
            if latest_link_state is not None and not self._inspection_normalized_state_counts_as_closed(latest_link_state):
                continue
            items.append(
                RecentInspectionClosureItem(
                    customer_name=self._resolve_customer_name(task_run.result_payload or {}, latest_record),
                    inspection_month=inspection_month,
                    final_link=str(final_link),
                    occurred_at=task_run.run_time,
                    detail_url=f"/console/task-runs/{task_run.id}",
                    task_plan_id=str(task_plan.id),
                    task_run_id=str(task_run.id),
                )
            )
            seen_links.add(str(final_link))
            if limit is not None and len(items) >= limit:
                break
        return items

    def _inspection_run_counts_as_closed(
        self,
        task_run,
        normalized_data: dict[str, object],
    ) -> bool:
        if self._inspection_normalized_state_counts_as_open(normalized_data):
            return False
        if not self._inspection_normalized_state_counts_as_closed(normalized_data):
            return False
        payload = task_run.result_payload or {}
        if payload.get("execution_mode") != "real":
            return False
        refresh_state = payload.get("_inspection_state_refresh") or {}
        if refresh_state.get("status") in {"reopened_or_open", "failed", "", None}:
            return False
        diagnostics = payload.get("runner_diagnostics") or {}
        postcheck = diagnostics.get("postcheck") or {}
        postcheck_passed = payload.get("postcheck_passed")
        closure_confirmed = payload.get("closure_confirmed")
        report_attached_confirmed = payload.get("report_attached_confirmed")
        if postcheck_passed is None:
            postcheck_passed = postcheck.get("postcheck_passed")
        if closure_confirmed is None:
            closure_confirmed = postcheck.get("closure_confirmed")
        if report_attached_confirmed is None:
            report_attached_confirmed = postcheck.get("report_attached_confirmed")
        if (
            refresh_state.get("status") == "closed_confirmed"
            and postcheck_passed is True
            and closure_confirmed is True
            and report_attached_confirmed is True
        ):
            return True
        return False

    @staticmethod
    def _inspection_normalized_state_counts_as_closed(normalized_data: dict[str, object]) -> bool:
        if normalized_data.get("work_order_closed") is not True:
            return False
        source = str(normalized_data.get("debug_work_order_stage_source") or "").strip()
        stage = str(
            normalized_data.get("work_order_stage")
            or normalized_data.get("debug_work_order_stage_normalized")
            or ""
        ).strip()
        if source not in {"pts_local_chrome_profile", "pts_browser_session", "pts_browser_structured"}:
            return False
        return stage in {"审核工单", "完成"}

    @staticmethod
    def _inspection_normalized_state_counts_as_open(normalized_data: dict[str, object]) -> bool:
        source = str(normalized_data.get("debug_work_order_stage_source") or "").strip()
        stage = str(
            normalized_data.get("work_order_stage")
            or normalized_data.get("debug_work_order_stage_normalized")
            or ""
        ).strip()
        if source not in {"pts_local_chrome_profile", "pts_browser_session", "pts_browser_structured"}:
            return False
        if not stage:
            return False
        return stage not in {"审核工单", "完成"}

    @staticmethod
    def _visit_run_counts_as_closed(task_run, normalized_data: dict[str, object]) -> bool:
        if task_run.manual_required:
            return False
        payload = dict(task_run.result_payload or {})
        if task_run.run_status != "success":
            return False
        if payload.get("execution_mode") != "real":
            return False
        diagnostics = payload.get("runner_diagnostics") or {}
        postcheck = diagnostics.get("postcheck") or {}
        postcheck_passed = payload.get("postcheck_passed")
        closure_confirmed = payload.get("closure_confirmed")
        delivery_bound_confirmed = payload.get("delivery_bound_confirmed")
        feedback_confirmed = payload.get("feedback_confirmed")
        if postcheck_passed is None:
            postcheck_passed = postcheck.get("postcheck_passed")
        if closure_confirmed is None:
            closure_confirmed = postcheck.get("closure_confirmed")
        if delivery_bound_confirmed is None:
            delivery_bound_confirmed = postcheck.get("delivery_bound_confirmed")
        if feedback_confirmed is None:
            feedback_confirmed = postcheck.get("feedback_confirmed")
        if (
            postcheck_passed is True
            and closure_confirmed is True
            and delivery_bound_confirmed is True
            and feedback_confirmed is True
        ):
            return True
        # 兼容人工闭环后的钉钉行：仅在回访链接已写回时算闭环
        return bool(str(normalized_data.get("visit_link") or "").strip())

    @staticmethod
    def _proactive_run_counts_as_closed(task_run, normalized_data: dict[str, object]) -> bool:
        if task_run.manual_required:
            return False
        payload = dict(task_run.result_payload or {})
        if task_run.run_status != "success":
            return False
        if payload.get("execution_mode") != "real":
            return False
        final_link = payload.get("final_link") or getattr(task_run, "final_link", None)
        return bool(str(final_link or "").strip())

    def list_failures(self, limit: int = 20) -> list[OpsEventItem]:
        items: list[OpsEventItem] = []
        for snapshot in self.snapshot_repo.list_failed(limit=limit):
            ops = (snapshot.raw_meta or {}).get("_ops", {})
            view = build_run_view(
                run_status=snapshot.sync_status,
                result_payload={"runner_diagnostics": {"error_type": "http_error" if ops.get("retryable") else None}},
                manual_required=False,
                retryable=bool(ops.get("retryable", False)),
                error_message=snapshot.sync_error,
            )
            items.append(
                OpsEventItem(
                    kind="sync",
                    module_code=snapshot.module_code,
                    title=f"{snapshot.module_code} sync failed",
                    status=snapshot.sync_status,
                    occurred_at=snapshot.sync_time,
                    message=snapshot.sync_error,
                    retryable=bool(ops.get("retryable", False)),
                    display_status=view["display_status"],
                    status_tone=view["status_tone"],
                    error_type=view["error_type"],
                    business_explanation=view["business_explanation"],
                    rerun_available=True,
                    snapshot_id=str(snapshot.id),
                )
            )

        for task_run in self.task_run_repo.list_recent(limit=limit):
            if task_run.run_status not in {"failed", "precheck_failed"}:
                continue
            task_plan = self.task_repo.get_by_id(task_run.task_plan_id)
            if task_plan is None:
                continue
            retryable = self._resolve_retryable(task_run)
            record = self.record_repo.get_by_id(task_plan.normalized_record_id)
            customer_name = self._resolve_customer_name(task_run.result_payload or {}, record)
            view = build_run_view(
                run_status=task_run.run_status,
                result_payload=task_run.result_payload,
                manual_required=task_run.manual_required,
                retryable=retryable,
                error_message=task_run.error_message,
                customer_name=customer_name,
                task_plan_id=str(task_plan.id),
                task_run_id=str(task_run.id),
            )
            items.append(
                OpsEventItem(
                    kind="execute",
                    module_code=task_plan.module_code,
                    title=f"{task_plan.module_code} execute {task_run.run_status}",
                    status=task_run.run_status,
                    occurred_at=task_run.run_time,
                    message=task_run.error_message,
                    retryable=retryable,
                    manual_required=task_run.manual_required,
                    customer_name=customer_name,
                    display_status=view["display_status"],
                    status_tone=view["status_tone"],
                    error_type=view["error_type"],
                    business_explanation=view["business_explanation"],
                    detail_url=view["detail_url"],
                    rerun_available=True,
                    task_plan_id=str(task_plan.id),
                    task_run_id=str(task_run.id),
                )
            )
        items.sort(key=lambda item: item.occurred_at, reverse=True)
        return items[:limit]

    def list_manual_required(self, limit: int = 20) -> list[OpsEventItem]:
        items: list[OpsEventItem] = []
        for task_run in self.task_run_repo.list_recent(limit=limit * 3):
            if not task_run.manual_required:
                continue
            task_plan = self.task_repo.get_by_id(task_run.task_plan_id)
            if task_plan is None:
                continue
            record = self.record_repo.get_by_id(task_plan.normalized_record_id)
            customer_name = self._resolve_customer_name(task_run.result_payload or {}, record)
            view = build_run_view(
                run_status=task_run.run_status,
                result_payload=task_run.result_payload,
                manual_required=True,
                retryable=False,
                error_message=task_run.error_message,
                customer_name=customer_name,
                task_plan_id=str(task_plan.id),
                task_run_id=str(task_run.id),
            )
            items.append(
                OpsEventItem(
                    kind="execute",
                    module_code=task_plan.module_code,
                    title=f"{task_plan.module_code} manual required",
                    status=task_run.run_status,
                    occurred_at=task_run.run_time,
                    message=task_run.error_message,
                    retryable=False,
                    manual_required=True,
                    customer_name=customer_name,
                    display_status=view["display_status"],
                    status_tone=view["status_tone"],
                    error_type=view["error_type"],
                    business_explanation=view["business_explanation"],
                    detail_url=view["detail_url"],
                    rerun_available=True,
                    task_plan_id=str(task_plan.id),
                    task_run_id=str(task_run.id),
                )
            )
        items.sort(key=lambda item: item.occurred_at, reverse=True)
        return items[:limit]

    @staticmethod
    def _resolve_retryable(task_run) -> bool:
        ops = (task_run.result_payload or {}).get("_ops", {})
        if "retryable" in ops:
            return bool(ops.get("retryable", False))
        return bool(getattr(task_run, "retryable", False))

    @staticmethod
    def _resolve_customer_name(result_payload: dict, record) -> str | None:
        return (
            result_payload.get("customer_name")
            or getattr(record, "customer_name", None)
            or (getattr(record, "normalized_data", {}) or {}).get("customer_name")
        )

    def _is_effectively_completed_run(self, task, task_run, normalized_data: dict[str, object]) -> bool:
        if task_run.manual_required:
            return False
        if task.module_code == "inspection":
            if task_run.run_status != "success":
                return False
            return self._inspection_run_counts_as_closed(task_run, normalized_data)
        if task.module_code == "visit":
            return self._visit_run_counts_as_closed(task_run, normalized_data)
        if task.module_code == "proactive":
            return self._proactive_run_counts_as_closed(task_run, normalized_data)
        return task_run.run_status in {"success", "simulated_success"}

    def _successful_business_key_counts_as_completed(
        self,
        *,
        module_code: str,
        source_row_id: str,
        task_type: str,
        normalized_data: dict[str, object],
    ) -> bool:
        latest_success = self.task_run_repo.latest_success_for_business_key(
            module_code=module_code,
            source_row_id=source_row_id,
            task_type=task_type,
        )
        if latest_success is None:
            return False
        if module_code == "inspection":
            return self._inspection_normalized_state_counts_as_closed(normalized_data)
        if module_code == "visit":
            return self._visit_run_counts_as_closed(latest_success, normalized_data)
        if module_code == "proactive":
            return self._proactive_run_counts_as_closed(latest_success, normalized_data)
        return True

    def _latest_runs_by_task_ids(self, tasks) -> dict[object, object]:
        latest_runs_by_task_id: dict[object, object] = {}
        task_ids = [task.id for task in tasks]
        for task_run in self.task_run_repo.list_by_task_plan_ids(task_ids):
            latest_runs_by_task_id.setdefault(task_run.task_plan_id, task_run)
        return latest_runs_by_task_id

    def _collect_pending_task_groups(
        self,
        module_code: str | None = None,
        month: str | None = None,
        visit_owner: str | None = None,
    ) -> list[dict[str, object]]:
        owner_key = (visit_owner or "").strip() or None
        cache_key = (module_code, month, owner_key)
        cached_groups = self._pending_task_groups_cache.get(cache_key)
        if cached_groups is not None:
            return cached_groups
        tasks = self.task_repo.list_latest_by_business_key(module_code=module_code, status=None)
        latest_runs_by_task_id = self._latest_runs_by_task_ids(tasks)
        records_by_id = self.record_repo.get_by_ids([task.normalized_record_id for task in tasks])
        active_snapshot_ids = self._active_pending_snapshot_ids(module_code)
        successful_keys = self.task_run_repo.list_successful_business_keys(module_code=module_code)
        grouped: dict[tuple[str, str, str], dict[str, object]] = {}
        for task in tasks:
            record = records_by_id.get(task.normalized_record_id)
            active_snapshot_id = active_snapshot_ids.get(task.module_code)
            if active_snapshot_id is not None:
                record_snapshot_id = getattr(record, "snapshot_id", None)
                if record is None or record_snapshot_id != active_snapshot_id:
                    continue
            source_row_id = getattr(record, "source_row_id", None) or str(task.normalized_record_id)
            key = (task.module_code, source_row_id, task.task_type)
            latest_run = latest_runs_by_task_id.get(task.id)
            candidate = {
                "task": task,
                "record": record,
                "latest_run": latest_run,
                "all_runs": [latest_run] if latest_run is not None else [],
            }
            existing = grouped.get(key)
            if existing is None or self._is_newer_task(task, existing["task"]):
                grouped[key] = candidate

        pending_groups: list[dict[str, object]] = []
        for group in grouped.values():
            task = group["task"]
            record = group["record"]
            source_row_id = getattr(record, "source_row_id", None) or str(task.normalized_record_id)
            normalized_data = (getattr(record, "normalized_data", {}) or {}) if record else {}
            if task.plan_status != "planned":
                continue
            if month and task.module_code == "inspection":
                if normalized_data.get("inspection_month") != month:
                    continue
            if task.module_code in {"visit", "proactive"} and owner_key:
                if str(normalized_data.get("visit_owner") or "").strip() != owner_key:
                    continue
            if task.module_code != "inspection":
                if (task.module_code, source_row_id, task.task_type) in successful_keys:
                    if self._successful_business_key_counts_as_completed(
                        module_code=task.module_code,
                        source_row_id=source_row_id,
                        task_type=task.task_type,
                        normalized_data=normalized_data,
                    ):
                        continue
            all_runs = group["all_runs"]
            if task.module_code != "inspection":
                if any(self._is_effectively_completed_run(task, run, normalized_data) for run in all_runs):
                    continue
            if any(run.manual_required for run in all_runs):
                if task.module_code != "inspection":
                    continue
            pending_groups.append(group)
        if module_code in {None, "inspection"}:
            pending_groups = self._dedupe_inspection_groups_by_work_order_link(pending_groups)
        self._pending_task_groups_cache[cache_key] = pending_groups
        return pending_groups

    def _active_pending_snapshot_ids(self, module_code: str | None) -> dict[str, object]:
        target_modules = {"visit", "proactive"}
        if module_code in {"visit", "proactive"}:
            target_modules = {module_code}
        elif module_code is not None:
            return {}
        snapshot_ids: dict[str, object] = {}
        for current_module in target_modules:
            snapshot_id = self._active_pending_snapshot_id_for_module(current_module)
            if snapshot_id is not None:
                snapshot_ids[current_module] = snapshot_id
        return snapshot_ids

    def _active_pending_snapshot_id_for_module(self, module_code: str) -> object | None:
        cached = self._active_pending_snapshot_id_cache.get(module_code)
        if cached is not None:
            return cached
        snapshots = self.snapshot_repo.list_recent(module_code=module_code, limit=10)
        selected = None
        for snapshot in snapshots:
            if getattr(snapshot, "sync_status", None) == "success":
                selected = snapshot
                break
        if selected is None and snapshots:
            selected = snapshots[0]
        snapshot_id = getattr(selected, "id", None) if selected is not None else None
        self._active_pending_snapshot_id_cache[module_code] = snapshot_id
        return snapshot_id

    def _dedupe_inspection_groups_by_work_order_link(
        self,
        groups: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        selected: dict[str, dict[str, object]] = {}
        passthrough: list[dict[str, object]] = []
        for group in groups:
            task = group.get("task")
            if task is None or getattr(task, "module_code", None) != "inspection":
                passthrough.append(group)
                continue
            record = group.get("record")
            normalized_data = (getattr(record, "normalized_data", {}) or {}) if record else {}
            normalized_link = self._normalize_work_order_link(normalized_data.get("work_order_link"))
            if not normalized_link:
                passthrough.append(group)
                continue
            existing = selected.get(normalized_link)
            if existing is None or self._inspection_group_preferred(group, existing):
                selected[normalized_link] = group
        return passthrough + list(selected.values())

    @staticmethod
    def _inspection_group_preferred(candidate: dict[str, object], existing: dict[str, object]) -> bool:
        def to_ts(value: object) -> float:
            if isinstance(value, datetime):
                try:
                    return value.timestamp()
                except Exception:
                    return float("-inf")
            return float("-inf")

        def rank(group: dict[str, object]) -> tuple[int, float, float, str]:
            task = group.get("task")
            latest_run = group.get("latest_run")
            latest_run_time = getattr(latest_run, "run_time", None)
            task_created_at = getattr(task, "created_at", None)
            task_id = str(getattr(task, "id", ""))
            return (
                1 if latest_run_time is not None else 0,
                to_ts(latest_run_time),
                to_ts(task_created_at),
                task_id,
            )

        return rank(candidate) > rank(existing)

    def _inspection_latest_trusted_state_by_link(self, links: list[str | None]) -> dict[str, str]:
        normalized_links = [str(link).strip() for link in links if isinstance(link, str) and str(link).strip()]
        if not normalized_links:
            return {}
        state_by_link: dict[str, str] = {}
        run_state_by_link = self._inspection_latest_run_state_by_link(normalized_links)
        state_by_link.update(run_state_by_link)
        statement = (
            select(NormalizedRecord)
            .where(
                NormalizedRecord.module_code == "inspection",
                NormalizedRecord.normalized_data["work_order_link"].astext.in_(normalized_links),
            )
            .order_by(desc(NormalizedRecord.updated_at), desc(NormalizedRecord.created_at))
        )
        rows = self.db.scalars(statement).all()
        seen_latest_link: set[str] = set()
        for row in rows:
            normalized_data = dict(row.normalized_data or {})
            normalized_link = self._normalize_work_order_link(normalized_data.get("work_order_link"))
            if (
                not normalized_link
                or normalized_link in seen_latest_link
                or normalized_link in state_by_link
            ):
                continue
            seen_latest_link.add(normalized_link)
            source = str(normalized_data.get("debug_work_order_stage_source") or "").strip()
            stage = str(
                normalized_data.get("work_order_stage")
                or normalized_data.get("debug_work_order_stage_normalized")
                or ""
            ).strip()
            if source not in _INSPECTION_TRUSTED_STAGE_SOURCES:
                # The latest record for this work order does not carry trusted stage source.
                # Do not fallback to older rows to avoid stale "已审核" state after reopen.
                continue
            if stage in {"审核工单", "完成"} and normalized_data.get("work_order_closed") is True:
                state_by_link[normalized_link] = "closed"
                continue
            if stage and stage not in {"审核工单", "完成"}:
                state_by_link[normalized_link] = "open"
        return state_by_link

    def _inspection_latest_run_state_by_link(self, normalized_links: list[str]) -> dict[str, str]:
        if not normalized_links:
            return {}
        statement = (
            select(
                NormalizedRecord.normalized_data["work_order_link"].astext.label("work_order_link"),
                TaskRun.run_time,
                TaskRun.run_status,
                TaskRun.manual_required,
                TaskRun.result_payload,
            )
            .join(TaskPlan, TaskPlan.normalized_record_id == NormalizedRecord.id)
            .join(TaskRun, TaskRun.task_plan_id == TaskPlan.id)
            .where(
                NormalizedRecord.module_code == "inspection",
                NormalizedRecord.normalized_data["work_order_link"].astext.in_(normalized_links),
            )
            .order_by(desc(TaskRun.run_time), desc(TaskRun.created_at))
        )
        rows = self.db.execute(statement).all()
        state_by_link: dict[str, str] = {}
        for row in rows:
            normalized_link = self._normalize_work_order_link(getattr(row, "work_order_link", None))
            if not normalized_link or normalized_link in state_by_link:
                continue
            state = self._inspection_state_from_run(
                run_status=str(getattr(row, "run_status", "") or ""),
                manual_required=bool(getattr(row, "manual_required", False)),
                result_payload=getattr(row, "result_payload", {}) or {},
            )
            if state in {"open", "closed"}:
                state_by_link[normalized_link] = state
        return state_by_link

    @classmethod
    def _inspection_state_from_run(
        cls,
        *,
        run_status: str,
        manual_required: bool,
        result_payload: dict[str, object],
    ) -> str | None:
        payload = dict(result_payload or {})
        refresh_state = payload.get("_inspection_state_refresh") or {}
        refresh_status = str(refresh_state.get("status") or "").strip()
        if refresh_status == "reopened_or_open":
            return "open"
        if refresh_status == "closed_confirmed" and cls._inspection_payload_has_closed_evidence(payload):
            return "closed"
        diagnostics = payload.get("runner_diagnostics") or {}
        failed_action = str(diagnostics.get("failed_action") or "").strip()
        error_type = str(diagnostics.get("error_type") or "").strip()
        if (
            manual_required
            and failed_action == "validate_execution_preconditions"
            and error_type == "already_closed_before_execution"
        ):
            return "closed"
        if run_status == "success" and cls._inspection_payload_has_closed_evidence(payload):
            return "closed"
        return None

    @staticmethod
    def _inspection_payload_has_closed_evidence(payload: dict[str, object]) -> bool:
        if payload.get("execution_mode") != "real":
            return False
        diagnostics = payload.get("runner_diagnostics") or {}
        postcheck = diagnostics.get("postcheck") or {}
        postcheck_passed = payload.get("postcheck_passed")
        closure_confirmed = payload.get("closure_confirmed")
        report_attached_confirmed = payload.get("report_attached_confirmed")
        if postcheck_passed is None:
            postcheck_passed = postcheck.get("postcheck_passed")
        if closure_confirmed is None:
            closure_confirmed = postcheck.get("closure_confirmed")
        if report_attached_confirmed is None:
            report_attached_confirmed = postcheck.get("report_attached_confirmed")
        return (
            postcheck_passed is True
            and closure_confirmed is True
            and report_attached_confirmed is True
        )

    def _resolve_pending_state(
        self,
        *,
        task,
        latest_run,
        normalized_data: dict[str, object],
        normalized_record_updated_at: datetime | None,
        running_task_ids: set[str],
        queued_task_ids: set[str],
        has_report_word_file: bool,
        inspection_trusted_state_by_link: dict[str, str] | None = None,
    ) -> dict[str, str]:
        if str(task.id) in queued_task_ids:
            return {"code": "queued", "label": "排队中", "tone": "warning"}
        if str(task.id) in running_task_ids:
            return {"code": "running", "label": "执行中", "tone": "warning"}

        if task.module_code == "visit":
            if not str(normalized_data.get("delivery_id") or "").strip():
                return {"code": "manual_required", "label": "缺少Delivery ID", "tone": "manual"}
            if not str(normalized_data.get("pts_link") or "").strip():
                return {"code": "manual_required", "label": "缺少PTS链接", "tone": "manual"}
            if latest_run and self._visit_run_counts_as_closed(latest_run, normalized_data):
                return {"code": "closed_success", "label": "已闭环", "tone": "success"}
            if latest_run and latest_run.manual_required:
                return {"code": "manual_required", "label": "需人工处理", "tone": "manual"}
            if latest_run and latest_run.run_status == "pending_confirmation":
                return {"code": "pending_confirmation", "label": "状态待确认", "tone": "warning"}
            if latest_run and latest_run.run_status == "success":
                return {"code": "pending_confirmation", "label": "状态待确认", "tone": "warning"}
            if latest_run and latest_run.run_status in {"failed", "precheck_failed"}:
                return {"code": "failed", "label": "执行失败", "tone": "failed"}
            return {"code": "actionable", "label": "可执行", "tone": "success"}

        if task.module_code != "inspection":
            return {"code": "actionable", "label": "可执行", "tone": "success"}

        # Inspection state priority:
        # 1) reviewed_no_action
        # 2) manual_required_owner
        # 3) upload_failed
        # 4) complete_failed
        # 5) missing_report
        # 6) actionable
        if self._inspection_is_reviewed_no_action(
            latest_run,
            normalized_data,
            normalized_record_updated_at=normalized_record_updated_at,
        ):
            return {"code": "reviewed_no_action", "label": "已审核工单（无需处理）", "tone": "success"}
        normalized_link = self._normalize_work_order_link(normalized_data.get("work_order_link"))
        historical_state = (
            (inspection_trusted_state_by_link or {}).get(normalized_link)
            if normalized_link
            else None
        )
        if historical_state == "closed" and not self._inspection_normalized_state_counts_as_open(normalized_data):
            return {"code": "reviewed_no_action", "label": "已审核工单（无需处理）", "tone": "success"}
        if self._inspection_is_manual_required_owner(latest_run):
            return {"code": "manual_required_owner", "label": "需手动指定负责人", "tone": "manual"}
        failure_code = self._inspection_failure_state_code(latest_run)
        if failure_code == "upload_failed":
            return {"code": "upload_failed", "label": "上传失败", "tone": "failed"}
        if failure_code == "complete_failed":
            return {"code": "complete_failed", "label": "完成处理失败", "tone": "failed"}
        if normalized_data.get("inspection_done") is not True:
            return {"code": "no_action", "label": "无需处理", "tone": "success"}
        if not has_report_word_file:
            return {"code": "missing_report", "label": "缺少报告", "tone": "warning"}
        return {"code": "actionable", "label": "可自动闭环", "tone": "warning"}

    @staticmethod
    def _resolve_technical_state(
        *,
        task,
        latest_run,
        running_task_ids: set[str],
        queued_task_ids: set[str],
    ) -> dict[str, str]:
        task_id = str(getattr(task, "id", "") or "")
        if task_id in queued_task_ids:
            return {
                "code": "queued",
                "label": "排队中",
                "tone": "warning",
                "detail": "任务已进入后台队列，等待 worker 处理。",
            }
        if task_id in running_task_ids:
            return {
                "code": "running",
                "label": "执行中",
                "tone": "warning",
                "detail": "任务正在执行动作链。",
            }
        if latest_run is None:
            return {
                "code": "idle",
                "label": "未执行",
                "tone": "unknown",
                "detail": "当前还没有执行记录。",
            }
        payload = dict(getattr(latest_run, "result_payload", {}) or {})
        diagnostics = payload.get("runner_diagnostics") or {}
        failed_action = str(diagnostics.get("failed_action") or "").strip()
        error_type = str(diagnostics.get("error_type") or "").strip()
        refresh_state = payload.get("_inspection_state_refresh") or {}
        refresh_status = str(refresh_state.get("status") or "").strip()
        detail_parts: list[str] = []
        if failed_action:
            detail_parts.append(f"failed_action={failed_action}")
        if error_type:
            detail_parts.append(f"error_type={error_type}")
        if refresh_status:
            detail_parts.append(f"refresh={refresh_status}")
        detail = " / ".join(detail_parts) or "存在执行记录，可在任务详情查看完整技术字段。"
        if latest_run.manual_required:
            return {
                "code": "manual_required",
                "label": "人工处理",
                "tone": "manual",
                "detail": detail,
            }
        if latest_run.run_status == "pending_confirmation":
            return {
                "code": "pending_confirmation",
                "label": "待确认",
                "tone": "warning",
                "detail": detail,
            }
        if latest_run.run_status in {"failed", "precheck_failed"}:
            return {
                "code": "failed",
                "label": "失败",
                "tone": "failed",
                "detail": detail,
            }
        if latest_run.run_status in {"success", "simulated_success"}:
            return {
                "code": "success",
                "label": "成功",
                "tone": "success",
                "detail": detail,
            }
        return {
            "code": str(latest_run.run_status or "unknown"),
            "label": str(latest_run.run_status or "未知"),
            "tone": "unknown",
            "detail": detail,
        }

    @staticmethod
    def _inspection_failure_state_code(latest_run) -> str | None:
        if latest_run is None or latest_run.run_status not in {"failed", "precheck_failed"}:
            return None
        payload = dict(getattr(latest_run, "result_payload", {}) or {})
        diagnostics = payload.get("runner_diagnostics") or {}
        failed_action = str(diagnostics.get("failed_action") or "").strip()
        error_type = str(diagnostics.get("error_type") or "").strip()
        if failed_action == "upload_report_files" or error_type == "upload_failed":
            return "upload_failed"
        if failed_action in {"complete_inspection", "postcheck_inspection_closure"} or error_type in {
            "complete_failed",
            "postcheck_failed",
        }:
            return "complete_failed"
        return None

    @classmethod
    def _inspection_is_reviewed_no_action(
        cls,
        latest_run,
        normalized_data: dict[str, object],
        *,
        normalized_record_updated_at: datetime | None = None,
    ) -> bool:
        if cls._inspection_normalized_state_counts_as_closed(normalized_data):
            return True
        if cls._inspection_normalized_state_counts_as_open(normalized_data):
            return False
        if latest_run is None:
            return False
        if (
            normalized_record_updated_at is not None
            and getattr(latest_run, "run_time", None) is not None
            and not cls._inspection_normalized_state_counts_as_closed(normalized_data)
        ):
            run_time = latest_run.run_time
            if isinstance(run_time, datetime):
                if run_time.tzinfo is None:
                    run_time = run_time.replace(tzinfo=timezone.utc)
                updated_at = normalized_record_updated_at
                if updated_at.tzinfo is None:
                    updated_at = updated_at.replace(tzinfo=timezone.utc)
                # Newer sync record should override stale reviewed conclusion from older runs.
                if updated_at >= run_time:
                    return False
        payload = dict(getattr(latest_run, "result_payload", {}) or {})
        refresh_state = payload.get("_inspection_state_refresh") or {}
        if refresh_state.get("status") == "closed_confirmed":
            return True
        diagnostics = payload.get("runner_diagnostics") or {}
        failed_action = str(diagnostics.get("failed_action") or "").strip()
        error_type = str(diagnostics.get("error_type") or "").strip()
        if (
            latest_run.run_status == "manual_required"
            and failed_action == "validate_execution_preconditions"
            and error_type == "already_closed_before_execution"
        ):
            return True
        return False

    @staticmethod
    def _inspection_is_manual_required_owner(latest_run) -> bool:
        if latest_run is None or not getattr(latest_run, "manual_required", False):
            return False
        payload = dict(getattr(latest_run, "result_payload", {}) or {})
        diagnostics = payload.get("runner_diagnostics") or {}
        failed_action = str(diagnostics.get("failed_action") or "").strip()
        error_type = str(diagnostics.get("error_type") or "").strip()
        if error_type in {"manual_required_owner", "permission_denied"}:
            return True
        if failed_action in {"add_member_if_missing", "assign_owner", "validate_pts_account"}:
            return True
        for item in payload.get("action_results") or []:
            if str(item.get("status") or "").strip() != "manual_required":
                continue
            action = str(item.get("action") or "").strip()
            item_error_type = str(item.get("error_type") or "").strip()
            if action in {"add_member_if_missing", "assign_owner", "validate_pts_account"}:
                return True
            if item_error_type in {"manual_required_owner", "permission_denied"}:
                return True
        return False

    def _resolve_report_word_file(self, task, normalized_data: dict) -> str | None:
        report_word_file = str(normalized_data.get("report_word_file") or "").strip()
        if report_word_file:
            return report_word_file
        payload = task.planned_payload or {}
        report_match = payload.get("report_match") or {}
        matched_files = report_match.get("matched_files") if isinstance(report_match, dict) else None
        if isinstance(matched_files, dict):
            word_files = matched_files.get("word")
            if isinstance(word_files, list) and word_files:
                return str(word_files[0])
        word_files = payload.get("word_files")
        if isinstance(word_files, list) and word_files:
            return str(word_files[0])
        if task.module_code == "inspection":
            match_result = self._match_inspection_report(str(normalized_data.get("customer_name") or ""))
            word_files = match_result.matched_files.get("word") if match_result.matched else None
            if isinstance(word_files, list) and word_files:
                return str(word_files[0])
        return None

    def _get_inspection_report_files(self):
        if self._inspection_report_files_cache is None:
            self._inspection_report_files_cache = self.inspection_report_scanner.scan()
        return self._inspection_report_files_cache

    def _match_inspection_report(self, customer_name: str):
        cache_key = (customer_name or "").strip()
        cached_result = self._inspection_report_match_cache.get(cache_key)
        if cached_result is not None:
            return cached_result
        match_result = self.inspection_report_matcher.match(cache_key, self._get_inspection_report_files())
        self._inspection_report_match_cache[cache_key] = match_result
        return match_result

    @staticmethod
    def _current_month_string() -> str:
        return datetime.now().strftime("%Y-%m")

    @staticmethod
    def _is_newer_task(task, current_task) -> bool:
        task_created_at = getattr(task, "created_at", None)
        current_created_at = getattr(current_task, "created_at", None)
        if task_created_at and current_created_at:
            return task_created_at > current_created_at
        return str(getattr(task, "id", "")) > str(getattr(current_task, "id", ""))

    @staticmethod
    def _is_task_pending(task, latest_run) -> bool:
        if task.plan_status != "planned":
            return False
        if latest_run is None:
            return True
        if latest_run.manual_required:
            return False
        return latest_run.run_status != "success"

    @staticmethod
    def _normalize_work_order_link(value: object) -> str:
        link = str(value or "").strip()
        if not link:
            return ""
        parts = urlsplit(link)
        normalized_path = parts.path.rstrip("/")
        return urlunsplit((parts.scheme, parts.netloc, normalized_path, parts.query, ""))
