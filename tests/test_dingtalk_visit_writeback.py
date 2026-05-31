from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock

from services.dingtalk_visit_writeback import DingtalkVisitWritebackService
from services.executors.schemas import ExecutorContext


def _build_context() -> ExecutorContext:
    return ExecutorContext(
        task_plan_id="task-1",
        module_code="visit",
        task_type="visit_close",
        plan_status="planned",
        normalized_record_id="record-1",
        source_row_id="visit-row-001",
        recognition_status="recognized",
        planned_payload={},
        normalized_data={"customer_name": "客户A"},
        source_url="https://alidocs.dingtalk.com",
        source_doc_key="4j6OJ5jPAGa8eq3p",
        source_view_key="AKOehLK",
        source_collector_type="dingtalk",
        source_extra_config={
            "structured_endpoint": "/api/document/data",
            "parallelv2_sheet_id": "Igz9TVd",
            "parallelv2_view_id": "AKOehLK",
            "structured_headers": {
                "Referer": "https://alidocs.dingtalk.com/iframe/notable?docKey=4j6OJ5jPAGa8eq3p&sheetId=Igz9TVd&viewId=AKOehLK"
            },
        },
    )


def _build_proactive_context() -> ExecutorContext:
    """超半年主动回访模块的上下文。"""
    return ExecutorContext(
        task_plan_id="task-proactive-1",
        module_code="proactive",
        task_type="visit_close",
        plan_status="planned",
        normalized_record_id="record-proactive-1",
        source_row_id="proactive-row-001",
        recognition_status="recognized",
        planned_payload={},
        normalized_data={"customer_name": "客户B"},
        source_url="https://alidocs.dingtalk.com",
        source_doc_key="J9LnW6jQKp6yelvD",
        source_view_key="f20Z2ZJ",
        source_collector_type="dingtalk",
        source_extra_config={
            "structured_endpoint": "/api/document/data",
            "parallelv2_sheet_id": "Z991EZV",
            "parallelv2_view_id": "f20Z2ZJ",
            "structured_headers": {
                "Referer": "https://alidocs.dingtalk.com/iframe/notable?docKey=J9LnW6jQKp6yelvD&sheetId=Z991EZV&viewId=f20Z2ZJ"
            },
        },
    )


def test_dingtalk_visit_writeback_uses_dws_cli(monkeypatch) -> None:
    """DWS CLI 可用时，优先使用 DWS CLI 写入回访链接。"""
    service = DingtalkVisitWritebackService()
    monkeypatch.setattr(
        "services.dingtalk_visit_writeback._resolve_dws_cli_path",
        lambda _s: "/usr/local/bin/dws",
    )

    fake_result = MagicMock()
    fake_result.returncode = 0
    fake_result.stdout = json.dumps({
        "status": "success",
        "data": {"recordIds": ["visit-row-001"]},
    })
    fake_result.stderr = ""
    monkeypatch.setattr(
        "services.dingtalk_visit_writeback._run_subprocess",
        lambda cmd: fake_result,
    )

    result = asyncio.run(
        service.write_visit_link(
            context=_build_context(),
            final_link="https://pts.chaitin.net/return-visit/detail/visit-1",
        )
    )

    assert result["status"] == "success"
    assert result["writeback_mode"] == "dws_cli"
    assert result["source_row_id"] == "visit-row-001"
    assert result["visit_link"] == "https://pts.chaitin.net/return-visit/detail/visit-1"


def test_dingtalk_visit_writeback_dws_cli_unavailable_returns_disabled(monkeypatch) -> None:
    """DWS CLI 不可用时，返回 disabled（不再回退到 Chrome 浏览器自动化）。"""
    service = DingtalkVisitWritebackService()
    monkeypatch.setattr(
        "services.dingtalk_visit_writeback._resolve_dws_cli_path",
        lambda _s: None,
    )

    result = asyncio.run(
        service.write_visit_link(
            context=_build_context(),
            final_link="https://pts.chaitin.net/return-visit/detail/visit-1",
        )
    )

    assert result["status"] == "skipped"
    assert result["writeback_mode"] == "disabled"


def test_dingtalk_visit_writeback_skips_non_dingtalk_context() -> None:
    service = DingtalkVisitWritebackService()
    context = _build_context().model_copy(update={"source_collector_type": "fixture"})

    result = asyncio.run(
        service.write_visit_link(
            context=context,
            final_link="https://pts.chaitin.net/return-visit/detail/visit-1",
        )
    )

    assert result["status"] == "skipped"
    assert result["writeback_mode"] == "disabled"


def test_dingtalk_visit_writeback_missing_row_id(monkeypatch) -> None:
    """source_row_id 缺失时返回 skipped（DWS CLI 无法写入）。"""
    service = DingtalkVisitWritebackService()
    monkeypatch.setattr(
        "services.dingtalk_visit_writeback._resolve_dws_cli_path",
        lambda _s: "/usr/local/bin/dws",
    )
    context = _build_context().model_copy(update={"source_row_id": None})

    result = asyncio.run(
        service.write_visit_link(
            context=context,
            final_link="https://pts.chaitin.net/return-visit/detail/visit-1",
        )
    )

    assert result["status"] == "skipped"
    assert result["writeback_mode"] == "disabled"


def test_dingtalk_proactive_writeback_uses_dws_cli(monkeypatch) -> None:
    """超半年主动回访模块使用 DWS CLI 写入时，选择正确的 base/table/field。"""
    service = DingtalkVisitWritebackService()
    monkeypatch.setattr(
        "services.dingtalk_visit_writeback._resolve_dws_cli_path",
        lambda _s: "/usr/local/bin/dws",
    )

    captured_cmds: list[list[str]] = []
    fake_result = MagicMock()
    fake_result.returncode = 0
    fake_result.stdout = json.dumps({
        "status": "success",
        "data": {"recordIds": ["proactive-row-001"]},
    })
    fake_result.stderr = ""

    def capture_run(cmd):
        captured_cmds.append(cmd)
        return fake_result

    monkeypatch.setattr(
        "services.dingtalk_visit_writeback._run_subprocess",
        capture_run,
    )

    result = asyncio.run(
        service.write_visit_link(
            context=_build_proactive_context(),
            final_link="https://pts.chaitin.net/return-visit/detail/proactive-1",
        )
    )

    assert result["status"] == "success"
    assert result["writeback_mode"] == "dws_cli"
    assert result["source_row_id"] == "proactive-row-001"
    assert result["field_id"] == "vxfqjxrfpcm57vc1rlbu4"  # proactive 的 field-id
    # 验证 DWS CLI 使用了正确的 proactive 配置
    assert len(captured_cmds) == 1
    cmd = captured_cmds[0]
    # base-id 应为 proactive 的
    assert "KGZLxjv9VG37XNDXS45epDXYV6EDybno" in cmd
    # table-id 应为 proactive 的
    assert "Z991EZV" in cmd
    # records 里应包含 proactive 的 field-id
    records_json = cmd[cmd.index("--records") + 1]
    assert "vxfqjxrfpcm57vc1rlbu4" in records_json