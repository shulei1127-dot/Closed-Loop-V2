from __future__ import annotations

import base64
import json
import re
import threading
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qsl, urlparse

from services.environment_check import EnvironmentCheckService
from services.pts_session_service import PtsSessionService


LOCAL_TZ = timezone(timedelta(hours=8))
EXTENSION_CLIENT_STATE = {
    "last_seen": None,
    "version": "",
    "reason": "",
    "user_agent": "",
}
EXTENSION_CLIENT_LOCK = threading.Lock()


def _cookies_to_map(cookies: list[dict[str, Any]] | None) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in cookies or []:
        name = str(item.get("name") or "").strip()
        value = str(item.get("value") or "").strip()
        if name:
            result[name] = value
    return result


def _cookies_to_header(cookies: list[dict[str, Any]] | None) -> str:
    parts: list[str] = []
    for item in cookies or []:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        if item.get("value") is None:
            continue
        parts.append(f"{name}={item['value']}")
    return "; ".join(parts)


def _merge_cookie_lists(*cookie_lists: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for cookie_list in cookie_lists:
        for item in cookie_list or []:
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            merged[name] = item
    return list(merged.values())


def _decode_jwt_payload(token: str | None) -> dict[str, Any]:
    parts = str(token or "").split(".")
    if len(parts) < 2:
        return {}
    payload = parts[1]
    payload += "=" * (-len(payload) % 4)
    try:
        decoded = base64.urlsafe_b64decode(payload.encode("utf-8")).decode("utf-8")
        parsed = json.loads(decoded)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _extract_cookie_value_from_text(text: str | None, cookie_name: str) -> str:
    text_value = str(text or "")
    pattern = re.compile(rf"(?:^|[;,\s]){re.escape(cookie_name)}=([^;,\s]+)")
    match = pattern.search(text_value)
    return match.group(1).strip() if match else ""


def _extract_query_value(url: str | None, candidate_names: list[str]) -> str:
    try:
        query_items = parse_qsl(urlparse(str(url or "")).query, keep_blank_values=True)
    except Exception:
        return ""
    candidate_name_set = {name.lower() for name in candidate_names}
    for key, value in reversed(query_items):
        if key.lower() in candidate_name_set and str(value).strip():
            return str(value).strip()
    return ""


def _extract_network_cookie_value(
    network_events: list[dict[str, Any]] | None,
    cookie_name: str,
    *,
    header_names: list[str] | None = None,
    query_names: list[str] | None = None,
) -> str:
    header_names = header_names or []
    query_names = query_names or [cookie_name]
    for event in reversed(network_events or []):
        headers = event.get("headers") or {}
        for header_name in header_names:
            value = _extract_cookie_value_from_text(headers.get(header_name, ""), cookie_name)
            if value:
                return value
        value = _extract_query_value(event.get("url", ""), query_names)
        if value:
            return value
    return ""


def _summarize_sources(sources: dict[str, Any]) -> list[str]:
    summary: list[str] = []
    for source_name, label in (("pts", "PTS"), ("auth", "Auth")):
        source = sources.get(source_name) or {}
        tab_state = source.get("tabState") or {}
        cookies = source.get("cookies") or []
        cookie_names = [str(item.get("name") or "").strip() for item in cookies if item.get("name")]
        cookie_names = [name for name in cookie_names if name]
        summary.append(f"{label}: {'已找到标签页' if tab_state.get('found') else '未找到标签页'}")
        if cookie_names:
            summary.append(f"{label} Cookies: {', '.join(cookie_names[:6])}")
        network_events = ((sources.get("network") or {}).get(source_name) or [])
        if network_events:
            summary.append(f"{label} Network Events: {len(network_events)}")
    return summary


def _extract_pts_credentials_from_sources(sources: dict[str, Any]) -> dict[str, str]:
    pts_source = sources.get("pts") or {}
    auth_source = sources.get("auth") or {}
    pts_cookies = pts_source.get("cookies") or []
    auth_cookies = auth_source.get("cookies") or []
    network = sources.get("network") or {}
    pts_network = (network.get("pts") or []) + (network.get("auth") or [])
    combined_cookies = _merge_cookie_lists(pts_cookies, auth_cookies)
    pts_tab_state = pts_source.get("tabState") or {}
    auth_tab_state = auth_source.get("tabState") or {}
    pts_cookie_map = _cookies_to_map(combined_cookies)
    auth_cookie_map = _cookies_to_map(auth_cookies)
    pts_document_cookie = str(pts_tab_state.get("cookie") or "").strip()
    auth_document_cookie = str(auth_tab_state.get("cookie") or "").strip()

    c_value = (
        pts_cookie_map.get("c")
        or _extract_cookie_value_from_text(pts_document_cookie, "c")
        or _extract_network_cookie_value(pts_network, "c", header_names=["cookie", "set-cookie"], query_names=["c", "ptsc"])
    )
    s_value = (
        auth_cookie_map.get("s")
        or _extract_cookie_value_from_text(auth_document_cookie, "s")
        or _extract_network_cookie_value(pts_network, "s", header_names=["cookie", "set-cookie"], query_names=["s", "ptss"])
    )
    ct_auth_value = (
        pts_cookie_map.get("_ct_auth")
        or _extract_cookie_value_from_text(pts_document_cookie, "_ct_auth")
        or _extract_network_cookie_value(pts_network, "_ct_auth", header_names=["cookie", "set-cookie"], query_names=["_ct_auth"])
    )

    pts_cookie_parts: list[str] = []
    if c_value:
        pts_cookie_parts.append(f"c={c_value}")
    if ct_auth_value:
        pts_cookie_parts.append(f"_ct_auth={ct_auth_value}")
    if pts_cookie_parts:
        pts_cookie_header = "; ".join(pts_cookie_parts)
    else:
        pts_cookie_header = _cookies_to_header(combined_cookies)

    payload = _decode_jwt_payload(ct_auth_value)
    return {
        "pts": pts_cookie_header,
        "ptsc": f"c={c_value}" if c_value else "",
        "ptss": f"s={s_value}" if s_value else "",
        "ptsusername": str(payload.get("sub") or "").strip(),
        "user_agent": (
            str(pts_tab_state.get("userAgent") or "").strip()
            or str(auth_tab_state.get("userAgent") or "").strip()
            or str((sources.get("browser") or {}).get("userAgent") or "").strip()
        ),
    }


def build_extension_api_payload(payload: dict[str, Any], status_code: int = 200) -> tuple[dict[str, Any], int, dict[str, str]]:
    return (
        payload,
        status_code,
        {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type",
            "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
            "Cache-Control": "no-store",
        },
    )


class BrowserExtensionAuthService:
    def __init__(self, pts_session_service: PtsSessionService | None = None) -> None:
        self.pts_session_service = pts_session_service or PtsSessionService()

    def update_extension_client_state(self, payload: dict[str, Any] | None) -> None:
        with EXTENSION_CLIENT_LOCK:
            EXTENSION_CLIENT_STATE["last_seen"] = datetime.now(LOCAL_TZ)
            EXTENSION_CLIENT_STATE["version"] = str((payload or {}).get("extension_version") or "").strip()
            EXTENSION_CLIENT_STATE["reason"] = str((payload or {}).get("reason") or "").strip()
            EXTENSION_CLIENT_STATE["user_agent"] = str((payload or {}).get("user_agent") or "").strip()

    def get_extension_connection_state(self) -> dict[str, Any]:
        with EXTENSION_CLIENT_LOCK:
            last_seen = EXTENSION_CLIENT_STATE.get("last_seen")
            version = EXTENSION_CLIENT_STATE.get("version", "")
            reason = EXTENSION_CLIENT_STATE.get("reason", "")
        if not last_seen:
            return {
                "status": "disconnected",
                "label": "未连接",
                "detail": "还没有收到浏览器扩展心跳。",
                "version": version,
                "reason": reason,
                "last_seen": "",
            }
        elapsed_seconds = max(0, int((datetime.now(LOCAL_TZ) - last_seen).total_seconds()))
        if elapsed_seconds <= 300:
            status = "connected"
            label = "已连接"
        elif elapsed_seconds <= 3600:
            status = "idle"
            label = "长时间未活动"
        else:
            status = "disconnected"
            label = "连接已过期"
        return {
            "status": status,
            "label": label,
            "detail": f"最近一次扩展心跳：{elapsed_seconds} 秒前，触发来源：{reason or '未知'}。",
            "version": version,
            "reason": reason,
            "last_seen": last_seen.isoformat(),
        }

    def collect_pts_auth(self, payload: dict[str, Any] | None) -> dict[str, Any]:
        scope = str((payload or {}).get("scope") or "pts_only").strip() or "pts_only"
        sources = (payload or {}).get("sources") or {}
        source_summary = _summarize_sources(sources)
        collected = _extract_pts_credentials_from_sources(sources)

        missing_fields = [field for field in ("pts", "ptsc", "ptss") if not collected.get(field)]
        unresolved_fields: list[str] = []
        if not collected.get("ptsusername"):
            unresolved_fields.append("PTS Username")

        if collected.get("pts"):
            session_status = self.pts_session_service.update_auth_bundle(
                cookie_header=collected["pts"],
                source="browser_extension",
                pts_username=collected.get("ptsusername") or None,
            )
        else:
            session_status = self.pts_session_service.get_status()

        env_report = EnvironmentCheckService().build_report()
        visit_report = (((env_report.get("real_execution") or {}).get("modules") or {}).get("visit") or {})

        health_check = {
            "overall": "ok" if session_status.get("configured") else "warning",
            "items": [
                {
                    "title": "PTS 会话",
                    "status": "ok" if session_status.get("configured") else "warning",
                    "status_label": "可用" if session_status.get("configured") else "待补充",
                    "detail": "已通过浏览器扩展接入当前本机 Chrome PTS 会话。"
                    if session_status.get("configured")
                    else "浏览器扩展未成功提取到可用 PTS 会话。",
                    "fields": ["PTS Cookie"] if not session_status.get("configured") else [],
                },
                {
                    "title": "Visit Real Execution",
                    "status": "ok" if visit_report.get("ok") else "warning",
                    "status_label": "可用" if visit_report.get("ok") else "待补充",
                    "detail": "visit 模块实时执行环境已就绪。"
                    if visit_report.get("ok")
                    else f"缺少字段：{', '.join(visit_report.get('missing_fields') or []) or '未知'}",
                    "fields": list(visit_report.get("missing_fields") or []),
                },
            ],
        }
        health_summary = [
            f"PTS：{'可用' if session_status.get('configured') else '待补充'}",
            f"Visit：{'可用' if visit_report.get('ok') else '待补充'}",
        ]

        collected_fields = []
        if collected.get("pts"):
            collected_fields.append("PTS Cookie")
        if collected.get("ptsc"):
            collected_fields.append("PTSC")
        if collected.get("ptss"):
            collected_fields.append("PTSS")
        if collected.get("ptsusername"):
            collected_fields.append("PTS Username")

        auth_export = {
            "schema_version": "1.0",
            "auth": {
                "pts": {
                    "cookie": collected.get("pts", ""),
                    "ptsc": collected.get("ptsc", ""),
                    "ptss": collected.get("ptss", ""),
                    "username": collected.get("ptsusername", ""),
                },
                "headers": {
                    "user_agent": collected.get("user_agent", ""),
                },
            },
        }

        success = not missing_fields
        message = "PTS 浏览器会话采集完成，已写入 Closed Loop V2 本地配置。"
        if missing_fields:
            message = f"PTS 已部分采集，但仍缺少：{'、'.join(missing_fields)}"
        elif unresolved_fields:
            message = f"PTS 会话已写入，但仍建议确认：{'、'.join(unresolved_fields)}"

        return {
            "success": success,
            "service": "closed-loop-v2",
            "scope": scope,
            "message": message,
            "source_summary": source_summary,
            "health_check": health_check,
            "health_summary": health_summary,
            "highlighted_fields": missing_fields,
            "collected_fields": collected_fields,
            "missing_fields": missing_fields,
            "unresolved_fields": unresolved_fields,
            "extension_connection": self.get_extension_connection_state(),
            "auth_export_available": bool(collected.get("pts")),
            "auth_export_fields": collected_fields,
            "auth_export_json": json.dumps(auth_export, ensure_ascii=False, indent=2),
            "pts_session": session_status,
        }

    def build_extension_status(self) -> dict[str, Any]:
        session_status = self.pts_session_service.get_status()
        raw_values = self.pts_session_service._read_env_values()
        env_report = EnvironmentCheckService().build_report()
        visit_report = (((env_report.get("real_execution") or {}).get("modules") or {}).get("visit") or {})
        auth_export = {
            "schema_version": "1.0",
            "auth": {
                "pts": {
                    "cookie": raw_values.get("PTS_COOKIE_HEADER", ""),
                    "ptsc": "",
                    "ptss": "",
                    "username": raw_values.get("PTS_USERNAME_HINT", ""),
                },
                "headers": {
                    "user_agent": "",
                },
            },
        }
        return {
            "success": True,
            "service": "closed-loop-v2",
            "execution_mode": "closed_loop_v2",
            "execution_mode_label": "Closed Loop V2",
            "message": "Closed Loop V2 浏览器扩展接入状态。",
            "health_check": {
                "overall": "ok" if session_status.get("configured") else "warning",
                "items": [
                    {
                        "title": "PTS 会话",
                        "status": "ok" if session_status.get("configured") else "warning",
                        "status_label": "可用" if session_status.get("configured") else "待补充",
                        "detail": "当前已存在可用 PTS 会话。" if session_status.get("configured") else "尚未接入可用 PTS 会话。",
                        "fields": [] if session_status.get("configured") else ["PTS Cookie"],
                    }
                ],
            },
            "health_summary": [f"PTS：{'可用' if session_status.get('configured') else '待补充'}"],
            "highlighted_fields": [] if session_status.get("configured") else ["PTS Cookie"],
            "extension_connection": self.get_extension_connection_state(),
            "pts_session": session_status,
            "browser_session_available": bool(visit_report.get("browser_session_available")),
            "auth_export_available": bool(session_status.get("configured")),
            "auth_export_fields": ["PTS Cookie"] if session_status.get("configured") else [],
            "auth_export_json": json.dumps(auth_export, ensure_ascii=False, indent=2),
        }
