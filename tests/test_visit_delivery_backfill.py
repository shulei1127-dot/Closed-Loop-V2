import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from services.planners.visit_planner import VisitPlanner
from services.recognizers.visit_delivery_backfill import (
    VisitDeliveryIdBackfill,
    extract_delivery_id_from_text,
    extract_pts_project_id,
)


def test_extract_pts_project_id_from_pts_link() -> None:
    assert extract_pts_project_id("https://pts.chaitin.net/project/694bb8f8c1df4508b53003a2#base") == "694bb8f8c1df4508b53003a2"
    assert extract_pts_project_id("https://pts.chaitin.net/project/694bb8f8c1df4508b53003a2") == "694bb8f8c1df4508b53003a2"
    assert extract_pts_project_id("https://pts.chaitin.net/return-visit/detail/123") is None


def test_extract_delivery_id_from_text() -> None:
    delivery_id, raw = extract_delivery_id_from_text('{"project":{"deliveryId":"DEL-BACKFILL-001"}}')
    assert delivery_id == "DEL-BACKFILL-001"
    assert raw == '"deliveryId":"DEL-BACKFILL-001"'


def test_visit_delivery_id_backfill_uses_pts_project_id_directly(monkeypatch) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path.startswith("/project/694bb8f8c1df4508b53003a2"):
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(
                    '<html><body><script>window.__DATA={"deliveryId":"DEL-BACKFILL-001"}</script></body></html>'.encode(
                        "utf-8"
                    )
                )
                return
            self.send_response(404)
            self.end_headers()

        def log_message(self, format: str, *args) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base_url = f"http://127.0.0.1:{server.server_port}"
        monkeypatch.setenv("PTS_BASE_URL", base_url)
        monkeypatch.setenv("PTS_COOKIE_HEADER", "session=pts-backfill-cookie")
        from core.config import get_settings

        get_settings.cache_clear()
        enricher = VisitDeliveryIdBackfill(get_settings())
        records = [
            {
                "source_row_id": "visit-backfill-001",
                "normalized_data": {
                    "customer_name": "招商银行股份有限公司信用卡中心",
                    "pts_link": f"{base_url}/project/694bb8f8c1df4508b53003a2#base",
                    "delivery_id": None,
                    "visit_owner": "舒磊",
                    "visit_status": "已回访",
                    "visit_link": None,
                },
                "recognition_status": "full",
            }
        ]

        enriched = asyncio.run(enricher.enrich_records(records))
        data = enriched[0]["normalized_data"]

        assert data["delivery_id"] == "694bb8f8c1df4508b53003a2"
        assert data["debug_pts_project_id"] == "694bb8f8c1df4508b53003a2"
        assert data["debug_delivery_id_source"] == "pts_link_project_id"
        assert data["debug_delivery_id_normalized"] == "694bb8f8c1df4508b53003a2"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        from core.config import get_settings

        get_settings.cache_clear()


def test_visit_planner_requires_delivery_id_after_backfill() -> None:
    planner = VisitPlanner()
    result = planner.plan(
        [
            {
                "source_row_id": "visit-no-delivery",
                "recognition_status": "full",
                "normalized_data": {
                    "customer_name": "客户A",
                    "delivery_id": None,
                    "visit_owner": "舒磊",
                    "visit_status": "已回访",
                    "visit_link": None,
                },
            },
            {
                "source_row_id": "visit-with-delivery",
                "recognition_status": "full",
                "normalized_data": {
                    "customer_name": "客户B",
                    "delivery_id": "694bb8f8c1df4508b53003a2",
                    "visit_owner": "舒磊",
                    "visit_status": "已回访",
                    "visit_link": None,
                },
            },
        ]
    )

    assert [item.plan_status for item in result] == ["skipped", "planned"]
