import uuid

from sqlalchemy import select

from models.normalized_record import NormalizedRecord
from models.task_plan import TaskPlan
from repositories.base import BaseRepository
from schemas.sync import TaskPlanDTO


class TaskPlanRepository(BaseRepository):
    def create_from_dtos(
        self,
        dtos: list[TaskPlanDTO],
        record_map: dict[str, object],
    ) -> list[TaskPlan]:
        items: list[TaskPlan] = []
        for dto in dtos:
            normalized_record = record_map[dto.source_row_id]
            task_plan = TaskPlan(
                module_code=dto.module_code,
                normalized_record_id=normalized_record.id,
                task_type=dto.task_type,
                eligibility=dto.eligibility,
                skip_reason=dto.skip_reason,
                planner_version=dto.planner_version,
                plan_status=dto.plan_status,
                planned_payload=dto.planned_payload,
            )
            self.db.add(task_plan)
            items.append(task_plan)
        self.db.flush()
        return items

    def list_by_filters(self, module_code: str | None, status: str | None) -> list[TaskPlan]:
        statement = select(TaskPlan).order_by(TaskPlan.created_at.desc())
        if module_code:
            statement = statement.where(TaskPlan.module_code == module_code)
        if status:
            statement = statement.where(TaskPlan.plan_status == status)
        return list(self.db.scalars(statement).all())

    def list_latest_by_business_key(self, module_code: str | None, status: str | None) -> list[TaskPlan]:
        statement = (
            select(TaskPlan, NormalizedRecord.source_row_id)
            .join(NormalizedRecord, NormalizedRecord.id == TaskPlan.normalized_record_id)
            .order_by(TaskPlan.created_at.desc())
        )
        if module_code:
            statement = statement.where(TaskPlan.module_code == module_code)

        grouped: dict[tuple[str, str, str], TaskPlan] = {}
        ordered: list[TaskPlan] = []
        for task, source_row_id in self.db.execute(statement).all():
            key = (
                task.module_code,
                str(source_row_id or task.normalized_record_id),
                task.task_type,
            )
            if key in grouped:
                continue
            if status and task.plan_status != status:
                grouped[key] = task
                continue
            grouped[key] = task
            ordered.append(task)
        return ordered

    def get_by_id(self, task_id: uuid.UUID) -> TaskPlan | None:
        statement = select(TaskPlan).where(TaskPlan.id == task_id)
        return self.db.scalar(statement)
