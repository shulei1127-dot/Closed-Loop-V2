from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field


class DeadlineReminderItem(BaseModel):
    reminder_id: str
    module_code: str
    pts_work_order_id: str
    pts_work_order_link: str | None = None
    customer_name: str | None = None
    service_type: str | None = None
    status_text: str | None = None
    remind_type: str
    deadline_date: date
    plan_finish_time_raw: str | None = None
    send_status: str
    message_channel: str | None = None
    sender_type: str | None = None
    error_message: str | None = None
    raw_payload: dict[str, Any] = Field(default_factory=dict)
    send_payload: dict[str, Any] = Field(default_factory=dict)
    sent_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class DeadlineReminderListResponse(BaseModel):
    ok: bool = True
    items: list[DeadlineReminderItem] = Field(default_factory=list)


class DeadlineReminderRunSummary(BaseModel):
    trigger: str
    scanned_count: int = 0
    eligible_count: int = 0
    sent_count: int = 0
    failed_count: int = 0
    duplicate_count: int = 0
    skipped_count: int = 0


class DeadlineReminderRunResponse(BaseModel):
    ok: bool = True
    summary: DeadlineReminderRunSummary
