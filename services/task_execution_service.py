from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from core.config import get_settings
from core.exceptions import OperationConflictError, ResourceNotFoundError
from core.runtime_state import runtime_state
from models.normalized_record import NormalizedRecord
from models.task_plan import TaskPlan
from repositories.normalized_record_repo import NormalizedRecordRepository
from repositories.module_config_repo import ModuleConfigRepository
from repositories.task_plan_repo import TaskPlanRepository
from repositories.task_run_repo import TaskRunRepository
from schemas.common import TaskRunDetail
from services.executors.proactive_executor import ProactiveExecutor
from services.executors.proactive_tag_mark_executor import ProactiveTagMarkExecutor
from services.executors.review_executor import ReviewExecutor
from services.executors.schemas import ExecutionResult, ExecutorContext
from services.executors.visit_executor import VisitExecutor


EXECUTOR_REGISTRY = {
    ("visit", "visit_close"): VisitExecutor,
    ("proactive", "proactive_visit_close"): ProactiveExecutor,
    ("proactive", "proactive_tag_mark"): ProactiveTagMarkExecutor,
    ("review", "review_audit"): ReviewExecutor,
}


class TaskExecutionService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.settings = get_settings()
        self.task_plan_repo = TaskPlanRepository(db)
        self.record_repo = NormalizedRecordRepository(db)
        self.module_config_repo = ModuleConfigRepository(db)
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

    def preview_task(self, task_id: uuid.UUID) -> TaskRunDetail:
        task_plan, record = self._load_task_context(task_id)
        result = self._run_precheck(task_plan, record)
        task_run = self.task_run_repo.create_from_result(
            task_plan.id,
            result,
            metadata={"trigger": "preview", "attempt": 1, "retried": False, "retry_count": 0},
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

        duplicate_failure = self._duplicate_execution_guard(task_plan, record)
        if duplicate_failure is not None:
            return duplicate_failure

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

    def _duplicate_execution_guard(
        self,
        task_plan: TaskPlan,
        record: NormalizedRecord | None,
    ) -> ExecutionResult | None:
        if record is None:
            return None

        # 交付转售后回访（visit）：防止同一行重复闭环
        if task_plan.module_code == "visit" and task_plan.task_type == "visit_close":
            if not record.source_row_id:
                return None
            existing_success = self.task_run_repo.latest_success_for_business_key(
                module_code=task_plan.module_code,
                source_row_id=record.source_row_id,
                task_type=task_plan.task_type,
            )
            if existing_success is None:
                return None
            if not self._visit_run_has_strict_closure(existing_success):
                return None
            return ExecutionResult(
                run_status="precheck_failed",
                error_message="该钉钉文档行已成功创建并闭环过回访工单，禁止重复执行",
                result_payload={
                    "source_row_id": record.source_row_id,
                    "existing_task_run_id": str(existing_success.id),
                    "existing_final_link": existing_success.final_link,
                },
                final_link=existing_success.final_link,
                executor_version="phase9-visit-real-v1",
            )

        # 超半年主动回访（proactive）：同一产品在半年内不重复创建回访工单
        if task_plan.module_code == "proactive" and task_plan.task_type == "proactive_visit_close":
            data = record.normalized_data or {}
            product_info_id = str(data.get("product_info_id") or "").strip()
            product_link = str(data.get("product_link") or "").strip()
            if not product_info_id and not product_link:
                return None

            from sqlalchemy import select
            from models.task_run import TaskRun
            from models.task_plan import TaskPlan as _TaskPlan
            from models.normalized_record import NormalizedRecord as _NR

            six_months_ago = datetime.now(timezone.utc) - __import__("datetime").timedelta(days=180)

            # 优先按 product_info_id 去重；否则按 product_link 完整匹配
            if product_info_id:
                cond_expr = _NR.normalized_data["product_info_id"].astext == product_info_id
            else:
                cond_expr = _NR.normalized_data["product_link"].astext == product_link

            stmt = (
                select(TaskRun)
                .join(_TaskPlan, _TaskPlan.id == TaskRun.task_plan_id)
                .join(_NR, _NR.id == _TaskPlan.normalized_record_id)
                .where(
                    _TaskPlan.module_code == "proactive",
                    _TaskPlan.task_type == "proactive_visit_close",
                    TaskRun.run_status == "success",
                    TaskRun.manual_required.is_(False),
                    TaskRun.run_time >= six_months_ago,
                    cond_expr,
                )
                .order_by(TaskRun.run_time.desc())
                .limit(1)
            )
            existing = self.db.scalar(stmt)
            if existing is None:
                return None
            if not self._visit_run_has_strict_closure(existing):
                return None
            return ExecutionResult(
                run_status="precheck_failed",
                error_message="该产品在近6个月内已闭环过主动回访，禁止重复创建",
                result_payload={
                    "product_info_id": product_info_id or None,
                    "product_link": product_link or None,
                    "existing_task_run_id": str(existing.id),
                    "existing_final_link": existing.final_link,
                },
                final_link=existing.final_link,
                executor_version="phase9-proactive-real-v1",
            )

        return None

    @staticmethod
    def _visit_run_has_strict_closure(task_run) -> bool:
        payload = dict(getattr(task_run, "result_payload", {}) or {})
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
        return (
            task_run.run_status == "success"
            and not bool(getattr(task_run, "manual_required", False))
            and payload.get("execution_mode") == "real"
            and postcheck_passed is True
            and closure_confirmed is True
            and delivery_bound_confirmed is True
            and feedback_confirmed is True
        )

    def _select_executor(self, task_plan: TaskPlan):
        executor_cls = EXECUTOR_REGISTRY.get((task_plan.module_code, task_plan.task_type))
        if executor_cls is None:
            raise ValueError("executor 与 module_code / task_type 不匹配")
        return executor_cls()

    def _build_executor_context(self, task_plan: TaskPlan, record: NormalizedRecord) -> ExecutorContext:
        source_config = self.module_config_repo.get_source_config(task_plan.module_code)
        return ExecutorContext(
            task_plan_id=str(task_plan.id),
            module_code=task_plan.module_code,
            task_type=task_plan.task_type,
            plan_status=task_plan.plan_status,
            normalized_record_id=str(record.id),
            source_row_id=record.source_row_id,
            recognition_status=record.recognition_status,
            planned_payload=task_plan.planned_payload,
            normalized_data=record.normalized_data,
            source_url=source_config.source_url if source_config else None,
            source_doc_key=source_config.source_doc_key if source_config else None,
            source_view_key=source_config.source_view_key if source_config else None,
            source_collector_type=source_config.collector_type if source_config else None,
            source_extra_config=dict(source_config.extra_config) if source_config else {},
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
