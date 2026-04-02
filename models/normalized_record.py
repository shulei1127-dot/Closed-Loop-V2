import uuid

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class NormalizedRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "normalized_records"

    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("source_snapshots.id", ondelete="CASCADE"),
        nullable=False,
    )
    module_code: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source_row_id: Mapped[str] = mapped_column(String(128), nullable=False)
    customer_name: Mapped[str | None] = mapped_column(String(255))
    normalized_data: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    field_mapping: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    field_confidence: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    field_evidence: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    field_samples: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    unresolved_fields: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    recognition_status: Mapped[str] = mapped_column(String(32), nullable=False)

    snapshot = relationship("SourceSnapshot", back_populates="normalized_records")
    task_plans = relationship("TaskPlan", back_populates="normalized_record", cascade="all, delete-orphan")
