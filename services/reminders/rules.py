from __future__ import annotations

from datetime import date, datetime
import json
from zoneinfo import ZoneInfo

from services.reminders.schemas import InspectionDeadlineCandidate


LOCAL_TZ = ZoneInfo("Asia/Shanghai")
ELIGIBLE_SERVICE_TYPE = "巡检工单"
TERMINAL_STATUSES = {"已完成", "已关闭", "已取消"}


def normalize_status_text(raw_value) -> str | None:
    if raw_value is None:
        return None
    if isinstance(raw_value, dict):
        for key in ("status_text", "status_name", "name", "label", "value", "text"):
            candidate = normalize_status_text(raw_value.get(key))
            if candidate:
                return candidate
        return None
    if isinstance(raw_value, list):
        for item in raw_value:
            candidate = normalize_status_text(item)
            if candidate:
                return candidate
        return None
    text = str(raw_value).strip()
    if not text:
        return None
    if text.startswith("{") or text.startswith("["):
        try:
            return normalize_status_text(json.loads(text))
        except (TypeError, ValueError, json.JSONDecodeError):
            return text
    return text


def parse_local_date(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=LOCAL_TZ)
        else:
            dt = dt.astimezone(LOCAL_TZ)
        return dt.date()
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if abs(timestamp) > 1_000_000_000_000:
            timestamp = timestamp / 1000.0
        return datetime.fromtimestamp(timestamp, tz=LOCAL_TZ).date()
    if isinstance(value, dict):
        for key in ("timestamp", "ts", "value", "3"):
            if key in value:
                return parse_local_date(value.get(key))
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return parse_local_date(int(text))
    if text.startswith("{") or text.startswith("["):
        try:
            return parse_local_date(json.loads(text))
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
    normalized_text = text.replace("Z", "+00:00").replace("/", "-")
    for fmt in (
        "%Y-%m-%d",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
    ):
        try:
            dt = datetime.strptime(normalized_text, fmt)
            return dt.replace(tzinfo=LOCAL_TZ).date()
        except ValueError:
            continue
    try:
        dt = datetime.fromisoformat(normalized_text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=LOCAL_TZ)
    else:
        dt = dt.astimezone(LOCAL_TZ)
    return dt.date()


def resolve_remind_type(*, deadline_date: date | None, today: date | None = None) -> str | None:
    if deadline_date is None:
        return None
    current_day = today or datetime.now(LOCAL_TZ).date()
    delta_days = (deadline_date - current_day).days
    if delta_days == 3:
        return "due_in_3d"
    if delta_days == 1:
        return "due_in_1d"
    if delta_days < 0:
        return "overdue"
    return None


def is_eligible_candidate(item: InspectionDeadlineCandidate) -> bool:
    if item.service_type != ELIGIBLE_SERVICE_TYPE:
        return False
    if item.plan_finish_date is None:
        return False
    if item.status_text in TERMINAL_STATUSES:
        return False
    return True
