from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any


@dataclass(slots=True)
class InspectionDeadlineCandidate:
    pts_work_order_id: str
    pts_work_order_link: str | None = None
    customer_name: str | None = None
    service_type: str | None = None
    status_text: str | None = None
    plan_finish_time_raw: str | None = None
    plan_finish_date: date | None = None
    raw_payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ReminderSendResult:
    success: bool
    message_channel: str
    sender_type: str
    payload: dict[str, Any] = field(default_factory=dict)
    error_message: str | None = None


@dataclass(slots=True)
class ReminderRunSummary:
    trigger: str
    scanned_count: int = 0
    eligible_count: int = 0
    sent_count: int = 0
    failed_count: int = 0
    duplicate_count: int = 0
    skipped_count: int = 0
