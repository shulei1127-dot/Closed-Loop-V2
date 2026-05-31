from schemas.sync import TaskPlanDTO

_TAG_MARK_STATUSES = {"不用回访", "售后断联"}


class ProactivePlanner:
    def plan(self, normalized_records: list[dict]) -> list[TaskPlanDTO]:
        task_plans: list[TaskPlanDTO] = []
        for item in normalized_records:
            data = item["normalized_data"]
            liaison_status = data.get("liaison_status")

            # 标签标记路径：不用回访 / 售后断联 → 打 PTS 项目标签
            if liaison_status in _TAG_MARK_STATUSES and not data.get("visit_link"):
                eligible = (
                    bool(data.get("customer_name"))
                    and item.get("recognition_status") != "failed"
                )
                task_plans.append(
                    TaskPlanDTO(
                        module_code="proactive",
                        source_row_id=item["source_row_id"],
                        task_type="proactive_tag_mark",
                        eligibility=eligible,
                        skip_reason=(
                            None
                            if eligible
                            else "不满足 customer_name 存在"
                        ),
                        plan_status="planned" if eligible else "skipped",
                        planned_payload={
                            "customer_name": data.get("customer_name"),
                            "product_info_id": data.get("product_info_id"),
                            "product_link": data.get("product_link"),
                            "liaison_status": liaison_status,
                            "tag_name": liaison_status,
                        },
                    )
                )
                continue

            # 回访闭环路径：已建联 → 创建回访工单 + 填写反馈 + 完成
            has_product = bool(str((data.get("product_link") or data.get("product_info_id") or "")).strip())
            eligible = (
                bool(data.get("customer_name"))
                and item.get("recognition_status") != "failed"
                and liaison_status == "已建联"
                and not data.get("visit_link")
                and bool(str(data.get("visit_owner") or "").strip())
                and bool(str(data.get("feedback_note") or "").strip())
                and has_product
            )
            task_plans.append(
                TaskPlanDTO(
                    module_code="proactive",
                    source_row_id=item["source_row_id"],
                    task_type="proactive_visit_close",
                    eligibility=eligible,
                    skip_reason=(
                        None
                        if eligible
                        else "不满足 customer_name 存在、liaison_status=已建联、visit_owner 存在、feedback_note 非空、visit_link 为空，且需提供 product_link 或 product_info_id"
                    ),
                    plan_status="planned" if eligible else "skipped",
                    planned_payload={
                        "customer_name": data.get("customer_name"),
                        "product_info_id": data.get("product_info_id"),
                        "product_link": data.get("product_link"),
                        "visit_owner": data.get("visit_owner"),
                        "visit_type": "客户满意度调研",
                        "feedback_note": data.get("feedback_note"),
                    },
                )
            )
        return task_plans
