from schemas.sync import TaskPlanDTO


class VisitPlanner:
    def plan(self, normalized_records: list[dict]) -> list[TaskPlanDTO]:
        task_plans: list[TaskPlanDTO] = []
        for item in normalized_records:
            data = item["normalized_data"]
            eligible = (
                bool(data.get("customer_name"))
                and item.get("recognition_status") != "failed"
                and bool(data.get("delivery_id"))
                and data.get("visit_owner") == "舒磊"
                and data.get("visit_status") == "已回访"
                and not data.get("visit_link")
            )
            skip_reason = None if eligible else "不满足 customer_name 存在、delivery_id 存在、visit_owner=舒磊、visit_status=已回访、visit_link 为空"
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
                    },
                )
            )
        return task_plans
