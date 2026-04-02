from __future__ import annotations

from typing import Any

from services.executors.schemas import ExecutorContext


VISIT_TYPE_MAP = {
    "交付回访": "delivery_followup",
    "售后回访": "after_sales_followup",
    "客户满意度调研": "customer_satisfaction",
    "交付满意度评价": "delivery_satisfaction",
}


class VisitActionBuilder:
    def build(self, context: ExecutorContext) -> tuple[list[dict[str, Any]], str | None]:
        data = context.normalized_data
        visit_type = data.get("visit_type")
        mapped_type = VISIT_TYPE_MAP.get(str(visit_type)) if visit_type else None
        if mapped_type is None:
            return [], f"visit_type `{visit_type}` 暂不支持自动执行"

        actions = [
            {
                "action": "open_pts_delivery_link",
                "target": data.get("pts_link"),
            },
            {
                "action": "create_visit_work_order",
                "work_order_type": mapped_type,
                "delivery_id": data.get("delivery_id"),
            },
            {
                "action": "assign_owner",
                "owner": "舒磊",
            },
            {
                "action": "mark_visit_target",
                "customer_name": data.get("customer_name"),
            },
            {
                "action": "fill_feedback",
                "satisfaction": data.get("satisfaction"),
                "feedback_note": data.get("feedback_note"),
            },
            {
                "action": "complete_visit",
                "visit_contact": data.get("visit_contact"),
            },
        ]
        return actions, None
