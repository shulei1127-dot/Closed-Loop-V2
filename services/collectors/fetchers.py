from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urljoin

import httpx

from core.config import get_settings
from services.collectors.dingtalk_parallelv2_decoder import (
    decode_parallelv2_bytes,
    parse_document_data_structure,
)
from services.collectors.source_config import ModuleSourceConfig


class TransportError(RuntimeError):
    error_type = "transport_error"
    http_status: int | None = None

    def __init__(self, message: str, *, http_status: int | None = None) -> None:
        super().__init__(message)
        self.http_status = http_status


class ConfigurationMissingError(TransportError):
    error_type = "configuration_missing"


class AuthenticationFailedError(TransportError):
    error_type = "authentication_failed"


class RequestFailedError(TransportError):
    error_type = "request_failed"


class EmptyResponseError(TransportError):
    error_type = "response_empty"


class PayloadParseError(TransportError):
    error_type = "payload_parse_failed"


class CollectorFetcher(Protocol):
    transport_mode: str

    async def fetch_structured(self, config: ModuleSourceConfig) -> dict[str, Any] | None: ...

    async def fetch_state(self, config: ModuleSourceConfig) -> dict[str, Any] | None: ...


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, dict):
        raise ValueError(f"fixture payload must be an object: {path}")
    return payload


def _extract_path(data: Any, path: str | None) -> Any:
    if not path:
        return data
    current = data
    for segment in path.split("."):
        if isinstance(current, dict):
            if segment not in current:
                raise PayloadParseError(f"path not found: {path}")
            current = current[segment]
            continue
        if isinstance(current, list):
            try:
                index = int(segment)
            except ValueError as exc:
                raise PayloadParseError(f"invalid list path segment `{segment}` in {path}") from exc
            try:
                current = current[index]
            except IndexError as exc:
                raise PayloadParseError(f"list index out of range in {path}") from exc
            continue
        raise PayloadParseError(f"cannot descend into path {path}")
    return current


def _parse_cookie_string(cookie_string: str) -> dict[str, str]:
    cookie_map: dict[str, str] = {}
    for part in cookie_string.split(";"):
        if "=" not in part:
            continue
        name, value = part.split("=", 1)
        cookie_map[name.strip()] = value.strip()
    return cookie_map


def _parse_json_mapping(raw: str, *, label: str) -> dict[str, Any]:
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigurationMissingError(f"{label} must be valid JSON object") from exc
    if not isinstance(parsed, dict):
        raise ConfigurationMissingError(f"{label} must be a JSON object")
    return parsed


class FakePayloadFetcher:
    transport_mode = "fake"

    async def fetch_structured(self, config: ModuleSourceConfig) -> dict[str, Any] | None:
        return self._load_inline_payload(config, "structured_payload")

    async def fetch_state(self, config: ModuleSourceConfig) -> dict[str, Any] | None:
        return self._load_inline_payload(config, "state_payload")

    @staticmethod
    def _load_inline_payload(config: ModuleSourceConfig, key: str) -> dict[str, Any] | None:
        payload = config.extra_config.get(key)
        if payload is None:
            return None
        if not isinstance(payload, dict):
            raise ConfigurationMissingError(f"{config.module_code} fake transport payload `{key}` must be an object")
        return payload


class FixturePayloadFetcher:
    transport_mode = "fixture"

    async def fetch_structured(self, config: ModuleSourceConfig) -> dict[str, Any] | None:
        return self._load_payload(config, "structured_payload", "structured_payload_path")

    async def fetch_state(self, config: ModuleSourceConfig) -> dict[str, Any] | None:
        return self._load_payload(config, "state_payload", "state_payload_path")

    def _load_payload(
        self,
        config: ModuleSourceConfig,
        inline_key: str,
        path_key: str,
    ) -> dict[str, Any] | None:
        inline_payload = config.extra_config.get(inline_key)
        if isinstance(inline_payload, dict):
            return inline_payload
        payload_path = config.resolve_path(path_key)
        if payload_path is None:
            return None
        return _read_json(payload_path)


class DingtalkPayloadFetcher:
    transport_mode = "real"

    def __init__(self) -> None:
        self.settings = get_settings()

    async def fetch_structured(self, config: ModuleSourceConfig) -> dict[str, Any] | None:
        if self._use_parallelv2_binary_mode(config):
            return await self._fetch_parallelv2_structured(config)
        return await self._fetch(config, step="structured")

    async def fetch_state(self, config: ModuleSourceConfig) -> dict[str, Any] | None:
        return await self._fetch(config, step="state")

    async def _fetch(self, config: ModuleSourceConfig, *, step: str) -> dict[str, Any] | None:
        endpoint_key = f"{step}_endpoint"
        endpoint = config.get_extra(endpoint_key)
        if not endpoint:
            return None

        request = self._build_request(config, step=step, endpoint=str(endpoint))
        response = await self._send_request(step=step, request=request)
        if not response.text.strip():
            raise EmptyResponseError(f"{step} response body is empty", http_status=response.status_code)

        try:
            response_data = response.json()
        except ValueError as exc:
            raise PayloadParseError(f"{step} response is not valid JSON") from exc
        if not isinstance(response_data, (dict, list)):
            raise PayloadParseError(f"{step} response must be JSON object or array")

        payload_root = _extract_path(response_data, config.get_extra(f"{step}_response_path"))
        columns = _extract_path(payload_root, config.get_extra(f"{step}_columns_path")) if payload_root is not None else []
        rows = _extract_path(payload_root, config.get_extra(f"{step}_rows_path")) if payload_root is not None else []
        meta = {}
        meta_path = config.get_extra(f"{step}_meta_path")
        if meta_path:
            extracted_meta = _extract_path(payload_root, meta_path)
            if not isinstance(extracted_meta, dict):
                raise PayloadParseError(f"{step} meta path must resolve to an object")
            meta = extracted_meta
        if not isinstance(columns, list):
            raise PayloadParseError(f"{step} columns payload must be a list")
        if not isinstance(rows, list):
            raise PayloadParseError(f"{step} rows payload must be a list")

        return {
            "raw_columns": columns,
            "raw_rows": rows,
            "raw_meta": {
                **meta,
                "transport_mode": self.transport_mode,
                "request_url": request["url"],
                "http_status": response.status_code,
            },
        }

    async def _fetch_parallelv2_structured(self, config: ModuleSourceConfig) -> dict[str, Any]:
        document_endpoint = config.get_extra("structured_endpoint")
        record_count_endpoint = config.get_extra("record_count_endpoint")
        parallelv2_endpoint = config.get_extra("parallelv2_endpoint")
        if not document_endpoint or not record_count_endpoint or not parallelv2_endpoint:
            raise ConfigurationMissingError(
                "parallelV2 collector requires structured_endpoint, record_count_endpoint, and parallelv2_endpoint"
            )

        sheet_id = str(config.get_extra("parallelv2_sheet_id") or "")
        view_id = str(config.get_extra("parallelv2_view_id") or "")
        if not sheet_id or not view_id:
            raise ConfigurationMissingError("parallelV2 collector requires parallelv2_sheet_id and parallelv2_view_id")

        document_request = self._build_request(config, step="structured", endpoint=str(document_endpoint))
        document_response = await self._send_request(step="structured", request=document_request)
        if not document_response.text.strip():
            raise EmptyResponseError("structured response body is empty", http_status=document_response.status_code)
        try:
            document_payload = document_response.json()
        except ValueError as exc:
            raise PayloadParseError("structured response is not valid JSON") from exc
        if not isinstance(document_payload, dict):
            raise PayloadParseError("structured response must be JSON object")

        structure = parse_document_data_structure(document_payload, sheet_id=sheet_id, view_id=view_id)
        access_token = self._extract_parallelv2_access_token(config, document_payload)
        parallelv2_version = self._extract_parallelv2_version(config, document_payload)

        followup_headers = self._build_headers(config, step="parallelv2")
        followup_token_header = str(config.get_extra("parallelv2_token_header", "A-Token"))
        if access_token:
            followup_headers[followup_token_header] = access_token

        record_count_request = self._build_request(config, step="record_count", endpoint=str(record_count_endpoint))
        record_count_request["headers"] = {**record_count_request["headers"], **followup_headers}
        record_count_response = await self._send_request(step="record_count", request=record_count_request)
        try:
            record_count_payload = record_count_response.json()
        except ValueError as exc:
            raise PayloadParseError("record_count response is not valid JSON") from exc

        record_count_value = self._extract_record_count(record_count_payload, config=config)

        parallelv2_request = self._build_request(config, step="parallelv2", endpoint=str(parallelv2_endpoint))
        parallelv2_request["headers"] = {**parallelv2_request["headers"], **followup_headers}
        if parallelv2_version is not None:
            parallelv2_request["params"] = {
                **parallelv2_request["params"],
                "version": parallelv2_version,
            }
        parallelv2_response = await self._send_request(step="parallelv2", request=parallelv2_request)
        if not parallelv2_response.content:
            raise EmptyResponseError("parallelv2 response body is empty", http_status=parallelv2_response.status_code)

        decoded_payload = decode_parallelv2_bytes(parallelv2_response.content, structure=structure)
        return {
            "data_source": "parallelv2_binary",
            "raw_columns": structure.raw_columns,
            "raw_rows": decoded_payload["rows"],
            "raw_meta": {
                "sheet_id": sheet_id,
                "view_id": view_id,
                "record_count": record_count_value,
                "data_source": "parallelv2_binary",
                "parallelv2_version": parallelv2_version,
                "transport_mode": self.transport_mode,
                "document_request_url": document_request["url"],
                "record_count_request_url": record_count_request["url"],
                "parallelv2_request_url": parallelv2_request["url"],
                "document_http_status": document_response.status_code,
                "record_count_http_status": record_count_response.status_code,
                "parallelv2_http_status": parallelv2_response.status_code,
                "decoder": decoded_payload["diagnostics"],
                "document_structure": {
                    "raw_columns": structure.raw_columns,
                    "view_field_ids": structure.view_field_ids,
                    "record_ids_count": len(structure.record_ids),
                },
            },
        }

    def _build_request(self, config: ModuleSourceConfig, *, step: str, endpoint: str) -> dict[str, Any]:
        url = endpoint if endpoint.startswith("http://") or endpoint.startswith("https://") else urljoin(config.source_url, endpoint)
        method = str(config.get_extra(f"{step}_method", "GET")).upper()
        params = config.get_extra(f"{step}_query_params", {})
        json_body = config.get_extra(f"{step}_json_body")
        headers = self._build_headers(config, step=step)
        cookies = self._build_cookies(config)
        timeout = float(config.get_extra("request_timeout_seconds", self.settings.dingtalk_request_timeout_seconds))
        verify_ssl = bool(config.get_extra("verify_ssl", self.settings.dingtalk_verify_ssl))
        return {
            "url": url,
            "method": method,
            "params": params if isinstance(params, dict) else {},
            "json_body": json_body if isinstance(json_body, dict) else None,
            "headers": headers,
            "cookies": cookies,
            "timeout": timeout,
            "verify_ssl": verify_ssl,
        }

    async def _send_request(self, *, step: str, request: dict[str, Any]) -> httpx.Response:
        async with httpx.AsyncClient(
            timeout=request["timeout"],
            verify=request["verify_ssl"],
            cookies=request["cookies"],
        ) as client:
            try:
                response = await client.request(
                    request["method"],
                    request["url"],
                    headers=request["headers"],
                    params=request["params"],
                    json=request["json_body"],
                )
            except httpx.HTTPError as exc:
                raise RequestFailedError(f"{step} request failed: {exc}") from exc

        if response.status_code in {401, 403}:
            raise AuthenticationFailedError(
                f"{step} request authentication failed with status {response.status_code}",
                http_status=response.status_code,
            )
        if response.status_code >= 400:
            raise RequestFailedError(
                f"{step} request failed with status {response.status_code}",
                http_status=response.status_code,
            )
        return response

    def _build_headers(self, config: ModuleSourceConfig, *, step: str) -> dict[str, str]:
        headers: dict[str, str] = {}
        headers.update({str(k): str(v) for k, v in _parse_json_mapping(self.settings.dingtalk_default_headers_json, label="DINGTALK_DEFAULT_HEADERS_JSON").items()})

        static_headers = config.get_extra("static_headers", {})
        if isinstance(static_headers, dict):
            headers.update({str(k): str(v) for k, v in static_headers.items()})
        step_headers = config.get_extra(f"{step}_headers", {})
        if isinstance(step_headers, dict):
            headers.update({str(k): str(v) for k, v in step_headers.items()})

        headers_env_name = config.get_env_name("headers_env")
        headers_env_value = config.get_env_value("headers_env")
        if headers_env_name and not headers_env_value:
            raise ConfigurationMissingError(f"missing required headers env: {headers_env_name}")
        if headers_env_value:
            headers.update({str(k): str(v) for k, v in _parse_json_mapping(headers_env_value, label="headers_env").items()})

        token_env_name = config.get_env_name("token_env")
        token_env_value = config.get_env_value("token_env", self.settings.dingtalk_auth_token)
        if token_env_name and not token_env_value:
            raise ConfigurationMissingError(f"missing required token env: {token_env_name}")
        if token_env_value:
            header_name = str(config.get_extra("token_header", "Authorization"))
            default_prefix = "Bearer " if header_name.lower() == "authorization" else ""
            prefix = str(config.get_extra("token_prefix", default_prefix))
            headers[header_name] = f"{prefix}{token_env_value}" if prefix else token_env_value

        return headers

    def _build_cookies(self, config: ModuleSourceConfig) -> dict[str, str]:
        cookies: dict[str, str] = {}
        cookies.update({str(k): str(v) for k, v in _parse_json_mapping(self.settings.dingtalk_default_cookies_json, label="DINGTALK_DEFAULT_COOKIES_JSON").items()})
        static_cookies = config.get_extra("static_cookies", {})
        if isinstance(static_cookies, dict):
            cookies.update({str(k): str(v) for k, v in static_cookies.items()})

        cookies_env_name = config.get_env_name("cookies_env")
        cookies_env_value = config.get_env_value("cookies_env")
        if cookies_env_name and not cookies_env_value:
            raise ConfigurationMissingError(f"missing required cookies env: {cookies_env_name}")
        if cookies_env_value:
            stripped = cookies_env_value.strip()
            if stripped.startswith("{"):
                cookies.update({str(k): str(v) for k, v in _parse_json_mapping(cookies_env_value, label="cookies_env").items()})
            else:
                cookies.update(_parse_cookie_string(cookies_env_value))
        return cookies

    @staticmethod
    def _use_parallelv2_binary_mode(config: ModuleSourceConfig) -> bool:
        return config.module_code == "visit" and bool(config.get_extra("parallelv2_enabled"))

    @staticmethod
    def _extract_parallelv2_access_token(config: ModuleSourceConfig, payload: dict[str, Any]) -> str | None:
        path = str(config.get_extra("parallelv2_access_token_path", "data.accessToken"))
        token = _extract_path(payload, path)
        if token is None:
            return None
        if not isinstance(token, str):
            raise PayloadParseError("parallelv2 access token path must resolve to a string")
        return token

    @staticmethod
    def _extract_record_count(payload: Any, *, config: ModuleSourceConfig) -> int | None:
        path = str(config.get_extra("record_count_response_path", "result"))
        value = _extract_path(payload, path)
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise PayloadParseError("record_count response path must resolve to a number")
        return int(value)

    @staticmethod
    def _extract_parallelv2_version(config: ModuleSourceConfig, payload: dict[str, Any]) -> int | None:
        path = str(config.get_extra("parallelv2_version_path", "data.documentContent.checkpoint.baseVersion"))
        try:
            value = _extract_path(payload, path)
        except PayloadParseError:
            value = None
        if value is None:
            params = config.get_extra("parallelv2_query_params", {})
            fallback = params.get("version") if isinstance(params, dict) else None
            value = fallback
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise PayloadParseError("parallelv2 version path must resolve to a number")
        return int(value)


def build_fetcher(config: ModuleSourceConfig) -> CollectorFetcher:
    if config.collector_type == "fake":
        return FakePayloadFetcher()
    if config.collector_type == "fixture":
        return FixturePayloadFetcher()
    if config.collector_type in {"dingtalk", "real"}:
        return DingtalkPayloadFetcher()
    raise ValueError(f"unsupported collector_type: {config.collector_type}")
