import asyncio

from services.collectors.inspection_collector import InspectionCollector
from services.collectors.proactive_collector import ProactiveCollector
from services.collectors.source_config import ModuleSourceConfig
from services.collectors.visit_collector import VisitCollector
from services.module_registry import default_module_configs
from services.planners.inspection_planner import InspectionPlanner
from services.planners.proactive_planner import ProactivePlanner
from services.planners.visit_planner import VisitPlanner
from services.recognizers.inspection_recognizer import InspectionRecognizer
from services.recognizers.proactive_recognizer import ProactiveRecognizer
from services.recognizers.visit_recognizer import VisitRecognizer


def _default_source_config(module_code: str) -> ModuleSourceConfig:
    defaults = {item["module_code"]: item for item in default_module_configs()}
    return ModuleSourceConfig.from_mapping(defaults[module_code])


def test_visit_real_rows_field_recognition() -> None:
    recognizer = VisitRecognizer()
    raw_columns = ["客户名", "PTS地址", "交付编号", "回访负责人", "回访结果", "闭环链接", "工单类型", "客户联系人", "客户满意度", "客户反馈"]
    raw_rows = [
        {
            "row_id": "visit-alias-001",
            "客户名": "上海别名客户",
            "PTS地址": "https://pts.example.com/alias-001",
            "交付编号": "DEL-ALIAS-001",
            "回访负责人": "舒磊",
            "回访结果": "回访完成",
            "闭环链接": "",
            "工单类型": "交付回访",
            "客户联系人": "王经理",
            "客户满意度": "满意",
            "客户反馈": "字段别名测试",
        }
    ]

    result = recognizer.recognize(raw_columns, raw_rows)

    assert result.field_mapping["customer_name"] == "客户名"
    assert result.field_mapping["visit_owner"] == "回访负责人"
    assert result.normalized_records[0]["normalized_data"]["visit_status"] == "已回访"
    assert result.normalized_records[0]["normalized_data"]["visit_link"] is None
    assert result.recognition_status == "full"


def test_visit_recognizer_normalizes_dingtalk_mention_and_wrapped_links() -> None:
    recognizer = VisitRecognizer()
    raw_columns = ["客户名称", "PTS交付链接", "回访人", "回访状态", "回访链接"]
    raw_rows = [
        {
            "row_id": "visit-mention-001",
            "客户名称": "招商银行股份有限公司信用卡中心",
            "PTS交付链接": '{"url":"https://pts.example.com/project/001#base","text":"https://pts.example.com/project/001#base"}',
            "回访人": '[{"id":"2747525037","name":"舒磊","realName":"舒磊","data-type":"mention"}]',
            "回访状态": "已回访",
            "回访链接": "",
        }
    ]

    result = recognizer.recognize(raw_columns, raw_rows)
    normalized = result.normalized_records[0]["normalized_data"]

    assert normalized["visit_owner"] == "舒磊"
    assert normalized["pts_link"] == "https://pts.example.com/project/001#base"
    assert normalized["visit_link"] is None
    assert normalized["debug_visit_owner_raw"].startswith("[{")
    assert normalized["debug_visit_owner_normalized"] == "舒磊"
    assert normalized["debug_pts_link_raw"].startswith('{"url":"https://pts.example.com/project/001#base"')
    assert normalized["debug_pts_link_normalized"] == "https://pts.example.com/project/001#base"


def test_visit_recognizer_maps_pts_selected_satisfaction() -> None:
    recognizer = VisitRecognizer()
    raw_columns = ["客户名称", "回访人", "回访状态", "回访链接", "PTS选择的满意度", "备注"]
    raw_rows = [
        {
            "row_id": "visit-satisfaction-001",
            "客户名称": "北京车之家信息技术有限公司",
            "回访人": "舒磊",
            "回访状态": "已回访",
            "回访链接": "",
            "PTS选择的满意度": "十分满意",
            "备注": "来自钉钉文档",
        }
    ]

    result = recognizer.recognize(raw_columns, raw_rows)
    normalized = result.normalized_records[0]["normalized_data"]

    assert normalized["satisfaction"] == "十分满意"
    assert normalized["feedback_note"] == "来自钉钉文档"


def test_inspection_real_rows_field_recognition() -> None:
    recognizer = InspectionRecognizer()
    raw_columns = ["企业名称", "任务链接", "工单号", "完成状态", "报告名称"]
    raw_rows = [
        {
            "row_id": "inspection-alias-001",
            "企业名称": "南京别名客户",
            "任务链接": "https://wo.example.com/alias-001",
            "工单号": "WO-ALIAS-001",
            "完成状态": "完成",
            "报告名称": "南京别名客户-巡检报告",
        }
    ]

    result = recognizer.recognize(raw_columns, raw_rows)

    assert result.field_mapping["customer_name"] == "企业名称"
    assert result.field_mapping["inspection_done"] == "完成状态"
    assert result.normalized_records[0]["normalized_data"]["inspection_done"] is True
    assert result.normalized_records[0]["normalized_data"]["work_order_id"] == "WO-ALIAS-001"
    assert result.recognition_status == "full"


def test_proactive_real_rows_field_recognition() -> None:
    recognizer = ProactiveRecognizer()
    raw_columns = ["公司名称", "产品页面", "信息ID", "建联状态", "闭环链接", "客户反馈", "联络人", "手机号", "负责人"]
    raw_rows = [
        {
            "row_id": "proactive-alias-001",
            "公司名称": "北京别名客户",
            "产品页面": "https://product.example.com/alias-001",
            "信息ID": "PI-ALIAS-001",
            "建联状态": "已联系",
            "闭环链接": "",
            "客户反馈": "主动回访别名测试",
            "联络人": "赵总",
            "手机号": "13800000000",
            "负责人": "工程师A",
        }
    ]

    result = recognizer.recognize(raw_columns, raw_rows)

    assert result.field_mapping["customer_name"] == "公司名称"
    assert result.field_mapping["liaison_status"] == "建联状态"
    assert result.normalized_records[0]["normalized_data"]["liaison_status"] == "已建联"
    assert result.normalized_records[0]["normalized_data"]["contact_phone"] == "13800000000"
    assert result.recognition_status == "full"


def test_recognition_status_full_partial_failed() -> None:
    recognizer = VisitRecognizer()

    full_result = recognizer.recognize(
        ["客户名称", "回访人", "回访状态", "回访链接"],
        [{"row_id": "full-001", "客户名称": "客户A", "回访人": "舒磊", "回访状态": "已回访", "回访链接": ""}],
    )
    partial_result = recognizer.recognize(
        ["客户名称", "回访负责人", "闭环链接"],
        [{"row_id": "partial-001", "客户名称": "客户B", "回访负责人": "舒磊", "闭环链接": ""}],
    )
    failed_result = recognizer.recognize(
        ["回访状态", "闭环链接"],
        [{"row_id": "failed-001", "回访状态": "已回访", "闭环链接": ""}],
    )

    assert full_result.recognition_status == "full"
    assert partial_result.recognition_status == "partial"
    assert failed_result.recognition_status == "failed"


def test_recognizer_output_structure_complete() -> None:
    recognizer = VisitRecognizer()
    result = recognizer.recognize(
        ["客户名称", "回访人", "回访状态", "回访链接"],
        [{"row_id": "structure-001", "客户名称": "客户A", "回访人": "舒磊", "回访状态": "已回访", "回访链接": ""}],
    )

    assert isinstance(result.normalized_records, list)
    assert isinstance(result.field_mapping, dict)
    assert isinstance(result.field_confidence, dict)
    assert isinstance(result.field_evidence, dict)
    assert isinstance(result.field_samples, dict)
    assert isinstance(result.unresolved_fields, list)
    assert result.recognition_status in {"full", "partial", "failed"}


def test_planner_linkage_with_real_rows() -> None:
    visit_collect = asyncio.run(VisitCollector(_default_source_config("visit")).collect())
    visit_recognition = VisitRecognizer().recognize(visit_collect.raw_columns, visit_collect.raw_rows)
    visit_tasks = VisitPlanner().plan(visit_recognition.normalized_records)
    assert [task.plan_status for task in visit_tasks] == ["planned", "skipped"]

    inspection_collect = asyncio.run(InspectionCollector(_default_source_config("inspection")).collect())
    inspection_recognition = InspectionRecognizer().recognize(inspection_collect.raw_columns, inspection_collect.raw_rows)
    inspection_tasks = InspectionPlanner().plan(inspection_recognition.normalized_records)
    assert [task.plan_status for task in inspection_tasks] == ["planned", "skipped"]

    proactive_collect = asyncio.run(ProactiveCollector(_default_source_config("proactive")).collect())
    proactive_recognition = ProactiveRecognizer().recognize(proactive_collect.raw_columns, proactive_collect.raw_rows)
    proactive_tasks = ProactivePlanner().plan(proactive_recognition.normalized_records)
    assert [task.plan_status for task in proactive_tasks] == ["planned", "skipped"]
    assert all(task.skip_reason is None or isinstance(task.skip_reason, str) for task in proactive_tasks)


def test_visit_planner_hits_after_recognizer_normalization() -> None:
    recognizer = VisitRecognizer()
    planner = VisitPlanner()
    result = recognizer.recognize(
        ["客户名称", "PTS交付链接", "交付单号", "回访人", "回访状态", "回访链接"],
        [
            {
                "row_id": "visit-planned-001",
                "客户名称": "招商银行股份有限公司信用卡中心",
                "PTS交付链接": '{"url":"https://pts.example.com/project/001#base","text":"https://pts.example.com/project/001#base"}',
                "交付单号": "DEL-PLANNED-001",
                "回访人": '[{"id":"2747525037","name":"舒磊","realName":"舒磊","data-type":"mention"}]',
                "回访状态": "已回访",
                "回访链接": "",
            },
            {
                "row_id": "visit-skipped-001",
                "客户名称": "北京自如住房租赁有限公司",
                "PTS交付链接": '{"url":"https://pts.example.com/project/002#base","text":"https://pts.example.com/project/002#base"}',
                "交付单号": "",
                "回访人": '[{"id":"2210855536","name":"杨彬","realName":"杨彬","data-type":"mention"}]',
                "回访状态": "跟进中",
                "回访链接": "",
            },
        ],
    )

    plans = planner.plan(result.normalized_records)

    assert [item.plan_status for item in plans] == ["planned", "skipped"]
