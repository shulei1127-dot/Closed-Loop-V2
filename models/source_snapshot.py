from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin


class SourceSnapshot(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "source_snapshots"

    module_code: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    source_doc_key: Mapped[str] = mapped_column(String(128), nullable=False)
    source_view_key: Mapped[str | None] = mapped_column(String(128))
    sync_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    data_source: Mapped[str] = mapped_column(String(32), nullable=False)
    sync_status: Mapped[str] = mapped_column(String(32), nullable=False)
    sync_error: Mapped[str | None] = mapped_column(Text)
    raw_columns: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    raw_rows: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    raw_meta: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    normalized_records = relationship("NormalizedRecord", back_populates="snapshot", cascade="all, delete-orphan")

