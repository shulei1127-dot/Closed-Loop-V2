from schemas.sync import TaskPlanDTO


class InspectionPlanner:
    def plan(self, normalized_records: list[dict]) -> list[TaskPlanDTO]:
        task_plans: list[TaskPlanDTO] = []
        for item in normalized_records:
            data = item["normalized_data"]
            service_type = str(data.get("service_type") or "")
            executor_name = str(data.get("executor_name") or "")
            work_order_closed = data.get("work_order_closed") is True
            eligible = (
                bool(data.get("customer_name"))
                and item.get("recognition_status") != "failed"
                and "巡检" in service_type
                and executor_name == "舒磊"
                and data.get("inspection_done") is True
                and not work_order_closed
                and bool(data.get("work_order_link") or data.get("work_order_id"))
            )
            task_plans.append(
                TaskPlanDTO(
                    module_code="inspection",
                    source_row_id=item["source_row_id"],
                    task_type="inspection_close",
                    eligibility=eligible,
                    skip_reason=None
                    if eligible
                    else "不满足 customer_name 存在、service_type 含巡检、executor_name=舒磊、inspection_done=true、work_order_closed!=true、且工单链接或工单ID存在",
                    plan_status="planned" if eligible else "skipped",
                    planned_payload={
                        "inspection_month": data.get("inspection_month"),
                        "customer_name": data.get("customer_name"),
                        "service_type": data.get("service_type"),
                        "executor_name": data.get("executor_name"),
                        "work_order_stage": data.get("work_order_stage"),
                        "work_order_closed": data.get("work_order_closed"),
                        "work_order_id": data.get("work_order_id"),
                        "work_order_link": data.get("work_order_link"),
                        "report_match_name": data.get("report_match_name"),
                        "report_lookup_customer": data.get("customer_name"),
                        "report_status_hint": "pending_report_match",
                    },
                )
            )
        return task_plans
