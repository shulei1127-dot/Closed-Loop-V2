from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from services.browser_extension_auth_service import BrowserExtensionAuthService, build_extension_api_payload


router = APIRouter()


def _json_response(payload: dict, status_code: int = 200) -> JSONResponse:
    data, resolved_status, headers = build_extension_api_payload(payload, status_code)
    return JSONResponse(content=data, status_code=resolved_status, headers=headers)


@router.options("/extension/status")
@router.options("/extension/ping")
@router.options("/extension/collect-auth")
async def extension_preflight() -> JSONResponse:
    return _json_response({"ok": True})


@router.get("/extension/status")
async def extension_status() -> JSONResponse:
    service = BrowserExtensionAuthService()
    return _json_response(service.build_extension_status())


@router.post("/extension/ping")
async def extension_ping(request: Request) -> JSONResponse:
    payload = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    service = BrowserExtensionAuthService()
    service.update_extension_client_state(payload if isinstance(payload, dict) else {})
    return _json_response(
        {
            "success": True,
            "message": "扩展心跳已记录。",
            "extension_connection": service.get_extension_connection_state(),
        }
    )


@router.post("/extension/collect-auth")
async def extension_collect_auth(request: Request) -> JSONResponse:
    payload = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    service = BrowserExtensionAuthService()
    try:
        result = service.collect_pts_auth(payload if isinstance(payload, dict) else {})
        return _json_response(result)
    except Exception as exc:
        return _json_response(
            {
                "success": False,
                "message": str(exc),
                "health_check": None,
                "health_summary": [],
                "highlighted_fields": [],
                "collected_fields": [],
                "extension_connection": service.get_extension_connection_state(),
            },
            500,
        )
