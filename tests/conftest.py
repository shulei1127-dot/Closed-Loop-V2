import base64
import os
import socket
import subprocess
import time
import uuid
from collections.abc import Generator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import threading

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from apps.api.main import app
from core.db import get_db
from core.runtime_state import runtime_state
from models.base import Base

FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures"


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_database(database_url: str, timeout: float = 30.0) -> None:
    engine = create_engine(database_url, pool_pre_ping=True)
    started_at = time.time()
    while time.time() - started_at < timeout:
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            engine.dispose()
            return
        except Exception:
            time.sleep(1)
    engine.dispose()
    raise RuntimeError(f"database did not become ready within {timeout} seconds: {database_url}")


@pytest.fixture(scope="session")
def postgres_database_url() -> Generator[str, None, None]:
    env_database_url = os.getenv("TEST_DATABASE_URL")
    if env_database_url:
        yield env_database_url
        return

    docker_info = subprocess.run(
        ["docker", "info"],
        check=False,
        capture_output=True,
        text=True,
    )
    if docker_info.returncode != 0:
        pytest.skip("integration tests require TEST_DATABASE_URL or a running Docker daemon")

    container_name = f"closed-loop-v2-test-{uuid.uuid4().hex[:8]}"
    host_port = _find_free_port()
    subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "-d",
            "--name",
            container_name,
            "-e",
            "POSTGRES_DB=closed_loop_v2_test",
            "-e",
            "POSTGRES_USER=postgres",
            "-e",
            "POSTGRES_PASSWORD=postgres",
            "-p",
            f"{host_port}:5432",
            "postgres:16-alpine",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    database_url = f"postgresql+psycopg://postgres:postgres@127.0.0.1:{host_port}/closed_loop_v2_test"
    try:
        _wait_for_database(database_url)
        yield database_url
    finally:
        subprocess.run(["docker", "rm", "-f", container_name], check=False, capture_output=True, text=True)


@pytest.fixture()
def db_session(postgres_database_url: str) -> Generator[Session, None, None]:
    engine = create_engine(postgres_database_url, pool_pre_ping=True)
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


@pytest.fixture()
def client(db_session: Session) -> Generator[TestClient, None, None]:
    def override_get_db() -> Generator[Session, None, None]:
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def clear_runtime_locks() -> Generator[None, None, None]:
    runtime_state.clear()
    try:
        yield
    finally:
        runtime_state.clear()


@pytest.fixture()
def transport_server() -> Generator[dict, None, None]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.server.request_log.append(
                {
                    "path": self.path,
                    "headers": {key: value for key, value in self.headers.items()},
                }
            )
            if self.path.startswith("/structured-auth-error"):
                self.send_response(401)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "auth"}).encode("utf-8"))
                return
            if self.path.startswith("/structured-invalid-json"):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b"not-json")
                return
            if self.path.startswith("/structured-empty-body"):
                self.send_response(200)
                self.end_headers()
                return
            if self.path.startswith("/structured"):
                if self.headers.get("X-Auth-Token") != "transport-token":
                    self.send_response(401)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": "missing auth"}).encode("utf-8"))
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                payload = {
                    "data": {
                        "payload": {
                            "columns": [
                                "客户名称",
                                "PTS链接",
                                "交付单号",
                                "回访人",
                                "回访状态",
                                "回访链接",
                                "回访类型",
                                "回访联系人",
                                "满意度",
                                "反馈备注",
                            ],
                            "rows": [
                                {
                                    "row_id": "visit-transport-001",
                                    "客户名称": "真实传输客户A",
                                    "PTS链接": '{"url":"https://pts.example.com/visit-transport-001","text":"https://pts.example.com/visit-transport-001"}',
                                    "交付单号": "DEL-TRANSPORT-001",
                                    "回访人": '[{"id":"2747525037","name":"舒磊","realName":"舒磊","data-type":"mention"}]',
                                    "回访状态": "已回访",
                                    "回访链接": "",
                                    "回访类型": "交付回访",
                                    "回访联系人": "王经理",
                                    "满意度": "满意",
                                    "反馈备注": "来自真实 transport",
                                }
                            ],
                            "meta": {"transport": "real"},
                        }
                    }
                }
                self.wfile.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
                return
            if self.path.startswith("/state"):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                payload = {
                    "payload": {
                        "columns": ["客户名称"],
                        "rows": [],
                        "meta": {"transport": "state"},
                    }
                }
                self.wfile.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
                return

            self.send_response(404)
            self.end_headers()

        def log_message(self, format: str, *args) -> None:
            return None

    port = _find_free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.request_log = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield {
            "base_url": f"http://127.0.0.1:{port}",
            "request_log": server.request_log,
        }
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture()
def dingtalk_parallelv2_server() -> Generator[dict, None, None]:
    document_payload = json.loads((FIXTURE_ROOT / "dingtalk" / "visit" / "document_data.json").read_text())
    record_count_payload = json.loads((FIXTURE_ROOT / "dingtalk" / "visit" / "record_count.json").read_text())
    parallelv2_body = (FIXTURE_ROOT / "dingtalk" / "visit" / "parallelv2_base64.txt").read_text().strip()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            self.server.request_log.append(
                {
                    "method": "POST",
                    "path": self.path,
                    "headers": {key: value for key, value in self.headers.items()},
                }
            )
            if self.path == "/api/document/data":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(document_payload, ensure_ascii=False).encode("utf-8"))
                return
            self.send_response(404)
            self.end_headers()

        def do_GET(self) -> None:
            self.server.request_log.append(
                {
                    "method": "GET",
                    "path": self.path,
                    "headers": {key: value for key, value in self.headers.items()},
                }
            )
            if self.path.startswith("/nt/api/sheets/Igz9TVd/record/count"):
                if self.headers.get("A-Token") != "offline-visit-access-token":
                    self.send_response(401)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": "missing document token"}).encode("utf-8"))
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(record_count_payload).encode("utf-8"))
                return
            if self.path.startswith("/nt/api/sheets/Igz9TVd/records/binary/parallelV2"):
                if self.headers.get("A-Token") != "offline-visit-access-token":
                    self.send_response(401)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": "missing document token"}).encode("utf-8"))
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.end_headers()
                self.wfile.write(base64.b64decode(parallelv2_body))
                return
            self.send_response(404)
            self.end_headers()

        def log_message(self, format: str, *args) -> None:
            return None

    port = _find_free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.request_log = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield {
            "base_url": f"http://127.0.0.1:{port}",
            "request_log": server.request_log,
        }
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture()
def visit_real_server() -> Generator[dict, None, None]:
    visit_state: dict[str, dict] = {}
    visit_counter = {"value": 0}

    def _extract(pattern: str, text: str) -> str | None:
        match = re.search(pattern, text)
        return match.group(1) if match else None

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.server.request_log.append(
                {
                    "method": "GET",
                    "path": self.path,
                    "headers": {key: value for key, value in self.headers.items()},
                }
            )
            if self.path.startswith("/pts-login/"):
                self.send_response(302)
                self.send_header("Location", "https://auth.chaitin.net/login")
                self.end_headers()
                return
            if self.path.startswith("/pts/"):
                if self.headers.get("Cookie") != "session=visit-real-cookie":
                    self.send_response(401)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": "unauthorized"}).encode("utf-8"))
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": True}).encode("utf-8"))
                return
            self.send_response(404)
            self.end_headers()

        def do_POST(self) -> None:
            content_length = int(self.headers.get("Content-Length", "0"))
            raw_body = self.rfile.read(content_length) if content_length else b""
            parsed_body = json.loads(raw_body.decode("utf-8") or "{}")
            self.server.request_log.append(
                {
                    "method": "POST",
                    "path": self.path,
                    "headers": {key: value for key, value in self.headers.items()},
                    "body": parsed_body,
                }
            )
            if self.path == "/query":
                if self.headers.get("Cookie") != "session=visit-real-cookie":
                    self.send_response(401)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"errors": [{"message": "unauthorized"}]}).encode("utf-8"))
                    return

                query = parsed_body.get("query", "")
                if "query {" in query and "me {" in query:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"data": {"me": {"id": "669723ae2f6e1a862a49ef16", "name": "舒磊"}}}).encode("utf-8"))
                    return

                if "list_product_delivery" in query:
                    delivery_id = _extract(r'search:\{id:"([^"]+)"\}', query) or "unknown-delivery"
                    payload = {
                        "data": {
                            "list_product_delivery": {
                                "total": 1,
                                "data": [
                                    {
                                        "id": delivery_id,
                                        "project": {
                                            "id": delivery_id,
                                            "name": f"Project-{delivery_id}",
                                            "company": {
                                                "id": f"company-{delivery_id}",
                                                "name": f"Customer-{delivery_id}",
                                                "contact": [
                                                    {
                                                        "id": f"contact-{delivery_id}",
                                                        "name": "王经理",
                                                        "area_code": "010",
                                                        "phone": "123456",
                                                        "email": "demo@example.com",
                                                        "duty": "负责人",
                                                        "meta": {},
                                                    }
                                                ],
                                            },
                                            "product_detail_list": [
                                                {
                                                    "product": {"id": "security_product.xray", "name": "Xray"},
                                                    "form": {"id": "renew", "name": "renew"},
                                                }
                                            ],
                                        },
                                        "visit_data": {"visit_finished": False},
                                    }
                                ],
                            }
                        }
                    }
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps(payload).encode("utf-8"))
                    return

                if "create_visit" in query:
                    delivery_id = _extract(r'delivery_id:"([^"]+)"', query) or "unknown-delivery"
                    visitor_id = _extract(r'visitor_id:"([^"]+)"', query) or "visitor"
                    company_id = _extract(r'company_id:"([^"]+)"', query) or f"company-{delivery_id}"
                    contact_id = _extract(r'contact_id:"([^"]+)"', query) or f"contact-{delivery_id}"
                    visit_type = "client" if "type:client" in query else "delivery"
                    visit_counter["value"] += 1
                    visit_id = f"visit-{visit_counter['value']}"
                    content_id = f"content-{visit_counter['value']}"
                    visit_state[visit_id] = {
                        "id": visit_id,
                        "delivery_id": delivery_id,
                        "company_id": company_id,
                        "visitor_id": visitor_id,
                        "visitor_name": "舒磊",
                        "contact_id": contact_id,
                        "contact_name": "王经理",
                        "type": visit_type,
                        "created_at": f"2026-04-02T15:00:0{visit_counter['value']}Z",
                        "finished": False,
                        "content_id": content_id,
                        "score": None,
                        "feedback_note": "",
                    }
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"data": {"create_visit": None}}).encode("utf-8"))
                    return

                if "list_visit" in query:
                    delivery_id = _extract(r'delivery_id:"([^"]+)"', query)
                    visitor_id = _extract(r'visitor_ids:\["([^"]+)"\]', query)
                    company_id = _extract(r'company_id:"([^"]+)"', query)
                    rows = [
                        {
                            "id": item["id"],
                            "type": item["type"],
                            "finished": item["finished"],
                            "created_at": item["created_at"],
                            "company": {"id": item["company_id"], "name": f"Customer-{item['delivery_id']}"},
                            "visitor": {"id": item["visitor_id"], "name": item["visitor_name"]},
                        }
                        for item in visit_state.values()
                        if (delivery_id is None or item["delivery_id"] == delivery_id)
                        and (visitor_id is None or item["visitor_id"] == visitor_id)
                        and (company_id is None or item["company_id"] == company_id)
                        and item["finished"] is False
                    ]
                    rows.sort(key=lambda item: item["created_at"], reverse=True)
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"data": {"list_visit": {"total": len(rows), "data": rows}}}).encode("utf-8"))
                    return

                if "visit_detail" in query:
                    visit_id = _extract(r'visit_detail\(id:"([^"]+)"\)', query)
                    visit = visit_state.get(visit_id or "")
                    payload = {
                        "data": {
                            "visit_detail": {
                                "id": visit["id"],
                                "type": visit["type"],
                                "finished": visit["finished"],
                                "company": {"id": visit["company_id"], "name": f"Customer-{visit['delivery_id']}"},
                                "visitor": {"id": visit["visitor_id"], "name": visit["visitor_name"]},
                                "contact_list": [
                                    {
                                        "contact": {
                                            "id": visit["contact_id"],
                                            "name": visit["contact_name"],
                                            "area_code": "010",
                                            "phone": "123456",
                                            "email": "demo@example.com",
                                            "duty": "负责人",
                                            "meta": {},
                                        },
                                        "visit_object": True,
                                        "note": "",
                                    }
                                ],
                                "content_list": [
                                    {
                                        "id": visit["content_id"],
                                        "score": visit["score"],
                                        "feedback_note": visit["feedback_note"],
                                        "product_detail": {
                                            "product": {"id": "security_product.xray", "name": "Xray"},
                                            "form": {"id": "renew", "name": "renew"},
                                        },
                                        "delivery_list": [
                                            {
                                                "delivery_id": visit["delivery_id"],
                                                "delivery_type": "product_delivery",
                                                "project": {
                                                    "id": visit["delivery_id"],
                                                    "name": f"Project-{visit['delivery_id']}",
                                                    "company": {"id": visit["company_id"], "name": f"Customer-{visit['delivery_id']}"},
                                                },
                                            }
                                        ],
                                    }
                                ],
                            }
                        }
                    }
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps(payload).encode("utf-8"))
                    return

                if "process_visit" in query:
                    visit_id = _extract(r'process_visit\(\s*id:"([^"]+)"', query) or _extract(r'process_visit\(\s*id:\s*"([^"]+)"', query)
                    score = _extract(r'score:([a-z]+)', query)
                    feedback_note = _extract(r'feedback_note:(".*?")', query)
                    visit = visit_state.get(visit_id or "")
                    if visit is None:
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({"errors": [{"message": "visit not found"}], "data": {"process_visit": None}}).encode("utf-8"))
                        return
                    visit["score"] = score
                    visit["feedback_note"] = json.loads(feedback_note) if feedback_note else ""
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"data": {"process_visit": None}}).encode("utf-8"))
                    return

                if "finish_visit" in query:
                    visit_id = _extract(r'finish_visit\(id:"([^"]+)"\)', query)
                    visit = visit_state.get(visit_id or "")
                    if visit is None:
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({"errors": [{"message": "visit not found"}], "data": {"finish_visit": None}}).encode("utf-8"))
                        return
                    if not visit["score"]:
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({"errors": [{"message": "未完成满意度评分"}], "data": {"finish_visit": None}}).encode("utf-8"))
                        return
                    visit["finished"] = True
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"data": {"finish_visit": None}}).encode("utf-8"))
                    return

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"errors": [{"message": "unsupported query"}]}).encode("utf-8"))
                return

            if self.path.startswith("/visit-work-orders-fail"):
                self.send_response(502)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "upstream failure"}).encode("utf-8"))
                return
            if self.path.endswith("/assign-owner-fail"):
                self.send_response(502)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "assign owner failed"}).encode("utf-8"))
                return
            if self.path.endswith("/mark-target-fail"):
                self.send_response(502)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "mark target failed"}).encode("utf-8"))
                return
            if self.path.endswith("/fill-feedback-fail"):
                self.send_response(502)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "fill feedback failed"}).encode("utf-8"))
                return
            if self.path.endswith("/complete-fail"):
                self.send_response(502)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "complete visit failed"}).encode("utf-8"))
                return
            if self.path.startswith("/visit-work-orders"):
                if self.headers.get("X-Visit-Token") != "visit-real-token":
                    self.send_response(401)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": "unauthorized"}).encode("utf-8"))
                    return
                if self.path.endswith("/assign-owner"):
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"ok": True, "assigned": True}).encode("utf-8"))
                    return
                if self.path.endswith("/mark-target"):
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"ok": True, "marked": True}).encode("utf-8"))
                    return
                if self.path.endswith("/fill-feedback"):
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"ok": True, "feedback_saved": True}).encode("utf-8"))
                    return
                if self.path.endswith("/complete"):
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"ok": True, "completed": True}).encode("utf-8"))
                    return
                self.send_response(201)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(
                    json.dumps(
                        {
                            "data": {
                                "final_link": f"http://127.0.0.1:{port}/work-orders/{parsed_body['delivery_id']}"
                            }
                        }
                    ).encode("utf-8")
                )
                return
            self.send_response(404)
            self.end_headers()

        def log_message(self, format: str, *args) -> None:
            return None

    port = _find_free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.request_log = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield {
            "base_url": f"http://127.0.0.1:{port}",
            "request_log": server.request_log,
        }
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture()
def visit_delivery_backfill_server() -> Generator[dict, None, None]:
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
            if self.path.startswith("/structured"):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                payload = {
                    "data": {
                        "payload": {
                            "columns": [
                                "客户名称",
                                "PTS交付链接",
                                "回访人",
                                "回访状态",
                                "回访链接",
                            ],
                            "rows": [
                                {
                                    "row_id": "visit-backfill-001",
                                    "客户名称": "招商银行股份有限公司信用卡中心",
                                    "PTS交付链接": f"http://127.0.0.1:{self.server.server_port}/project/694bb8f8c1df4508b53003a2#base",
                                    "回访人": '[{"id":"2747525037","name":"舒磊","realName":"舒磊","data-type":"mention"}]',
                                    "回访状态": "已回访",
                                    "回访链接": "",
                                }
                            ],
                            "meta": {"transport": "real"},
                        }
                    }
                }
                self.wfile.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
                return

            self.send_response(404)
            self.end_headers()

        def log_message(self, format: str, *args) -> None:
            return None

    port = _find_free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield {"base_url": f"http://127.0.0.1:{port}"}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture()
def inspection_real_server() -> Generator[dict, None, None]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.server.request_log.append(
                {
                    "method": "GET",
                    "path": self.path,
                    "headers": {key: value for key, value in self.headers.items()},
                }
            )
            if self.path.startswith("/inspection-work-orders/"):
                if self.headers.get("X-Inspection-Token") != "inspection-real-token":
                    self.send_response(401)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": "unauthorized"}).encode("utf-8"))
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": True}).encode("utf-8"))
                return
            self.send_response(404)
            self.end_headers()

        def do_POST(self) -> None:
            content_length = int(self.headers.get("Content-Length", "0"))
            raw_body = self.rfile.read(content_length) if content_length else b""
            work_order_id = self.path.rstrip("/").split("/")[-2] if "/inspection-work-orders/" in self.path else ""
            self.server.request_log.append(
                {
                    "method": "POST",
                    "path": self.path,
                    "headers": {key: value for key, value in self.headers.items()},
                    "body_length": len(raw_body),
                    "content_type": self.headers.get("Content-Type"),
                }
            )
            if self.headers.get("X-Inspection-Token") != "inspection-real-token":
                self.send_response(401)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "unauthorized"}).encode("utf-8"))
                return
            if self.path.endswith("/assign-owner"):
                if work_order_id.startswith("WO-DENIED"):
                    self.send_response(403)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": "permission denied"}).encode("utf-8"))
                    return
                if work_order_id.startswith("WO-MEMBER") and work_order_id not in self.server.added_members:
                    self.send_response(409)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"error_code": "member_missing"}).encode("utf-8"))
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": True, "assigned": True}).encode("utf-8"))
                return
            if self.path.endswith("/add-member"):
                if work_order_id.startswith("WO-DENIED"):
                    self.send_response(403)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": "permission denied"}).encode("utf-8"))
                    return
                self.server.added_members.add(work_order_id)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": True, "member_added": True}).encode("utf-8"))
                return
            if self.path.endswith("/upload-reports-fail"):
                self.send_response(502)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "upload failed"}).encode("utf-8"))
                return
            if self.path.endswith("/complete-fail"):
                self.send_response(502)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "complete failed"}).encode("utf-8"))
                return
            if self.path.endswith("/upload-reports"):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": True, "uploaded": True}).encode("utf-8"))
                return
            if self.path.endswith("/complete"):
                work_order_id = self.path.rstrip("/").split("/")[-2]
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(
                    json.dumps(
                        {
                            "data": {
                                "final_link": f"http://127.0.0.1:{port}/inspection-work-orders/{work_order_id}/completed"
                            }
                        }
                    ).encode("utf-8")
                )
                return
            self.send_response(404)
            self.end_headers()

        def log_message(self, format: str, *args) -> None:
            return None

    port = _find_free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.request_log = []
    server.added_members = set()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield {
            "base_url": f"http://127.0.0.1:{port}",
            "request_log": server.request_log,
        }
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture()
def proactive_real_server() -> Generator[dict, None, None]:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            content_length = int(self.headers.get("Content-Length", "0"))
            raw_body = self.rfile.read(content_length) if content_length else b""
            parsed_body = json.loads(raw_body.decode("utf-8") or "{}")
            self.server.request_log.append(
                {
                    "method": "POST",
                    "path": self.path,
                    "headers": {key: value for key, value in self.headers.items()},
                    "body": parsed_body,
                }
            )
            if self.headers.get("X-Proactive-Token") != "proactive-real-token":
                self.send_response(401)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "unauthorized"}).encode("utf-8"))
                return
            if self.path.startswith("/proactive-work-orders-fail"):
                self.send_response(502)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "create failed"}).encode("utf-8"))
                return
            if self.path.endswith("/assign-owner-fail"):
                self.send_response(502)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "assign owner failed"}).encode("utf-8"))
                return
            if self.path.endswith("/fill-feedback-fail"):
                self.send_response(502)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "fill feedback failed"}).encode("utf-8"))
                return
            if self.path.startswith("/proactive-work-orders"):
                if self.path.endswith("/assign-owner"):
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"ok": True, "assigned": True}).encode("utf-8"))
                    return
                if self.path.endswith("/fill-feedback"):
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"ok": True, "feedback_saved": True}).encode("utf-8"))
                    return
                self.send_response(201)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(
                    json.dumps(
                        {
                            "data": {
                                "final_link": f"http://127.0.0.1:{port}/proactive-work-orders/{parsed_body['product_info_id'] or 'generated-001'}"
                            }
                        }
                    ).encode("utf-8")
                )
                return
            self.send_response(404)
            self.end_headers()

        def log_message(self, format: str, *args) -> None:
            return None

    port = _find_free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.request_log = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield {
            "base_url": f"http://127.0.0.1:{port}",
            "request_log": server.request_log,
        }
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
