import uuid

from sqlalchemy import desc, select

from models.normalized_record import NormalizedRecord
from models.task_plan import TaskPlan
from models.task_run import TaskRun
from repositories.base import BaseRepository
from services.executors.schemas import ExecutionResult


class TaskRunRepository(BaseRepository):
    def create_from_result(
        self,
        task_plan_id: uuid.UUID,
        result: ExecutionResult,
        *,
        metadata: dict | None = None,
    ) -> TaskRun:
        payload = dict(result.result_payload or {})
        ops_payload = dict(payload.get("_ops", {}))
        if metadata:
            ops_payload.update(metadata)
        ops_payload["retryable"] = result.retryable
        if ops_payload:
            payload["_ops"] = ops_payload
        task_run = TaskRun(
            task_plan_id=task_plan_id,
            run_status=result.run_status,
            manual_required=result.manual_required,
            result_payload=payload,
            final_link=result.final_link,
            error_message=result.error_message,
            executor_version=result.executor_version,
        )
        self.db.add(task_run)
        self.db.flush()
        return task_run

    def list_by_task_plan(self, task_plan_id: str) -> list[TaskRun]:
        statement = (
            select(TaskRun)
            .where(TaskRun.task_plan_id == task_plan_id)
            .order_by(TaskRun.run_time.desc())
        )
        return list(self.db.scalars(statement).all())

    def get_by_id(self, run_id: uuid.UUID) -> TaskRun | None:
        statement = select(TaskRun).where(TaskRun.id == run_id)
        return self.db.scalar(statement)

    def latest_for_task_plan(self, task_plan_id: uuid.UUID) -> TaskRun | None:
        statement = (
            select(TaskRun)
            .where(TaskRun.task_plan_id == task_plan_id)
            .order_by(TaskRun.run_time.desc())
            .limit(1)
        )
        return self.db.scalar(statement)

    def list_recent(self, limit: int = 50) -> list[TaskRun]:
        statement = select(TaskRun).order_by(desc(TaskRun.run_time)).limit(limit)
        return list(self.db.scalars(statement).all())

    def list_by_task_plan_ids(self, task_plan_ids: list[uuid.UUID]) -> list[TaskRun]:
        if not task_plan_ids:
            return []
        statement = (
            select(TaskRun)
            .where(TaskRun.task_plan_id.in_(task_plan_ids))
            .order_by(desc(TaskRun.run_time))
        )
        return list(self.db.scalars(statement).all())

    def latest_success_for_business_key(
        self,
        *,
        module_code: str,
        source_row_id: str,
        task_type: str,
    ) -> TaskRun | None:
        statement = (
            select(TaskRun)
            .join(TaskPlan, TaskPlan.id == TaskRun.task_plan_id)
            .join(NormalizedRecord, NormalizedRecord.id == TaskPlan.normalized_record_id)
            .where(
                TaskPlan.module_code == module_code,
                TaskPlan.task_type == task_type,
                NormalizedRecord.source_row_id == source_row_id,
                TaskRun.run_status == "success",
                TaskRun.manual_required.is_(False),
            )
            .order_by(desc(TaskRun.run_time))
            .limit(1)
        )
        return self.db.scalar(statement)

    def list_successful_business_keys(self, module_code: str | None = None) -> set[tuple[str, str, str]]:
        statement = (
            select(TaskPlan.module_code, NormalizedRecord.source_row_id, TaskPlan.task_type)
            .select_from(TaskRun)
            .join(TaskPlan, TaskPlan.id == TaskRun.task_plan_id)
            .join(NormalizedRecord, NormalizedRecord.id == TaskPlan.normalized_record_id)
            .where(
                TaskRun.run_status == "success",
                TaskRun.manual_required.is_(False),
            )
        )
        if module_code:
            statement = statement.where(TaskPlan.module_code == module_code)
        return {
            (str(current_module), str(source_row_id), str(task_type))
            for current_module, source_row_id, task_type in self.db.execute(statement).all()
        }
