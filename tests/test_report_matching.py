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


def test_multiple_candidates_require_manual(tmp_path: Path) -> None:
    _write_file(tmp_path / "南京真实客户雷池巡检报告-2026.03.27.docx")
    _write_file(tmp_path / "南京真实客户雷池巡检报告-2026.03.28.docx")
    _write_file(tmp_path / "南京真实客户雷池巡检报告-2026.03.27.pdf")

    files = InspectionReportScanner(tmp_path).scan()
    result = InspectionReportMatcher().match("南京真实客户", files)

    assert result.matched is False
    assert result.manual_required is True
    assert result.match_strategy == "multiple_candidates"
    assert "word" in (result.error_message or "")


def test_inspection_planner_and_executor_linkage(tmp_path: Path, monkeypatch) -> None:
    _write_file(tmp_path / "南京真实客户雷池巡检报告-2026.03.27.docx")
    _write_file(tmp_path / "南京真实客户雷池巡检报告-2026.03.27.pdf")
    monkeypatch.setenv("INSPECTION_REPORT_ROOT", str(tmp_path))
    get_settings.cache_clear()

    planner = InspectionPlanner()
    tasks = planner.plan(
        [
            {
                "source_row_id": "inspection-001",
                "recognition_status": "full",
                "normalized_data": {
                    "customer_name": "南京真实客户",
                    "inspection_done": True,
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
            "customer_name": "南京真实客户",
            "inspection_done": True,
            "work_order_link": "https://wo.example.com/1",
            "work_order_id": "WO-001",
        },
    )
    precheck = executor.precheck(context)
    assert precheck.run_status == "precheck_passed"
    assert precheck.result_payload["report_match"]["matched"] is True
    get_settings.cache_clear()
