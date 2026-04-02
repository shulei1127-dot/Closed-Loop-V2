import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin


class TaskRun(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "task_runs"

    task_plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("task_plans.id", ondelete="CASCADE"),
        nullable=False,
    )
    run_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    run_status: Mapped[str] = mapped_column(String(32), nullable=False)
    manual_required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    result_payload: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    final_link: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    executor_version: Mapped[str | None] = mapped_column(String(32))

    task_plan = relationship("TaskPlan", back_populates="task_runs")
