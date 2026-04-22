import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import Base, CreatedAtMixin


class TaskBatchJob(CreatedAtMixin, Base):
    __tablename__ = "task_batch_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("task_batches.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    task_plan_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), default="queued", nullable=False)
    terminal_status: Mapped[str | None] = mapped_column(String(32))
    run_status: Mapped[str | None] = mapped_column(String(32))
    manual_required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    task_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    batch = relationship("TaskBatch", back_populates="jobs")
