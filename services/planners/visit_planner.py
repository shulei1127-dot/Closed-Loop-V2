from __future__ import annotations

import datetime as dt
import re

from schemas.sync import TaskPlanDTO


class VisitPlanner:
    def plan(self, normalized_records: list[dict]) -> list[TaskPlanDTO]:
        task_plans: list[TaskPlanDTO] = []
        for item in normalized_records:
            data = item["normalized_data"]
            due_reached = _visit_due_reached(
                data.get("due_date_primary"),
                data.get("due_date_secondary"),
            )
            eligible = (
                bool(data.get("customer_name"))
                and item.get("recognition_status") != "failed"
                and bool(data.get("visit_owner"))
                and data.get("visit_status") == "已回访"
                and not data.get("visit_link")
                and due_reached
                and bool(data.get("delivery_id") or data.get("pts_link"))
            )
            skip_reason = (
                None
                if eligible
                else "不满足 customer_name 存在、visit_owner 存在、visit_status=已回访、visit_link 为空、时间已满足、且存在交付信息"
            )
            task_plans.append(
                TaskPlanDTO(
                    module_code="visit",
                    source_row_id=item["source_row_id"],
                    task_type="visit_close",
                    eligibility=eligible,
                    skip_reason=skip_reason,
                    plan_status="planned" if eligible else "skipped",
                    planned_payload={
                        "customer_name": data.get("customer_name"),
                        "delivery_id": data.get("delivery_id"),
                        "pts_link": data.get("pts_link"),
                        "visit_owner": data.get("visit_owner"),
                        "visit_status": data.get("visit_status"),
                        "due_date_primary": data.get("due_date_primary"),
                        "due_date_secondary": data.get("due_date_secondary"),
                        "due_reached": due_reached,
                    },
                )
            )
        return task_plans


def _visit_due_reached(*texts: str | None) -> bool:
    today = dt.date.today()
    for text in texts:
        for candidate in _extract_dates(text):
            if candidate <= today:
                return True
    return False


def _extract_dates(text: str | None) -> list[dt.date]:
    if not text:
        return []
    matches = re.finditer(r"(20\d{2})[-年/.](\d{1,2})[-月/.](\d{1,2})", str(text))
    dates: list[dt.date] = []
    for match in matches:
        try:
            dates.append(
                dt.date(
                    int(match.group(1)),
                    int(match.group(2)),
                    int(match.group(3)),
                )
            )
        except ValueError:
            continue
    return dates
