from __future__ import annotations

from typing import Any

from schemas.sync import TaskPlanDTO


# Criteria for creating a review_audit task plan:
#   delivery_stage must include "转售后审核"
#   stage_status must include "等待审核是否转售后"
_REQUIRED_DELIVERY_STAGE_KEYWORD = "转售后审核"
_REQUIRED_STAGE_STATUS_KEYWORD = "等待审核是否转售后"


class ReviewPlanner:
    """Planner for review module.

    Creates review_audit tasks for records where the project is in the
    post-sales review stage and awaiting audit decision.
    """

    def plan(self, normalized_records: list[dict]) -> list[TaskPlanDTO]:
        task_plans: list[TaskPlanDTO] = []

        for item in normalized_records:
            data = item["normalized_data"]
            delivery_stage = str(data.get("delivery_stage") or "")
            stage_status = str(data.get("stage_status") or "")

            # Only plan tasks for projects in the review stage awaiting audit
            if _REQUIRED_DELIVERY_STAGE_KEYWORD not in delivery_stage:
                continue
            if _REQUIRED_STAGE_STATUS_KEYWORD not in stage_status:
                continue

            project_id = str(data.get("project_id") or "").strip()
            eligible = bool(project_id) and item.get("recognition_status") != "failed"

            task_plans.append(
                TaskPlanDTO(
                    module_code="review",
                    source_row_id=item.get("source_row_id", ""),
                    task_type="review_audit",
                    eligibility=eligible,
                    skip_reason=None if eligible else "project_id 缺失或识别失败",
                    plan_status="planned" if eligible else "skipped",
                    planned_payload={
                        "project_id": project_id,
                        "project_name": data.get("project_name"),
                        "customer_name": data.get("customer_name"),
                        "after_sales_leader": data.get("after_sales_leader"),
                        "assigner_name": data.get("assigner_name"),
                        "person_in_charge_name": data.get("person_in_charge_name"),
                    },
                )
            )

        return task_plans
