from schemas.sync import TaskPlanDTO


class InspectionPlanner:
    def plan(self, normalized_records: list[dict]) -> list[TaskPlanDTO]:
        task_plans: list[TaskPlanDTO] = []
        for item in normalized_records:
            data = item["normalized_data"]
            eligible = (
                bool(data.get("customer_name"))
                and item.get("recognition_status") != "failed"
                and data.get("inspection_done") is True
                and bool(data.get("work_order_link") or data.get("work_order_id"))
            )
            task_plans.append(
                TaskPlanDTO(
                    module_code="inspection",
                    source_row_id=item["source_row_id"],
                    task_type="inspection_close",
                    eligibility=eligible,
                    skip_reason=None if eligible else "不满足 customer_name 存在、inspection_done=true、且工单链接或工单ID存在",
                    plan_status="planned" if eligible else "skipped",
                    planned_payload={
                        "customer_name": data.get("customer_name"),
                        "work_order_id": data.get("work_order_id"),
                        "work_order_link": data.get("work_order_link"),
                        "report_match_name": data.get("report_match_name"),
                        "report_lookup_customer": data.get("customer_name"),
                        "report_status_hint": "pending_report_match",
                    },
                )
            )
        return task_plans
