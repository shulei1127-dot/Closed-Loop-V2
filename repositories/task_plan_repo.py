import uuid
import logging

from sqlalchemy import String, cast, func, select
from sqlalchemy.orm import aliased

from core.config import get_settings
from models.normalized_record import NormalizedRecord
from models.task_plan import TaskPlan
from repositories.base import BaseRepository
from schemas.sync import TaskPlanDTO

logger = logging.getLogger(__name__)


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
        settings = get_settings()
        if settings.task_plan_latest_by_sql_enabled:
            logger.info(
                "task_plan latest-by-business-key strategy=sql module_code=%s status=%s",
                module_code,
                status,
            )
            return self._list_latest_by_business_key_sql(module_code=module_code, status=status)
        logger.info(
            "task_plan latest-by-business-key strategy=python module_code=%s status=%s",
            module_code,
            status,
        )
        return self._list_latest_by_business_key_python(module_code=module_code, status=status)

    def _list_latest_by_business_key_python(self, module_code: str | None, status: str | None) -> list[TaskPlan]:
        statement = (
            select(TaskPlan, NormalizedRecord.source_row_id)
            .join(NormalizedRecord, NormalizedRecord.id == TaskPlan.normalized_record_id)
            .order_by(TaskPlan.created_at.desc(), TaskPlan.id.desc())
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

    def _list_latest_by_business_key_sql(self, module_code: str | None, status: str | None) -> list[TaskPlan]:
        business_row_expr = func.coalesce(
            NormalizedRecord.source_row_id,
            cast(TaskPlan.normalized_record_id, String),
        )
        ranked_statement = (
            select(
                TaskPlan.id.label("task_plan_id"),
                TaskPlan.plan_status.label("plan_status"),
                func.row_number()
                .over(
                    partition_by=(TaskPlan.module_code, business_row_expr, TaskPlan.task_type),
                    order_by=(TaskPlan.created_at.desc(), TaskPlan.id.desc()),
                )
                .label("rn"),
            )
            .join(NormalizedRecord, NormalizedRecord.id == TaskPlan.normalized_record_id)
        )
        if module_code:
            ranked_statement = ranked_statement.where(TaskPlan.module_code == module_code)
        ranked_subquery = ranked_statement.subquery("ranked_task_plans")

        latest_ids_statement = select(ranked_subquery.c.task_plan_id).where(ranked_subquery.c.rn == 1)
        if status:
            latest_ids_statement = latest_ids_statement.where(ranked_subquery.c.plan_status == status)

        latest_task_plan = aliased(TaskPlan)
        statement = (
            select(latest_task_plan)
            .where(latest_task_plan.id.in_(latest_ids_statement))
            .order_by(latest_task_plan.created_at.desc(), latest_task_plan.id.desc())
        )
        return list(self.db.scalars(statement).all())

    def get_by_id(self, task_id: uuid.UUID) -> TaskPlan | None:
        statement = select(TaskPlan).where(TaskPlan.id == task_id)
        return self.db.scalar(statement)
