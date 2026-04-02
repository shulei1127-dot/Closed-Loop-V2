from services.planners.inspection_planner import InspectionPlanner
from services.planners.proactive_planner import ProactivePlanner
from services.planners.visit_planner import VisitPlanner


def test_visit_planner_marks_only_matching_rows_as_planned() -> None:
    planner = VisitPlanner()
    result = planner.plan(
        [
            {
                "source_row_id": "visit-001",
                "recognition_status": "full",
                "normalized_data": {
                    "customer_name": "上海客户",
                    "delivery_id": "DEL-001",
                    "visit_owner": "舒磊",
                    "visit_status": "已回访",
                    "visit_link": None,
                },
            },
            {
                "source_row_id": "visit-002",
                "recognition_status": "full",
                "normalized_data": {
                    "customer_name": "杭州客户",
                    "delivery_id": None,
                    "visit_owner": "其他人",
                    "visit_status": "待回访",
                    "visit_link": None,
                },
            },
        ]
    )
    assert len(result) == 2
    assert result[0].eligibility is True
    assert result[0].plan_status == "planned"
    assert result[0].task_type == "visit_close"
    assert result[1].eligibility is False
    assert result[1].plan_status == "skipped"
    assert "customer_name" in (result[1].skip_reason or "")


def test_inspection_planner_uses_boolean_flag() -> None:
    planner = InspectionPlanner()
    result = planner.plan(
        [
            {
                "source_row_id": "inspection-001",
                "recognition_status": "full",
                "normalized_data": {
                    "customer_name": "南京客户",
                    "inspection_done": True,
                    "work_order_link": "https://wo.example.com/1",
                },
            },
            {
                "source_row_id": "inspection-002",
                "recognition_status": "full",
                "normalized_data": {
                    "customer_name": "苏州客户",
                    "inspection_done": False,
                    "work_order_link": "https://wo.example.com/2",
                },
            },
        ]
    )
    assert result[0].eligibility is True
    assert result[0].plan_status == "planned"
    assert result[1].eligibility is False
    assert result[1].plan_status == "skipped"
    assert result[1].skip_reason == "不满足 customer_name 存在、inspection_done=true、且工单链接或工单ID存在"


def test_proactive_planner_requires_liaison_and_empty_visit_link() -> None:
    planner = ProactivePlanner()
    result = planner.plan(
        [
            {
                "source_row_id": "proactive-001",
                "recognition_status": "full",
                "normalized_data": {
                    "customer_name": "北京客户",
                    "liaison_status": "已建联",
                    "visit_link": None,
                },
            },
            {
                "source_row_id": "proactive-002",
                "recognition_status": "full",
                "normalized_data": {
                    "customer_name": "深圳客户",
                    "liaison_status": "已建联",
                    "visit_link": "x",
                },
            },
        ]
    )
    assert result[0].task_type == "proactive_visit_close"
    assert result[0].eligibility is True
    assert result[1].eligibility is False
    assert result[1].plan_status == "skipped"
    assert result[1].skip_reason == "不满足 customer_name 存在、liaison_status=已建联、且 visit_link 为空"
