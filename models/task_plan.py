import uuid

from sqlalchemy import Boolean, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class TaskPlan(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "task_plans"

    module_code: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    normalized_record_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("normalized_records.id", ondelete="CASCADE"),
        nullable=False,
    )
    task_type: Mapped[str] = mapped_column(String(64), nullable=False)
    eligibility: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    skip_reason: Mapped[str | None] = mapped_column(Text)
    planner_version: Mapped[str] = mapped_column(String(32), nullable=False)
    plan_status: Mapped[str] = mapped_column(String(32), default="planned", nullable=False)
    planned_payload: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    normalized_record = relationship("NormalizedRecord", back_populates="task_plans")
    task_runs = relationship("TaskRun", back_populates="task_plan", cascade="all, delete-orphan")
