from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class DeadlineReminder(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "deadline_reminders"
    __table_args__ = (
        UniqueConstraint(
            "pts_work_order_id",
            "remind_type",
            name="uq_deadline_reminders_pts_work_order_remind_type",
        ),
    )

    module_code: Mapped[str] = mapped_column(String(32), nullable=False, default="inspection", server_default="inspection")
    pts_work_order_id: Mapped[str] = mapped_column(String(128), nullable=False)
    pts_work_order_link: Mapped[str | None] = mapped_column(Text)
    customer_name: Mapped[str | None] = mapped_column(String(255))
    service_type: Mapped[str | None] = mapped_column(String(64))
    status_text: Mapped[str | None] = mapped_column(String(64))
    remind_type: Mapped[str] = mapped_column(String(32), nullable=False)
    deadline_date: Mapped[date] = mapped_column(Date, nullable=False)
    plan_finish_time_raw: Mapped[str | None] = mapped_column(String(128))
    send_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", server_default="pending")
    message_channel: Mapped[str | None] = mapped_column(String(32))
    sender_type: Mapped[str | None] = mapped_column(String(32))
    error_message: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw_payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    send_payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
