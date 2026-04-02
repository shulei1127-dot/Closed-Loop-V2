from schemas.sync import TaskPlanDTO


class ProactivePlanner:
    def plan(self, normalized_records: list[dict]) -> list[TaskPlanDTO]:
        task_plans: list[TaskPlanDTO] = []
        for item in normalized_records:
            data = item["normalized_data"]
            eligible = (
                bool(data.get("customer_name"))
                and item.get("recognition_status") != "failed"
                and data.get("liaison_status") == "已建联"
                and not data.get("visit_link")
            )
            task_plans.append(
                TaskPlanDTO(
                    module_code="proactive",
                    source_row_id=item["source_row_id"],
                    task_type="proactive_visit_close",
                    eligibility=eligible,
                    skip_reason=None if eligible else "不满足 customer_name 存在、liaison_status=已建联、且 visit_link 为空",
                    plan_status="planned" if eligible else "skipped",
                    planned_payload={
                        "customer_name": data.get("customer_name"),
                        "product_info_id": data.get("product_info_id"),
                        "product_link": data.get("product_link"),
                    },
                )
            )
        return task_plans
