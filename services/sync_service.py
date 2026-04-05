from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from core.config import get_settings
from core.exceptions import EnvironmentDependencyError, OperationConflictError, ResourceNotFoundError
from core.runtime_state import runtime_state
from models.normalized_record import NormalizedRecord
from models.source_snapshot import SourceSnapshot
from models.task_plan import TaskPlan
from repositories.module_config_repo import ModuleConfigRepository
from repositories.normalized_record_repo import NormalizedRecordRepository
from repositories.source_snapshot_repo import SourceSnapshotRepository
from repositories.task_plan_repo import TaskPlanRepository
from schemas.common import ModuleSummaryItem, RecordDetail, SnapshotDetail, TaskDetail
from schemas.sync import CollectResult, SyncRunResponse
from services.collectors.inspection_collector import InspectionCollector
from services.collectors.proactive_collector import ProactiveCollector
from services.collectors.visit_collector import VisitCollector
from services.module_registry import MODULE_DEFINITIONS, default_module_configs, get_module_definition
from services.planners.inspection_planner import InspectionPlanner
from services.planners.proactive_planner import ProactivePlanner
from services.planners.visit_planner import VisitPlanner
from services.recognizers.inspection_work_order_backfill import InspectionWorkOrderStageBackfill
from services.recognizers.inspection_recognizer import InspectionRecognizer
from services.recognizers.proactive_recognizer import ProactiveRecognizer
from services.recognizers.visit_delivery_backfill import VisitDeliveryIdBackfill
from services.recognizers.visit_recognizer import VisitRecognizer


COLLECTOR_REGISTRY = {
    "visit": VisitCollector,
    "inspection": InspectionCollector,
    "proactive": ProactiveCollector,
}

RECOGNIZER_REGISTRY = {
    "visit": VisitRecognizer,
    "inspection": InspectionRecognizer,
    "proactive": ProactiveRecognizer,
}

PLANNER_REGISTRY = {
    "visit": VisitPlanner,
    "inspection": InspectionPlanner,
    "proactive": ProactivePlanner,
}

ENRICHER_REGISTRY = {
    "visit": VisitDeliveryIdBackfill,
    "inspection": InspectionWorkOrderStageBackfill,
}


class SyncService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.settings = get_settings()
        self.module_repo = ModuleConfigRepository(db)
        self.snapshot_repo = SourceSnapshotRepository(db)
        self.record_repo = NormalizedRecordRepository(db)
        self.task_repo = TaskPlanRepository(db)

    def ensure_module_configs(self) -> None:
        try:
            self.module_repo.upsert_defaults(default_module_configs())
        except SQLAlchemyError as exc:
            raise EnvironmentDependencyError(
                error_type="module_config_unavailable",
                public_message="module config 无法加载",
                hint="请检查数据库迁移是否已执行，以及 module_configs 表是否可访问。",
                details={"reason": str(exc)},
            ) from exc

    async def run_sync(
        self,
        module_code: str,
        force: bool = False,
        *,
        trigger: str = "manual",
        allow_auto_retry: bool = True,
    ) -> SyncRunResponse:
        del force
        self.ensure_module_configs()
        get_module_definition(module_code)
        source_config = self.module_repo.get_source_config(module_code)
        if source_config is None:
            raise ResourceNotFoundError(f"module source config not found: {module_code}")

        if not runtime_state.acquire_sync(module_code):
            raise OperationConflictError(f"module {module_code} 已在运行中，禁止重复 sync")

        try:
            max_attempts = max(1, self.settings.sync_retry_max_attempts)
            attempt = 1
            retry_count = 0
            current_trigger = trigger
            while True:
                attempt_result = await self._run_single_attempt(
                    module_code=module_code,
                    source_config=source_config,
                    trigger=current_trigger,
                    attempt=attempt,
                )
                if not (
                    allow_auto_retry
                    and attempt_result["retryable"]
                    and attempt < max_attempts
                ):
                    return self._build_sync_response(
                        attempt_result["snapshot"],
                        recognition_counts=attempt_result["recognition_counts"],
                        task_plan_counts=attempt_result["task_plan_counts"],
                        trigger=current_trigger,
                        attempt=attempt,
                        retry_count=retry_count,
                        retryable=attempt_result["retryable"],
                    )
                retry_count += 1
                attempt += 1
                current_trigger = "retry"
        finally:
            runtime_state.release_sync(module_code)

    def build_module_summaries(self) -> list[ModuleSummaryItem]:
        self.ensure_module_configs()
        summaries: list[ModuleSummaryItem] = []
        for module_code, meta in MODULE_DEFINITIONS.items():
            latest = self.snapshot_repo.latest_for_module(module_code)
            if latest is None:
                summaries.append(
                    ModuleSummaryItem(
                        module_code=module_code,
                        module_name=meta["module_name"],
                    )
                )
                continue
            summaries.append(self._build_module_summary(latest, meta["module_name"]))
        return summaries

    def get_snapshot_detail(self, snapshot_id: uuid.UUID) -> SnapshotDetail:
        snapshot = self.snapshot_repo.get_by_id(snapshot_id)
        if snapshot is None:
            raise ResourceNotFoundError(f"snapshot not found: {snapshot_id}")
        return SnapshotDetail(
            snapshot_id=str(snapshot.id),
            module_code=snapshot.module_code,
            sync_time=snapshot.sync_time,
            sync_status=snapshot.sync_status,
            data_source=snapshot.data_source,
            row_count=snapshot.row_count,
            source_url=snapshot.source_url,
            source_doc_key=snapshot.source_doc_key,
            source_view_key=snapshot.source_view_key,
            sync_error=snapshot.sync_error,
            raw_columns=snapshot.raw_columns,
            raw_rows=snapshot.raw_rows,
            raw_meta=snapshot.raw_meta,
            created_at=snapshot.created_at,
        )

    def get_record_detail(self, record_id: uuid.UUID) -> RecordDetail:
        record = self.record_repo.get_by_id(record_id)
        if record is None:
            raise ResourceNotFoundError(f"record not found: {record_id}")
        return RecordDetail(
            record_id=str(record.id),
            snapshot_id=str(record.snapshot_id),
            module_code=record.module_code,
            source_row_id=record.source_row_id,
            customer_name=record.customer_name,
            normalized_data=record.normalized_data,
            field_mapping=record.field_mapping,
            field_confidence=record.field_confidence,
            recognition_status=record.recognition_status,
            field_evidence=record.field_evidence,
            field_samples=record.field_samples,
            unresolved_fields=record.unresolved_fields,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

    def get_task_detail(self, task_id: uuid.UUID) -> TaskDetail:
        task = self.task_repo.get_by_id(task_id)
        if task is None:
            raise ResourceNotFoundError(f"task not found: {task_id}")
        return TaskDetail(
            task_plan_id=str(task.id),
            module_code=task.module_code,
            normalized_record_id=str(task.normalized_record_id),
            task_type=task.task_type,
            eligibility=task.eligibility,
            skip_reason=task.skip_reason,
            planner_version=task.planner_version,
            plan_status=task.plan_status,
            planned_payload=task.planned_payload,
            created_at=task.created_at,
            updated_at=task.updated_at,
        )

    def get_latest_module_summary(self, module_code: str) -> ModuleSummaryItem:
        self.ensure_module_configs()
        meta = get_module_definition(module_code)
        latest = self.snapshot_repo.latest_for_module(module_code)
        if latest is None:
            raise ResourceNotFoundError(f"no snapshots found for module: {module_code}")
        return self._build_module_summary(latest, meta["module_name"])

    async def _run_single_attempt(
        self,
        *,
        module_code: str,
        source_config,
        trigger: str,
        attempt: int,
    ) -> dict[str, Any]:
        collector = COLLECTOR_REGISTRY[module_code](source_config)
        recognizer = RECOGNIZER_REGISTRY[module_code]()
        planner = PLANNER_REGISTRY[module_code]()

        ops_meta = {
            "trigger": trigger,
            "attempt": attempt,
            "module_code": module_code,
        }
        try:
            health = collector.healthcheck()
            collector.validate()
            collect_result = await collector.collect()
            collect_result.raw_meta.setdefault("collector_health", health)
            collect_result.raw_meta.setdefault("_ops", {}).update(
                {
                    **ops_meta,
                    "retryable": False,
                }
            )
            snapshot = self.snapshot_repo.create_from_collect_result(collect_result)
            recognition_result = recognizer.recognize(collect_result.raw_columns, collect_result.raw_rows)
            recognition_result = await self._enrich_recognition(module_code, recognition_result)
            record_map = self.record_repo.create_from_recognition(snapshot.id, module_code, recognition_result)
            task_plans = planner.plan(recognition_result.normalized_records)
            created_task_plans = self.task_repo.create_from_dtos(task_plans, record_map)
            self.db.commit()
            recognition_counts = self._build_recognition_stats(
                recognition_result.normalized_records,
                recognition_result.unresolved_fields,
            )
            task_plan_counts = self._build_task_plan_stats(created_task_plans)
            return {
                "snapshot": snapshot,
                "recognition_counts": recognition_counts,
                "task_plan_counts": task_plan_counts,
                "retryable": False,
            }
        except Exception as exc:
            error_type, retryable = self._classify_sync_exception(exc)
            failure_result = CollectResult(
                module_code=module_code,
                source_url=source_config.source_url,
                source_doc_key=source_config.source_doc_key,
                source_view_key=source_config.source_view_key,
                data_source="unavailable",
                sync_status="failed",
                sync_error=str(exc),
                raw_columns=[],
                raw_rows=[],
                raw_meta={
                    "collector": collector.__class__.__name__,
                    "collector_type": getattr(source_config, "collector_type", "unknown"),
                    "_ops": {
                        **ops_meta,
                        "retryable": retryable,
                        "error_type": error_type,
                    },
                },
            )
            snapshot = self.snapshot_repo.create_from_collect_result(failure_result)
            self.db.commit()
            return {
                "snapshot": snapshot,
                "recognition_counts": self._build_recognition_stats([], []),
                "task_plan_counts": self._build_task_plan_stats([]),
                "retryable": retryable,
            }

    async def _enrich_recognition(self, module_code: str, recognition_result):
        enricher_cls = ENRICHER_REGISTRY.get(module_code)
        if enricher_cls is None:
            return recognition_result
        enricher = enricher_cls()
        recognition_result.normalized_records = await enricher.enrich_records(recognition_result.normalized_records)
        return recognition_result

    def _build_module_summary(self, latest: SourceSnapshot, module_name: str) -> ModuleSummaryItem:
        full_records = self.db.scalar(
            select(func.count(NormalizedRecord.id)).where(
                NormalizedRecord.snapshot_id == latest.id,
                NormalizedRecord.recognition_status == "full",
            )
        )
        partial_records = self.db.scalar(
            select(func.count(NormalizedRecord.id)).where(
                NormalizedRecord.snapshot_id == latest.id,
                NormalizedRecord.recognition_status == "partial",
            )
        )
        failed_records = self.db.scalar(
            select(func.count(NormalizedRecord.id)).where(
                NormalizedRecord.snapshot_id == latest.id,
                NormalizedRecord.recognition_status == "failed",
            )
        )
        planned_tasks = self.db.scalar(
            select(func.count(TaskPlan.id))
            .join(NormalizedRecord, TaskPlan.normalized_record_id == NormalizedRecord.id)
            .where(
                NormalizedRecord.snapshot_id == latest.id,
                TaskPlan.plan_status == "planned",
            )
        )
        skipped_tasks = self.db.scalar(
            select(func.count(TaskPlan.id))
            .join(NormalizedRecord, TaskPlan.normalized_record_id == NormalizedRecord.id)
            .where(
                NormalizedRecord.snapshot_id == latest.id,
                TaskPlan.plan_status == "skipped",
            )
        )
        return ModuleSummaryItem(
            module_code=latest.module_code,
            module_name=module_name,
            snapshot_id=str(latest.id),
            latest_snapshot_time=latest.sync_time,
            sync_status=latest.sync_status,
            row_count=latest.row_count,
            full_records=full_records or 0,
            partial_records=partial_records or 0,
            failed_records=failed_records or 0,
            planned_tasks=planned_tasks or 0,
            skipped_tasks=skipped_tasks or 0,
        )

    def _build_sync_response(
        self,
        snapshot: SourceSnapshot,
        *,
        recognition_counts: dict[str, int],
        task_plan_counts: dict[str, int],
        trigger: str,
        attempt: int,
        retry_count: int,
        retryable: bool,
    ) -> SyncRunResponse:
        return SyncRunResponse(
            snapshot=SyncRunResponse.SnapshotSummary(
                snapshot_id=str(snapshot.id),
                module_code=snapshot.module_code,
                sync_status=snapshot.sync_status,
                data_source=snapshot.data_source,
                row_count=snapshot.row_count,
            ),
            recognition=SyncRunResponse.RecognitionStats(**recognition_counts),
            task_plans=SyncRunResponse.TaskPlanStats(**task_plan_counts),
            run_context=SyncRunResponse.RunContext(
                trigger=trigger,
                attempt=attempt,
                retry_count=retry_count,
                retried=retry_count > 0,
                retryable=retryable,
            ),
        )

    @staticmethod
    def _build_recognition_stats(normalized_records: list[dict], unresolved_fields: list[str]) -> dict[str, int]:
        full_count = sum(1 for item in normalized_records if item.get("recognition_status", "full") == "full")
        partial_count = sum(1 for item in normalized_records if item.get("recognition_status") == "partial")
        failed_count = sum(1 for item in normalized_records if item.get("recognition_status") == "failed")
        return {
            "record_count": len(normalized_records),
            "full_count": full_count,
            "partial_count": partial_count,
            "failed_count": failed_count,
            "unresolved_field_count": len(unresolved_fields),
        }

    @staticmethod
    def _build_task_plan_stats(task_plans: list[TaskPlan]) -> dict[str, int]:
        planned_count = sum(1 for item in task_plans if item.plan_status == "planned")
        skipped_count = sum(1 for item in task_plans if item.plan_status == "skipped")
        return {
            "total_count": len(task_plans),
            "planned_count": planned_count,
            "skipped_count": skipped_count,
        }

    @staticmethod
    def _classify_sync_exception(exc: Exception) -> tuple[str, bool]:
        if isinstance(exc, TimeoutError):
            return "temporary_timeout", True
        if isinstance(exc, (ConnectionError, OSError)):
            return "temporary_network_error", True
        if isinstance(exc, ValueError):
            return "configuration_error", False
        return "unexpected_error", False
