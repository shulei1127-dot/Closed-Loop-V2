from sqlalchemy import Boolean, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class ModuleConfig(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "module_configs"

    module_code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    module_name: Mapped[str] = mapped_column(String(64), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    source_doc_key: Mapped[str] = mapped_column(String(128), nullable=False)
    source_view_key: Mapped[str | None] = mapped_column(String(128))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    collector_type: Mapped[str] = mapped_column(String(32), default="fixture", nullable=False)
    sync_cron: Mapped[str | None] = mapped_column(String(64))
    extra_config: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
