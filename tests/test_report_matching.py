from pathlib import Path

from core.config import get_settings
from services.executors.inspection_executor import InspectionExecutor
from services.executors.schemas import ExecutorContext
from services.planners.inspection_planner import InspectionPlanner
from services.report_matching.matcher import InspectionReportMatcher
from services.report_matching.scanner import InspectionReportScanner


def _write_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("dummy", encoding="utf-8")


def test_report_scanner_filters_temp_and_invalid_files(tmp_path: Path) -> None:
    _write_file(tmp_path / "南京真实客户雷池巡检报告-2026.03.27.docx")
    _write_file(tmp_path / "南京真实客户雷池巡检报告-2026.03.27.pdf")
    _write_file(tmp_path / ".~南京真实客户雷池巡检报告-2026.03.27.docx")
    _write_file(tmp_path / "说明.txt")

    scanner = InspectionReportScanner(tmp_path)
    files = scanner.scan()

    assert len(files) == 2
    assert {item.file_type for item in files} == {"word", "pdf"}


def test_company_name_matching_success(tmp_path: Path) -> None:
    _write_file(tmp_path / "南京真实客户雷池巡检报告-2026.03.27.docx")
    _write_file(tmp_path / "南京真实客户雷池巡检报告-2026.03.27.pdf")

    files = InspectionReportScanner(tmp_path).scan()
    result = InspectionReportMatcher().match("南京真实客户", files)

    assert result.matched is True
    assert result.manual_required is False
    assert result.match_strategy in {"exact", "normalized"}
    assert result.missing_file_types == []


def test_missing_word_file_requires_manual(tmp_path: Path) -> None:
    _write_file(tmp_path / "南京真实客户雷池巡检报告-2026.03.27.pdf")

    files = InspectionReportScanner(tmp_path).scan()
    result = InspectionReportMatcher().match("南京真实客户", files)

    assert result.matched is False
    assert result.manual_required is True
    assert result.missing_file_types == ["word"]
    assert result.match_strategy == "missing_files"


def test_missing_pdf_file_requires_manual(tmp_path: Path) -> None:
    _write_file(tmp_path / "南京真实客户雷池巡检报告-2026.03.27.docx")

    files = InspectionReportScanner(tmp_path).scan()
    result = InspectionReportMatcher().match("南京真实客户", files)

    assert result.matched is False
    assert result.manual_required is True
    assert result.missing_file_types == ["pdf"]
    assert result.match_strategy == "missing_files"


def test_multiple_word_candidates_are_allowed(tmp_path: Path) -> None:
    _write_file(tmp_path / "南京真实客户雷池巡检报告-2026.03.27.docx")
    _write_file(tmp_path / "南京真实客户雷池巡检报告-2026.03.28.docx")
    _write_file(tmp_path / "南京真实客户雷池巡检报告-2026.03.27.pdf")

    files = InspectionReportScanner(tmp_path).scan()
    result = InspectionReportMatcher().match("南京真实客户", files)

    assert result.matched is True
    assert result.manual_required is False
    assert result.match_strategy in {"exact", "normalized"}
    assert len(result.matched_files["word"]) == 2


def test_archived_word_candidates_are_ignored_when_active_file_exists(tmp_path: Path) -> None:
    _write_file(tmp_path / "国网冀北电力有限公司电力科学研究院墨攻巡检报告(1).docx")
    _write_file(
        tmp_path
        / "已上传的文档"
        / "国网冀北电力有限公司电力科学研究院墨攻巡检报告-1775185175.docx"
    )

    files = InspectionReportScanner(tmp_path).scan()
    result = InspectionReportMatcher(required_file_types=("word",)).match(
        "国网冀北电力有限公司电力科学研究院",
        files,
    )

    assert result.matched is True
    assert result.manual_required is False
    assert result.matched_files["word"] == [
        str(tmp_path / "国网冀北电力有限公司电力科学研究院墨攻巡检报告(1).docx")
    ]


def test_prefixed_word_filename_still_matches_customer(tmp_path: Path) -> None:
    _write_file(tmp_path / "牧云-运行状态巡检-昆明电力交易中心有限责任公司-20260325.docx")

    files = InspectionReportScanner(tmp_path).scan()
    result = InspectionReportMatcher(required_file_types=("word",)).match("昆明电力交易中心有限责任公司", files)

    assert result.matched is True
    assert result.manual_required is False
    assert result.matched_files["word"]


def test_bracketed_customer_with_chinese_date_suffix_matches_customer(tmp_path: Path) -> None:
    _write_file(tmp_path / "【上海科创银行】谛听巡检报告-2026年04月03日.docx")

    files = InspectionReportScanner(tmp_path).scan()
    result = InspectionReportMatcher(required_file_types=("word",)).match("上海科创银行有限公司", files)

    assert result.matched is True
    assert result.manual_required is False
    assert result.matched_files["word"][0].endswith("【上海科创银行】谛听巡检报告-2026年04月03日.docx")


def test_long_customer_name_matches_truncated_candidate_via_bidirectional_fuzzy(tmp_path: Path) -> None:
    _write_file(tmp_path / "国网冀北电力有限公司电力科学研究院墨攻巡检报告.docx")

    files = InspectionReportScanner(tmp_path).scan()
    result = InspectionReportMatcher(required_file_types=("word",)).match(
        "国网冀北电力有限公司电力科学研究院",
        files,
    )

    assert result.matched is True
    assert result.manual_required is False
    assert result.matched_files["word"][0].endswith("国网冀北电力有限公司电力科学研究院墨攻巡检报告.docx")


def test_duplicate_copy_suffix_does_not_require_manual(tmp_path: Path) -> None:
    _write_file(tmp_path / "国网冀北电力有限公司电力科学研究院墨攻巡检报告.docx")
    _write_file(tmp_path / "国网冀北电力有限公司电力科学研究院墨攻巡检报告(1).docx")

    files = InspectionReportScanner(tmp_path).scan()
    result = InspectionReportMatcher(required_file_types=("word",)).match(
        "国网冀北电力有限公司电力科学研究院",
        files,
    )

    assert result.matched is True
    assert result.manual_required is False
    assert len(result.matched_files["word"]) == 1


def test_inspection_planner_and_executor_linkage(tmp_path: Path, monkeypatch) -> None:
    _write_file(tmp_path / "南京真实客户雷池巡检报告-2026.03.27.docx")
    monkeypatch.setenv("INSPECTION_REPORT_ROOT", str(tmp_path))
    get_settings.cache_clear()

    planner = InspectionPlanner()
    tasks = planner.plan(
        [
            {
                "source_row_id": "inspection-001",
                "recognition_status": "full",
                "normalized_data": {
                    "inspection_month": "2026-03",
                    "customer_name": "南京真实客户",
                    "service_type": "巡检服务",
                    "executor_name": "舒磊",
                    "inspection_done": True,
                    "work_order_closed": False,
                    "work_order_link": "https://wo.example.com/1",
                    "work_order_id": "WO-001",
                    "report_match_name": "南京真实客户-巡检报告",
                },
            }
        ]
    )

    assert tasks[0].plan_status == "planned"
    assert tasks[0].planned_payload["report_status_hint"] == "pending_report_match"

    executor = InspectionExecutor()
    context = ExecutorContext(
        task_plan_id="task-001",
        module_code="inspection",
        task_type="inspection_close",
        plan_status="planned",
        normalized_record_id="record-001",
        recognition_status="full",
        planned_payload=tasks[0].planned_payload,
        normalized_data={
            "inspection_month": "2026-03",
            "customer_name": "南京真实客户",
            "service_type": "巡检服务",
            "executor_name": "舒磊",
            "inspection_done": True,
            "work_order_closed": False,
            "work_order_link": "https://wo.example.com/1",
            "work_order_id": "WO-001",
        },
    )
    precheck = executor.precheck(context)
    assert precheck.run_status == "precheck_passed"
    assert precheck.result_payload["report_match"]["matched"] is True
    get_settings.cache_clear()
