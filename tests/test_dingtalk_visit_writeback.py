from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from services.collectors.dingtalk_parallelv2_decoder import DingtalkDocumentStructure
from services.dingtalk_visit_writeback import DingtalkVisitWritebackService
from services.executors.schemas import ExecutorContext


class _FakeChromeSession:
    last_calls: list[tuple[str, tuple, dict]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def open_url(self, target_url: str) -> None:
        self.last_calls.append(("open_url", (target_url,), {}))

    async def wait_until_ready(self) -> None:
        self.last_calls.append(("wait_until_ready", (), {}))

    async def ensure_column_visible(self, target_field_id: str) -> None:
        self.last_calls.append(("ensure_column_visible", (target_field_id,), {}))

    async def focus_grid(self, customer_field_id: str) -> None:
        self.last_calls.append(("focus_grid", (customer_field_id,), {}))

    async def navigate_to_row(self, **kwargs) -> None:
        self.last_calls.append(("navigate_to_row", (), kwargs))

    async def select_cell(self, **kwargs) -> None:
        self.last_calls.append(("select_cell", (), kwargs))

    async def move_selection_to_field(self, **kwargs) -> None:
        self.last_calls.append(("move_selection_to_field", (), kwargs))

    async def open_link_editor(self) -> None:
        self.last_calls.append(("open_link_editor", (), {}))

    async def fill_link_editor(self, **kwargs) -> None:
        self.last_calls.append(("fill_link_editor", (), kwargs))

    async def save_link_editor(self) -> None:
        self.last_calls.append(("save_link_editor", (), {}))

    async def assert_cell_contains(self, **kwargs) -> None:
        self.last_calls.append(("assert_cell_contains", (), kwargs))

    async def close_link_editor_if_visible(self) -> None:
        self.last_calls.append(("close_link_editor_if_visible", (), {}))


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


def test_dingtalk_visit_writeback_succeeds(monkeypatch) -> None:
    service = DingtalkVisitWritebackService()
    structure = DingtalkDocumentStructure(
        sheet_id="Igz9TVd",
        view_id="AKOehLK",
        field_name_by_id={"customer-field": "客户名称", "link-field": "回访链接"},
        field_type_by_id={},
        field_enum_label_by_id={},
        view_field_ids=["customer-field", "link-field"],
        raw_columns=["客户名称", "回访链接"],
        record_ids=["visit-row-001"],
    )

    async def fake_load_structure(source_config):
        return structure

    monkeypatch.setattr(service, "_load_structure", fake_load_structure)
    _FakeChromeSession.last_calls = []
    fake_session = _FakeChromeSession()

    @asynccontextmanager
    async def fake_shared_session():
        yield fake_session

    monkeypatch.setattr(
        "services.dingtalk_visit_writeback._shared_chrome_dingtalk_session",
        fake_shared_session,
    )

    result = asyncio.run(
        service.write_visit_link(
            context=_build_context(),
            final_link="https://pts.chaitin.net/return-visit/detail/visit-1",
        )
    )

    assert result["status"] == "success"
    assert result["field_name"] == "回访链接"
    assert result["field_id"] == "link-field"
    assert result["source_row_id"] == "visit-row-001"
    assert any(call[0] == "fill_link_editor" for call in _FakeChromeSession.last_calls)


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


def test_dingtalk_visit_writeback_reuses_shared_session(monkeypatch) -> None:
    service = DingtalkVisitWritebackService()
    structure = DingtalkDocumentStructure(
        sheet_id="Igz9TVd",
        view_id="AKOehLK",
        field_name_by_id={"customer-field": "客户名称", "link-field": "回访链接"},
        field_type_by_id={},
        field_enum_label_by_id={},
        view_field_ids=["customer-field", "link-field"],
        raw_columns=["客户名称", "回访链接"],
        record_ids=["visit-row-001", "visit-row-002"],
    )

    async def fake_load_structure(source_config):
        return structure

    monkeypatch.setattr(service, "_load_structure", fake_load_structure)
    _FakeChromeSession.last_calls = []
    fake_session = _FakeChromeSession()

    @asynccontextmanager
    async def fake_shared_session():
        yield fake_session

    monkeypatch.setattr(
        "services.dingtalk_visit_writeback._shared_chrome_dingtalk_session",
        fake_shared_session,
    )

    first = _build_context()
    second = _build_context().model_copy(update={"source_row_id": "visit-row-002"})

    asyncio.run(
        service.write_visit_link(
            context=first,
            final_link="https://pts.chaitin.net/return-visit/detail/visit-1",
        )
    )
    asyncio.run(
        service.write_visit_link(
            context=second,
            final_link="https://pts.chaitin.net/return-visit/detail/visit-2",
        )
    )

    open_calls = [call for call in _FakeChromeSession.last_calls if call[0] == "open_url"]
    assert len(open_calls) == 2
    assert open_calls[0][1][0] == open_calls[1][1][0]
