from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from core.config import get_settings
from core.exceptions import OperationConflictError, ResourceNotFoundError
from core.runtime_state import runtime_state
from models.normalized_record import NormalizedRecord
from models.task_plan import TaskPlan
from repositories.normalized_record_repo import NormalizedRecordRepository
from repositories.task_plan_repo import TaskPlanRepository
from repositories.task_run_repo import TaskRunRepository
from schemas.common import TaskRunDetail
from services.executors.inspection_executor import InspectionExecutor
from services.executors.proactive_executor import ProactiveExecutor
from services.executors.schemas import ExecutionResult, ExecutorContext
from services.executors.visit_executor import VisitExecutor


EXECUTOR_REGISTRY = {
    ("visit", "visit_close"): VisitExecutor,
    ("inspection", "inspection_close"): InspectionExecutor,
    ("proactive", "proactive_visit_close"): ProactiveExecutor,
}


class TaskExecutionService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.settings = get_settings()
        self.task_plan_repo = TaskPlanRepository(db)
        self.record_repo = NormalizedRecordRepository(db)
        self.task_run_repo = TaskRunRepository(db)

    def precheck_task(self, task_id: uuid.UUID) -> TaskRunDetail:
        task_plan, record = self._load_task_context(task_id)
        result = self._run_precheck(task_plan, record)
        task_run = self.task_run_repo.create_from_result(
            task_plan.id,
            result,
            metadata={"trigger": "manual", "attempt": 1, "retried": False, "retry_count": 0},
        )
        self.db.commit()
        return self._to_task_run_detail(task_run)

    async def execute_task(
        self,
        task_id: uuid.UUID,
        *,
        dry_run: bool = False,
        trigger: str = "manual",
        allow_auto_retry: bool = True,
    ) -> TaskRunDetail:
        task_plan, record = self._load_task_context(task_id)
        task_lock_key = str(task_plan.id)
        if not runtime_state.acquire_task(task_lock_key):
            raise OperationConflictError(f"task {task_plan.id} 已在运行中，禁止重复 execute")

        try:
            precheck_result = self._run_precheck(task_plan, record)
            if precheck_result.run_status != "precheck_passed":
                task_run = self.task_run_repo.create_from_result(
                    task_plan.id,
                    precheck_result,
                    metadata={"trigger": trigger, "attempt": 1, "retried": False, "retry_count": 0},
                )
                self.db.commit()
                return self._to_task_run_detail(task_run)

            executor = self._select_executor(task_plan)
            assert record is not None
            context = self._build_executor_context(task_plan, record)
            if dry_run:
                result = await executor.dry_run(context)
                task_run = self.task_run_repo.create_from_result(
                    task_plan.id,
                    result,
                    metadata={"trigger": trigger, "attempt": 1, "retried": False, "retry_count": 0},
                )
                self.db.commit()
                return self._to_task_run_detail(task_run)

            max_attempts = max(1, self.settings.execute_retry_max_attempts)
            attempt = 1
            retry_count = 0
            current_trigger = trigger
            while True:
                result = await executor.execute(context)
                task_run = self.task_run_repo.create_from_result(
                    task_plan.id,
                    result,
                    metadata={
                        "trigger": current_trigger,
                        "attempt": attempt,
                        "retried": retry_count > 0,
                        "retry_count": retry_count,
                    },
                )
                self.db.commit()
                if not (
                    allow_auto_retry
                    and result.run_status == "failed"
                    and result.retryable
                    and attempt < max_attempts
                ):
                    return self._to_task_run_detail(task_run)
                attempt += 1
                retry_count += 1
                current_trigger = "retry"
        finally:
            runtime_state.release_task(task_lock_key)

    async def rerun_task(self, task_id: uuid.UUID, *, dry_run: bool = False) -> TaskRunDetail:
        return await self.execute_task(task_id, dry_run=dry_run, trigger="rerun")

    def get_task_run_detail(self, run_id: uuid.UUID) -> TaskRunDetail:
        task_run = self.task_run_repo.get_by_id(run_id)
        if task_run is None:
            raise ResourceNotFoundError(f"task run not found: {run_id}")
        return self._to_task_run_detail(task_run)

    def _load_task_context(self, task_id: uuid.UUID) -> tuple[TaskPlan, NormalizedRecord | None]:
        task_plan = self.task_plan_repo.get_by_id(task_id)
        if task_plan is None:
            raise ResourceNotFoundError(f"task not found: {task_id}")
        record = self.record_repo.get_by_id(task_plan.normalized_record_id)
        return task_plan, record

    def _run_precheck(self, task_plan: TaskPlan, record: NormalizedRecord | None) -> ExecutionResult:
        generic_failure = self._generic_precheck(task_plan, record)
        if generic_failure is not None:
            return generic_failure

        assert record is not None
        executor = self._select_executor(task_plan)
        context = self._build_executor_context(task_plan, record)
        return executor.precheck(context)

    def _generic_precheck(self, task_plan: TaskPlan, record: NormalizedRecord | None) -> ExecutionResult | None:
        if task_plan.plan_status != "planned":
            return ExecutionResult(
                run_status="precheck_failed",
                error_message="plan_status != planned，禁止执行",
                result_payload={"plan_status": task_plan.plan_status},
                executor_version="phase6-v1",
            )
        if record is None:
            return ExecutionResult(
                run_status="precheck_failed",
                error_message="关联 normalized_record 不存在",
                result_payload={"normalized_record_id": str(task_plan.normalized_record_id)},
                executor_version="phase6-v1",
            )
        if record.recognition_status == "failed":
            return ExecutionResult(
                run_status="precheck_failed",
                error_message="recognition_status == failed，禁止执行",
                result_payload={"recognition_status": record.recognition_status},
                executor_version="phase6-v1",
            )
        if (task_plan.module_code, task_plan.task_type) not in EXECUTOR_REGISTRY:
            return ExecutionResult(
                run_status="precheck_failed",
                error_message="executor 与 module_code / task_type 不匹配",
                result_payload={"module_code": task_plan.module_code, "task_type": task_plan.task_type},
                executor_version="phase6-v1",
            )
        return None

    def _select_executor(self, task_plan: TaskPlan):
        executor_cls = EXECUTOR_REGISTRY.get((task_plan.module_code, task_plan.task_type))
        if executor_cls is None:
            raise ValueError("executor 与 module_code / task_type 不匹配")
        return executor_cls()

    @staticmethod
    def _build_executor_context(task_plan: TaskPlan, record: NormalizedRecord) -> ExecutorContext:
        return ExecutorContext(
            task_plan_id=str(task_plan.id),
            module_code=task_plan.module_code,
            task_type=task_plan.task_type,
            plan_status=task_plan.plan_status,
            normalized_record_id=str(record.id),
            recognition_status=record.recognition_status,
            planned_payload=task_plan.planned_payload,
            normalized_data=record.normalized_data,
        )

    @staticmethod
    def _to_task_run_detail(task_run) -> TaskRunDetail:
        return TaskRunDetail(
            task_run_id=str(task_run.id),
            task_plan_id=str(task_run.task_plan_id),
            run_status=task_run.run_status,
            manual_required=task_run.manual_required,
            result_payload=task_run.result_payload,
            final_link=task_run.final_link,
            error_message=task_run.error_message,
            executor_version=task_run.executor_version,
            run_time=task_run.run_time,
            created_at=task_run.created_at,
        )
