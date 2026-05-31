from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from core.config import Settings
from services.dingtalk_visit_writeback import DingtalkVisitWritebackService
from services.executors.proactive_executor import ProactiveExecutor
from services.executors.proactive_tag_mark_executor import ProactiveTagMarkExecutor, _TAG_MARK_STATUSES
from services.executors.schemas import ExecutorContext


def _make_context(
    *,
    liaison_status: str = "不用回访",
    tag_name: str | None = None,
    customer_name: str = "测试客户",
    product_link: str = "",
    product_info_id: str = "",
    visit_link: str = "",
    source_row_id: str = "row-001",
    source_doc_key: str = "J9LnW6jQKp6yelvD",
) -> ExecutorContext:
    normalized_data = {
        "customer_name": customer_name,
        "liaison_status": liaison_status,
        "product_link": product_link,
        "product_info_id": product_info_id,
        "visit_link": visit_link,
        "tag_name": tag_name or liaison_status,
    }
    return ExecutorContext(
        task_plan_id="tp-001",
        module_code="proactive",
        task_type="proactive_tag_mark",
        plan_status="planned",
        normalized_record_id="nr-001",
        source_row_id=source_row_id,
        recognition_status="full",
        planned_payload={**normalized_data},
        normalized_data=normalized_data,
        source_url="https://alidocs.dingtalk.com",
        source_doc_key=source_doc_key,
        source_view_key="f20Z2ZJ",
        source_collector_type="dws_cli",
        source_extra_config={},
    )


def _make_settings(**overrides) -> Settings:
    defaults = {
        "pts_base_url": "https://pts.chaitin.net",
        "pts_api_token": "pt_test_token",
        "pts_api_base_url": "http://api.in.chaitin.net",
        "pts_direct_http_enabled": True,
        "pts_browser_profile_enabled": False,
        "pts_execution_transport": "cookie_direct",
        "proactive_writeback_enabled": True,
    }
    return Settings(**{**defaults, **overrides})


# ─── precheck tests ────────────────────────────────────────────────


def test_precheck_passes_for_no_visit_status() -> None:
    settings = _make_settings()
    executor = ProactiveTagMarkExecutor(settings=settings)
    context = _make_context(liaison_status="不用回访", product_link="https://pts.chaitin.net/project/abc123")
    result = executor.precheck(context)

    assert result.run_status == "precheck_passed"
    assert result.executor_version == ProactiveTagMarkExecutor.executor_version
    assert result.result_payload["tag_name"] == "不用回访"


def test_precheck_passes_for_after_sale_disconnect_status() -> None:
    settings = _make_settings()
    executor = ProactiveTagMarkExecutor(settings=settings)
    context = _make_context(liaison_status="售后断联", product_info_id="67890abcdef123456789012")
    result = executor.precheck(context)

    assert result.run_status == "precheck_passed"
    assert result.result_payload["tag_name"] == "售后断联"


def test_precheck_fails_for_wrong_liaison_status() -> None:
    settings = _make_settings()
    executor = ProactiveTagMarkExecutor(settings=settings)
    context = _make_context(liaison_status="已建联")
    result = executor.precheck(context)

    assert result.run_status == "precheck_failed"
    assert "liaison_status" in result.error_message


def test_precheck_fails_for_missing_customer_name() -> None:
    settings = _make_settings()
    executor = ProactiveTagMarkExecutor(settings=settings)
    context = _make_context(customer_name="")
    result = executor.precheck(context)

    assert result.run_status == "precheck_failed"
    assert "customer_name" in result.error_message


def test_precheck_fails_for_existing_visit_link() -> None:
    settings = _make_settings()
    executor = ProactiveTagMarkExecutor(settings=settings)
    context = _make_context(visit_link="https://some-link")
    result = executor.precheck(context)

    assert result.run_status == "precheck_failed"
    assert "visit_link" in result.error_message


def test_precheck_fails_for_missing_product_link_and_info_id() -> None:
    settings = _make_settings()
    executor = ProactiveTagMarkExecutor(settings=settings)
    context = _make_context(product_link="", product_info_id="")
    result = executor.precheck(context)

    assert result.run_status == "precheck_failed"
    assert "product_link" in result.error_message


def test_precheck_fails_for_wrong_module_code_or_task_type() -> None:
    settings = _make_settings()
    executor = ProactiveTagMarkExecutor(settings=settings)
    context = _make_context()
    # Override module_code / task_type
    context = ExecutorContext(
        task_plan_id="tp-001",
        module_code="visit",
        task_type="visit_close",
        plan_status="planned",
        normalized_record_id="nr-001",
        source_row_id="row-001",
        recognition_status="full",
        planned_payload={},
        normalized_data={"liaison_status": "不用回访"},
        source_collector_type="dws_cli",
    )
    result = executor.precheck(context)

    assert result.run_status == "precheck_failed"
    assert "module_code" in result.error_message or "task_type" in result.error_message


def test_precheck_fails_when_pts_api_not_available() -> None:
    settings = _make_settings(pts_api_token="", pts_cookie_header="", pts_direct_http_enabled=False, pts_browser_profile_enabled=False, pts_execution_transport="auto")
    # Need to mock the proactive executor's transport methods since they depend on
    # pts_execution_transport and other settings that may have defaults
    mock_proactive = MagicMock(spec=ProactiveExecutor)
    mock_proactive._can_use_direct_http_transport = MagicMock(return_value=False)
    mock_proactive._can_use_browser_profile_transport = MagicMock(return_value=False)

    executor = ProactiveTagMarkExecutor(settings=settings, proactive_executor=mock_proactive)
    context = _make_context(product_link="https://pts.chaitin.net/project/abc123")
    result = executor.precheck(context)

    assert result.run_status == "precheck_failed"
    assert "PTS" in result.error_message


# ─── dry_run tests ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dry_run_returns_ready() -> None:
    settings = _make_settings()
    executor = ProactiveTagMarkExecutor(settings=settings)
    context = _make_context(liaison_status="不用回访")
    result = await executor.dry_run(context)

    assert result.run_status == "dry_run_ready"
    assert result.result_payload["tag_name"] == "不用回访"
    assert result.result_payload["execution_mode"] == "dry_run"


# ─── execute tests ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_execute_success_with_product_link() -> None:
    settings = _make_settings()
    mock_proactive = MagicMock(spec=ProactiveExecutor)
    mock_proactive.settings = settings
    mock_proactive.module_code = "proactive"
    mock_proactive.task_type = "proactive_visit_close"
    mock_proactive._can_use_direct_http_transport = MagicMock(return_value=True)
    mock_proactive._can_use_browser_profile_transport = MagicMock(return_value=False)
    mock_proactive._resolve_delivery_id_from_product_link = AsyncMock(
        return_value=MagicMock(delivery_id="abc123def456789012345678", source="delivery_url", raw="https://pts.chaitin.net/project/abc123")
    )
    mock_proactive._query_pts_graphql_payload = AsyncMock(
        return_value={"productDeliveryByID": {"id": "abc123def456789012345678", "label_list": []}}
    )
    # Mutation returns true for success
    mock_proactive._query_pts_graphql_payload_side_effect = None

    # We need separate mocks for the query and mutation calls
    # Use side_effect to return different results based on payload
    call_count = 0

    async def mock_graphql_payload(payload):
        call_count_local = 0
        op_name = payload.get("operationName", "")
        if op_name == "ProductDeliveryLabels":
            return {"productDeliveryByID": {"id": "abc123def456789012345678", "label_list": []}}
        if op_name == "UpdateProductDeliveryLabel":
            return {"updateProductDeliveryLabel": True}
        return {}

    mock_proactive._query_pts_graphql_payload = AsyncMock(side_effect=mock_graphql_payload)
    mock_proactive._query_pts_graphql_payload_via_browser_profile = AsyncMock(side_effect=mock_graphql_payload)

    mock_writeback = AsyncMock(return_value={
        "action": "writeback_visit_link_to_dingtalk",
        "status": "success",
        "writeback_mode": "dws_cli",
        "visit_link": "已打标签",
    })

    executor = ProactiveTagMarkExecutor(
        settings=settings,
        writeback_service=MagicMock(spec=DingtalkVisitWritebackService),
        proactive_executor=mock_proactive,
    )
    executor.writeback_service.write_visit_link = mock_writeback

    context = _make_context(liaison_status="不用回访", product_link="https://pts.chaitin.net/project/abc123")
    result = await executor.execute(context)

    assert result.run_status == "success"
    assert result.final_link == "已打标签"
    assert result.result_payload["delivery_id"] == "abc123def456789012345678"
    assert result.result_payload["tag_name"] == "不用回访"
    assert result.result_payload["new_label_list"] == ["不用回访"]
    mock_writeback.assert_called_once()


@pytest.mark.asyncio
async def test_execute_success_appends_to_existing_labels() -> None:
    settings = _make_settings()
    mock_proactive = MagicMock(spec=ProactiveExecutor)
    mock_proactive.settings = settings
    mock_proactive._can_use_direct_http_transport = MagicMock(return_value=True)
    mock_proactive._can_use_browser_profile_transport = MagicMock(return_value=False)

    async def mock_graphql_payload(payload):
        op_name = payload.get("operationName", "")
        if op_name == "ProductDeliveryLabels":
            return {"productDeliveryByID": {"id": "abc123def456789012345678", "label_list": ["已审核", "VIP"]}}
        if op_name == "UpdateProductDeliveryLabel":
            return {"updateProductDeliveryLabel": True}
        return {}

    mock_proactive._query_pts_graphql_payload = AsyncMock(side_effect=mock_graphql_payload)
    mock_proactive._query_pts_graphql_payload_via_browser_profile = AsyncMock(side_effect=mock_graphql_payload)
    mock_proactive._resolve_delivery_id_from_product_link = AsyncMock(
        return_value=MagicMock(delivery_id="abc123def456789012345678", source="delivery_url", raw="url")
    )

    mock_writeback = AsyncMock(return_value={"status": "success", "writeback_mode": "dws_cli"})

    executor = ProactiveTagMarkExecutor(
        settings=settings,
        writeback_service=MagicMock(spec=DingtalkVisitWritebackService),
        proactive_executor=mock_proactive,
    )
    executor.writeback_service.write_visit_link = mock_writeback

    context = _make_context(liaison_status="售后断联", product_link="https://pts.chaitin.net/project/abc123")
    result = await executor.execute(context)

    assert result.run_status == "success"
    assert result.final_link == "已打标签"
    assert result.result_payload["existing_labels"] == ["已审核", "VIP"]
    assert result.result_payload["new_label_list"] == ["已审核", "VIP", "售后断联"]


@pytest.mark.asyncio
async def test_execute_does_not_duplicate_existing_tag() -> None:
    settings = _make_settings()
    mock_proactive = MagicMock(spec=ProactiveExecutor)
    mock_proactive.settings = settings
    mock_proactive._can_use_direct_http_transport = MagicMock(return_value=True)
    mock_proactive._can_use_browser_profile_transport = MagicMock(return_value=False)

    async def mock_graphql_payload(payload):
        op_name = payload.get("operationName", "")
        if op_name == "ProductDeliveryLabels":
            return {"productDeliveryByID": {"id": "abc123", "label_list": ["不用回访"]}}
        if op_name == "UpdateProductDeliveryLabel":
            return {"updateProductDeliveryLabel": True}
        return {}

    mock_proactive._query_pts_graphql_payload = AsyncMock(side_effect=mock_graphql_payload)
    mock_proactive._query_pts_graphql_payload_via_browser_profile = AsyncMock(side_effect=mock_graphql_payload)
    mock_proactive._resolve_delivery_id_from_product_link = AsyncMock(
        return_value=MagicMock(delivery_id="abc123", source="delivery_url", raw="url")
    )

    executor = ProactiveTagMarkExecutor(
        settings=settings,
        writeback_service=MagicMock(spec=DingtalkVisitWritebackService),
        proactive_executor=mock_proactive,
    )
    executor.writeback_service.write_visit_link = AsyncMock(return_value={"status": "success"})

    context = _make_context(liaison_status="不用回访", product_link="https://pts.chaitin.net/project/abc123")
    result = await executor.execute(context)

    assert result.run_status == "success"
    assert result.result_payload["new_label_list"] == ["不用回访"]  # no duplicate


@pytest.mark.asyncio
async def test_execute_fails_when_delivery_id_unresolved() -> None:
    settings = _make_settings()
    mock_proactive = MagicMock(spec=ProactiveExecutor)
    mock_proactive.settings = settings
    mock_proactive._resolve_delivery_id_from_product_link = AsyncMock(
        return_value=MagicMock(delivery_id=None, source="not_found", raw="")
    )

    executor = ProactiveTagMarkExecutor(
        settings=settings,
        writeback_service=MagicMock(spec=DingtalkVisitWritebackService),
        proactive_executor=mock_proactive,
    )

    context = _make_context(product_link="https://pts.chaitin.net/project/invalid")
    result = await executor.execute(context)

    assert result.run_status == "failed"
    assert "delivery_id" in result.error_message


@pytest.mark.asyncio
async def test_execute_fails_when_mutation_returns_false() -> None:
    settings = _make_settings()
    mock_proactive = MagicMock(spec=ProactiveExecutor)
    mock_proactive.settings = settings
    mock_proactive._can_use_direct_http_transport = MagicMock(return_value=True)
    mock_proactive._can_use_browser_profile_transport = MagicMock(return_value=False)

    async def mock_graphql_payload(payload):
        op_name = payload.get("operationName", "")
        if op_name == "ProductDeliveryLabels":
            return {"productDeliveryByID": {"id": "abc123", "label_list": []}}
        if op_name == "UpdateProductDeliveryLabel":
            return {"updateProductDeliveryLabel": False}
        return {}

    mock_proactive._query_pts_graphql_payload = AsyncMock(side_effect=mock_graphql_payload)
    mock_proactive._query_pts_graphql_payload_via_browser_profile = AsyncMock(side_effect=mock_graphql_payload)
    mock_proactive._resolve_delivery_id_from_product_link = AsyncMock(
        return_value=MagicMock(delivery_id="abc123", source="delivery_url", raw="url")
    )

    executor = ProactiveTagMarkExecutor(
        settings=settings,
        writeback_service=MagicMock(spec=DingtalkVisitWritebackService),
        proactive_executor=mock_proactive,
    )

    context = _make_context(product_link="https://pts.chaitin.net/project/abc123")
    result = await executor.execute(context)

    assert result.run_status == "failed"
    assert result.retryable is True


# ─── planner tests ─────────────────────────────────────────────────


def test_proactive_planner_creates_tag_mark_task_for_no_visit_status() -> None:
    from services.planners.proactive_planner import ProactivePlanner

    planner = ProactivePlanner()
    records = [
        {
            "source_row_id": "row-001",
            "recognition_status": "full",
            "normalized_data": {
                "customer_name": "测试客户A",
                "liaison_status": "不用回访",
                "visit_link": "",
                "product_link": "https://pts.chaitin.net/project/abc123",
                "product_info_id": "",
            },
        }
    ]
    plans = planner.plan(records)

    assert len(plans) == 1
    assert plans[0].task_type == "proactive_tag_mark"
    assert plans[0].eligibility is True
    assert plans[0].plan_status == "planned"
    assert plans[0].planned_payload["tag_name"] == "不用回访"


def test_proactive_planner_creates_tag_mark_task_for_after_sale_disconnect() -> None:
    from services.planners.proactive_planner import ProactivePlanner

    planner = ProactivePlanner()
    records = [
        {
            "source_row_id": "row-002",
            "recognition_status": "full",
            "normalized_data": {
                "customer_name": "测试客户B",
                "liaison_status": "售后断联",
                "visit_link": "",
                "product_link": "https://pts.chaitin.net/project/abc123",
            },
        }
    ]
    plans = planner.plan(records)

    assert len(plans) == 1
    assert plans[0].task_type == "proactive_tag_mark"
    assert plans[0].planned_payload["tag_name"] == "售后断联"


def test_proactive_planner_creates_visit_close_task_for_liaison_connected() -> None:
    from services.planners.proactive_planner import ProactivePlanner

    planner = ProactivePlanner()
    records = [
        {
            "source_row_id": "row-003",
            "recognition_status": "full",
            "normalized_data": {
                "customer_name": "测试客户C",
                "liaison_status": "已建联",
                "visit_link": "",
                "visit_owner": "舒磊",
                "feedback_note": "客户满意",
                "product_link": "https://pts.chaitin.net/project/abc123",
            },
        }
    ]
    plans = planner.plan(records)

    assert len(plans) == 1
    assert plans[0].task_type == "proactive_visit_close"


def test_proactive_planner_skips_no_visit_without_customer_name() -> None:
    from services.planners.proactive_planner import ProactivePlanner

    planner = ProactivePlanner()
    records = [
        {
            "source_row_id": "row-004",
            "recognition_status": "full",
            "normalized_data": {
                "customer_name": "",
                "liaison_status": "不用回访",
                "visit_link": "",
            },
        }
    ]
    plans = planner.plan(records)

    assert plans[0].task_type == "proactive_tag_mark"
    assert plans[0].eligibility is False
    assert plans[0].plan_status == "skipped"


# ─── healthcheck tests ─────────────────────────────────────────────


def test_healthcheck_returns_api_availability() -> None:
    settings = _make_settings()
    executor = ProactiveTagMarkExecutor(settings=settings)
    result = executor.healthcheck()

    assert result["module_code"] == "proactive"
    assert result["task_type"] == "proactive_tag_mark"
    assert result["pts_api_available"] is True


def test_healthcheck_shows_no_api_when_unconfigured() -> None:
    settings = _make_settings(pts_api_token="", pts_cookie_header="", pts_direct_http_enabled=False, pts_browser_profile_enabled=False, pts_execution_transport="auto")
    mock_proactive = MagicMock(spec=ProactiveExecutor)
    mock_proactive._can_use_direct_http_transport = MagicMock(return_value=False)
    mock_proactive._can_use_browser_profile_transport = MagicMock(return_value=False)

    executor = ProactiveTagMarkExecutor(settings=settings, proactive_executor=mock_proactive)
    result = executor.healthcheck()

    assert result["pts_api_available"] is False