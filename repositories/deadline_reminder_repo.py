from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import desc, select

from models.deadline_reminder import DeadlineReminder
from repositories.base import BaseRepository
from services.reminders.schemas import InspectionDeadlineCandidate


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class DeadlineReminderRepository(BaseRepository):
    def get_by_business_key(self, *, pts_work_order_id: str, remind_type: str) -> DeadlineReminder | None:
        statement = select(DeadlineReminder).where(
            DeadlineReminder.pts_work_order_id == pts_work_order_id,
            DeadlineReminder.remind_type == remind_type,
        )
        return self.db.scalar(statement)

    def create_pending(
        self,
        *,
        reminder_item: InspectionDeadlineCandidate,
        remind_type: str,
    ) -> DeadlineReminder:
        reminder = DeadlineReminder(
            module_code="inspection",
            pts_work_order_id=reminder_item.pts_work_order_id,
            pts_work_order_link=reminder_item.pts_work_order_link,
            customer_name=reminder_item.customer_name,
            service_type=reminder_item.service_type,
            status_text=reminder_item.status_text,
            remind_type=remind_type,
            deadline_date=reminder_item.plan_finish_date,
            plan_finish_time_raw=reminder_item.plan_finish_time_raw,
            send_status="pending",
            raw_payload=dict(reminder_item.raw_payload or {}),
            send_payload={},
        )
        self.db.add(reminder)
        self.db.flush()
        return reminder

    def mark_sent(
        self,
        reminder: DeadlineReminder,
        *,
        message_channel: str,
        sender_type: str,
        send_payload: dict,
    ) -> DeadlineReminder:
        reminder.send_status = "sent"
        reminder.message_channel = message_channel
        reminder.sender_type = sender_type
        reminder.send_payload = dict(send_payload or {})
        reminder.error_message = None
        reminder.sent_at = _utc_now()
        self.db.add(reminder)
        self.db.flush()
        return reminder

    def mark_failed(
        self,
        reminder: DeadlineReminder,
        *,
        message_channel: str | None,
        sender_type: str | None,
        send_payload: dict,
        error_message: str | None,
    ) -> DeadlineReminder:
        reminder.send_status = "failed"
        reminder.message_channel = message_channel
        reminder.sender_type = sender_type
        reminder.send_payload = dict(send_payload or {})
        reminder.error_message = error_message
        self.db.add(reminder)
        self.db.flush()
        return reminder

    def list_recent(
        self,
        *,
        limit: int = 50,
        send_status: str | None = None,
        remind_type: str | None = None,
    ) -> list[DeadlineReminder]:
        statement = select(DeadlineReminder).order_by(desc(DeadlineReminder.created_at))
        if send_status:
            statement = statement.where(DeadlineReminder.send_status == send_status)
        if remind_type:
            statement = statement.where(DeadlineReminder.remind_type == remind_type)
        statement = statement.limit(limit)
        return list(self.db.scalars(statement).all())
