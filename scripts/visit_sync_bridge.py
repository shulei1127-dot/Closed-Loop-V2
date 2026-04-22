from __future__ import annotations

import copy
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import requests

from services.collectors.dingtalk_browser_auth import resolve_dingtalk_browser_auth


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STRUCTURE_PATH = PROJECT_ROOT / "tests" / "fixtures" / "dingtalk" / "visit" / "document_data.json"
HOST = "127.0.0.1"
PORT = 8011


def _load_structure_payload() -> dict:
    with STRUCTURE_PATH.open("r", encoding="utf-8") as fh:
        payload = json.load(fh)
    if not isinstance(payload, dict):
        raise ValueError("visit structure payload must be an object")
    return payload


def _fetch_access_token() -> str:
    auth = resolve_dingtalk_browser_auth(source_url="https://alidocs.dingtalk.com")
    response = requests.get(
        "https://alidocs.dingtalk.com/core/api/accessToken",
        headers=auth.get("headers") or {},
        cookies=auth.get("cookies") or {},
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    token = payload.get("data") or payload.get("token")
    if not isinstance(token, str) or not token.strip():
        raise RuntimeError("failed to fetch DingTalk access token")
    return token


class VisitSyncBridgeHandler(BaseHTTPRequestHandler):
    server_version = "VisitSyncBridge/1.0"

    def do_GET(self) -> None:  # noqa: N802
        try:
            if self.path.startswith("/visit/document-data"):
                self._handle_document_data()
                return
            if self.path.startswith("/visit/record-count"):
                self._handle_record_count()
                return
            self._write_json({"ok": False, "error": "not_found"}, status=HTTPStatus.NOT_FOUND)
        except Exception as exc:  # pragma: no cover - operational path
            self._write_json(
                {
                    "ok": False,
                    "error": "bridge_error",
                    "message": str(exc),
                },
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return

    def _handle_document_data(self) -> None:
        payload = copy.deepcopy(_load_structure_payload())
        payload.setdefault("data", {})
        payload["data"]["accessToken"] = _fetch_access_token()
        self._write_json(payload)

    def _handle_record_count(self) -> None:
        self._write_json({"result": 829})

    def _write_json(self, payload: dict, *, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), VisitSyncBridgeHandler)
    print(f"visit sync bridge listening on http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
