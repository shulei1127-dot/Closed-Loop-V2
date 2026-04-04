from __future__ import annotations

from sqlalchemy.orm import Session

from core.runtime_state import runtime_state
from repositories.module_config_repo import ModuleConfigRepository
from repositories.normalized_record_repo import NormalizedRecordRepository
from repositories.task_plan_repo import TaskPlanRepository
from repositories.task_run_repo import TaskRunRepository
from repositories.source_snapshot_repo import SourceSnapshotRepository
from schemas.ops import OpsEventItem, OpsOverviewItem, PendingTaskItem, RecentVisitLinkItem
from services.ops_copy import build_run_view, status_label
from services.sync_service import SyncService


class OpsService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.sync_service = SyncService(db)
        self.module_repo = ModuleConfigRepository(db)
        self.snapshot_repo = SourceSnapshotRepository(db)
        self.record_repo = NormalizedRecordRepository(db)
        self.task_repo = TaskPlanRepository(db)
        self.task_run_repo = TaskRunRepository(db)

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
            pending_task_count = len(self._collect_pending_task_groups(module_code=summary.module_code))
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

    def list_pending_tasks(self, module_code: str | None = None, limit: int = 20) -> list[PendingTaskItem]:
        items: list[PendingTaskItem] = []
        for group in self._collect_pending_task_groups(module_code=module_code):
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
            items.append(
                PendingTaskItem(
                    task_plan_id=str(task.id),
                    module_code=task.module_code,
                    task_type=task.task_type,
                    customer_name=customer_name,
                    delivery_id=normalized_data.get("delivery_id"),
                    visit_type=normalized_data.get("visit_type"),
                    planned_payload=task.planned_payload or {},
                    latest_run_status=latest_run.run_status if latest_run else None,
                    latest_run_status_label=latest_run_view["display_status"] if latest_run_view else None,
                    latest_run_time=latest_run.run_time if latest_run else None,
                    business_explanation=latest_run_view["business_explanation"] if latest_run_view else "等待执行",
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

    def list_recent_visit_links(self, limit: int | None = 10) -> list[RecentVisitLinkItem]:
        items: list[RecentVisitLinkItem] = []
        seen_links: set[str] = set()
        recent_limit = max((limit or 100) * 10, 50)
        for task_run in self.task_run_repo.list_recent(limit=recent_limit):
            if task_run.run_status not in {"success", "simulated_success"} or task_run.manual_required:
                continue
            result_payload = task_run.result_payload or {}
            final_link = result_payload.get("final_link") or getattr(task_run, "final_link", None)
            if not final_link or final_link in seen_links:
                continue
            task_plan = self.task_repo.get_by_id(task_run.task_plan_id)
            if task_plan is None or task_plan.module_code != "visit":
                continue
            record = self.record_repo.get_by_id(task_plan.normalized_record_id)
            normalized_data = (getattr(record, "normalized_data", {}) or {}) if record else {}
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

    def _latest_runs_by_task_ids(self, tasks) -> dict[object, object]:
        latest_runs_by_task_id: dict[object, object] = {}
        task_ids = [task.id for task in tasks]
        for task_run in self.task_run_repo.list_by_task_plan_ids(task_ids):
            latest_runs_by_task_id.setdefault(task_run.task_plan_id, task_run)
        return latest_runs_by_task_id

    def _collect_pending_task_groups(self, module_code: str | None = None) -> list[dict[str, object]]:
        tasks = self.task_repo.list_latest_by_business_key(module_code=module_code, status=None)
        latest_runs_by_task_id = self._latest_runs_by_task_ids(tasks)
        records_by_id = self.record_repo.get_by_ids([task.normalized_record_id for task in tasks])
        successful_keys = self.task_run_repo.list_successful_business_keys(module_code=module_code)
        grouped: dict[tuple[str, str, str], dict[str, object]] = {}
        for task in tasks:
            record = records_by_id.get(task.normalized_record_id)
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
            if task.plan_status != "planned":
                continue
            if (task.module_code, source_row_id, task.task_type) in successful_keys:
                continue
            all_runs = group["all_runs"]
            if any(run.run_status in {"success", "simulated_success"} and not run.manual_required for run in all_runs):
                continue
            if any(run.manual_required for run in all_runs):
                continue
            pending_groups.append(group)
        return pending_groups

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
