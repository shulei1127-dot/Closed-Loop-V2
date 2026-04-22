from __future__ import annotations

import logging

from services.reminders.schemas import InspectionDeadlineCandidate, ReminderSendResult


logger = logging.getLogger(__name__)


class LogInspectionDeadlineSender:
    sender_type = "log_fallback"
    message_channel = "log_fallback"

    async def send(self, *, item: InspectionDeadlineCandidate, remind_type: str, message_text: str) -> ReminderSendResult:
        logger.warning(
            "inspection deadline reminder fallback sender used: remind_type=%s pts_work_order_id=%s deadline=%s message=%s",
            remind_type,
            item.pts_work_order_id,
            item.plan_finish_date,
            message_text,
        )
        return ReminderSendResult(
            success=True,
            message_channel=self.message_channel,
            sender_type=self.sender_type,
            payload={
                "message_text": message_text,
                "pts_work_order_id": item.pts_work_order_id,
                "remind_type": remind_type,
            },
        )
