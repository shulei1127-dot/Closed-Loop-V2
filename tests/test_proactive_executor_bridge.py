import asyncio

from core.config import Settings
from services.executors.proactive_executor import ProactiveExecutor
from services.executors.schemas import ExecutorContext


def _context(product_link: str) -> ExecutorContext:
    return ExecutorContext(
        task_plan_id="task-proactive-001",
        module_code="proactive",
        task_type="proactive_visit_close",
        plan_status="planned",
        normalized_record_id="record-proactive-001",
        source_row_id="row-proactive-001",
        recognition_status="full",
        normalized_data={
            "customer_name": "横琴华通金融租赁有限公司",
            "liaison_status": "已建联",
            "visit_owner": "龙静玥",
            "feedback_note": "主动回访反馈",
            "product_link": product_link,
        },
        planned_payload={},
    )


def test_proactive_bridge_resolves_delivery_id_from_product_info_graphql(monkeypatch) -> None:
    executor = ProactiveExecutor(
        settings=Settings(
            pts_base_url="https://pts.example.com",
            pts_cookie_header="session=pts-cookie",
            pts_browser_profile_enabled=False,
        )
    )
    calls = []

    async def fake_query(payload):
        calls.append(payload)
        return {
            "productInfoByID": {
                "id": "66b5f01ca5e0003e906d55e7",
                "type": "delivery",
                "product_detail": {
                    "product": {
                        "id": "security_product.safeline",
                        "name": "下一代Web应用防火墙（雷池20系列）",
                    },
                    "form": {"id": "software", "name": "软件版"},
                },
                "delivery": {
                    "id": "66a3080301fb92e1766b54a3",
                    "project": {
                        "id": "656f3af061e8123119f1e684",
                        "name": "2024-华通金租-waf",
                        "company": {"id": "6565f616882183cb2c9ca699", "name": "横琴华通金融租赁有限公司"},
                    },
                },
            }
        }

    async def fail_browser_query(payload):  # pragma: no cover - should not be called
        raise AssertionError("browser fallback should not run when direct GraphQL succeeds")

    monkeypatch.setattr(executor, "_query_pts_graphql_payload", fake_query)
    monkeypatch.setattr(executor, "_query_pts_graphql_payload_via_browser", fail_browser_query)

    bridge = asyncio.run(
        executor._build_visit_bridge_context(
            _context("https://pts.example.com/project/product/66b5f01ca5e0003e906d55e7")
        )
    )

    assert calls[0]["operationName"] == "ProductInfoByID"
    assert calls[0]["variables"]["id"] == "66b5f01ca5e0003e906d55e7"
    assert bridge["delivery_id"] == "66a3080301fb92e1766b54a3"
    assert bridge["pts_link"] == "https://pts.example.com/project/66a3080301fb92e1766b54a3#base"
    assert bridge["product_id_hint"] == "security_product.safeline"
    assert bridge["product_name_hint"] == "下一代Web应用防火墙（雷池20系列）"
    assert bridge["delivery_resolution_source"] == "product_info_graphql"


def test_proactive_bridge_accepts_direct_delivery_project_url(monkeypatch) -> None:
    executor = ProactiveExecutor(settings=Settings(pts_base_url="https://pts.example.com", pts_browser_profile_enabled=False))

    async def fail_query(payload):  # pragma: no cover - should not be called
        raise AssertionError("GraphQL should not run for direct delivery URLs")

    monkeypatch.setattr(executor, "_query_pts_graphql_payload", fail_query)
    monkeypatch.setattr(executor, "_query_pts_graphql_payload_via_browser", fail_query)

    bridge = asyncio.run(
        executor._build_visit_bridge_context(
            _context("https://pts.example.com/project/66a3080301fb92e1766b54a3#base")
        )
    )

    assert bridge["delivery_id"] == "66a3080301fb92e1766b54a3"
    assert bridge["delivery_resolution_source"] == "delivery_url"


def test_proactive_bridge_prefers_browser_profile_graphql(monkeypatch, tmp_path) -> None:
    profile_dir = tmp_path / "pts-profile"
    (profile_dir / "Default").mkdir(parents=True)
    (profile_dir / "Default" / "Preferences").write_text("{}", encoding="utf-8")
    executor = ProactiveExecutor(
        settings=Settings(
            pts_base_url="https://pts.example.com",
            pts_cookie_header="",
            pts_browser_profile_dir=str(profile_dir),
            pts_execution_transport="browser_profile",
        )
    )
    calls = []

    async def fake_browser_query(payload):
        calls.append(payload)
        return {
            "productInfoByID": {
                "id": "66b5f01ca5e0003e906d55e7",
                "product_detail": {
                    "product": {"id": "security_product.safeline", "name": "雷池"},
                    "form": {"id": "software", "name": "软件版"},
                },
                "delivery": {"id": "66a3080301fb92e1766b54a3"},
            }
        }

    async def fail_direct(payload):  # pragma: no cover - should not be called
        raise AssertionError("direct cookie GraphQL should not run in browser_profile mode")

    monkeypatch.setattr(executor, "_query_pts_graphql_payload_via_browser_profile", fake_browser_query)
    monkeypatch.setattr(executor, "_query_pts_graphql_payload", fail_direct)

    bridge = asyncio.run(
        executor._build_visit_bridge_context(
            _context("https://pts.example.com/project/product/66b5f01ca5e0003e906d55e7")
        )
    )

    assert calls[0]["operationName"] == "ProductInfoByID"
    assert bridge["delivery_id"] == "66a3080301fb92e1766b54a3"
    assert bridge["delivery_resolution_source"] == "product_info_browser_profile_graphql"
