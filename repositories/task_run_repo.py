import uuid

from sqlalchemy import desc, select

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
