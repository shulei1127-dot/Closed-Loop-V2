from __future__ import annotations

from typing import Protocol

from services.reminders.schemas import InspectionDeadlineCandidate, ReminderSendResult


class ReminderSender(Protocol):
    sender_type: str
    message_channel: str

    async def send(self, *, item: InspectionDeadlineCandidate, remind_type: str, message_text: str) -> ReminderSendResult:
        ...
