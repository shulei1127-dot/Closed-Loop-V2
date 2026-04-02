from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any


class DingtalkParallelV2DecodeError(ValueError):
    pass


@dataclass(slots=True)
class DingtalkDocumentStructure:
    sheet_id: str
    view_id: str
    field_name_by_id: dict[str, str]
    field_type_by_id: dict[str, str]
    field_enum_label_by_id: dict[str, dict[str, str]]
    view_field_ids: list[str]
    raw_columns: list[str]
    record_ids: list[str]


def parse_document_data_structure(payload: dict[str, Any], *, sheet_id: str, view_id: str) -> DingtalkDocumentStructure:
    content = _extract_document_content(payload)
    sheet_map = content.get("sheetMap")
    if not isinstance(sheet_map, dict):
        raise DingtalkParallelV2DecodeError("document/data missing sheetMap")
    sheet_payload = sheet_map.get(sheet_id)
    if not isinstance(sheet_payload, dict):
        raise DingtalkParallelV2DecodeError(f"sheetId not found in document/data: {sheet_id}")

    field_map = sheet_payload.get("fieldMap")
    if not isinstance(field_map, dict):
        raise DingtalkParallelV2DecodeError(f"sheet `{sheet_id}` missing fieldMap")

    view_map = sheet_payload.get("viewMap")
    if not isinstance(view_map, dict):
        raise DingtalkParallelV2DecodeError(f"sheet `{sheet_id}` missing viewMap")
    view_payload = view_map.get(view_id)
    if not isinstance(view_payload, dict):
        raise DingtalkParallelV2DecodeError(f"viewId not found in document/data: {view_id}")

    raw_view_columns = view_payload.get("columns")
    if not isinstance(raw_view_columns, list):
        raise DingtalkParallelV2DecodeError(f"view `{view_id}` missing columns")

    view_field_ids: list[str] = []
    raw_columns: list[str] = []
    field_name_by_id: dict[str, str] = {}
    field_type_by_id: dict[str, str] = {}
    field_enum_label_by_id: dict[str, dict[str, str]] = {}

    for field_id, field_payload in field_map.items():
        if not isinstance(field_payload, dict):
            continue
        field_name = str(field_payload.get("name") or field_id)
        field_name_by_id[str(field_id)] = field_name
        field_type_by_id[str(field_id)] = str(field_payload.get("type") or "")
        field_enum_label_by_id[str(field_id)] = _build_enum_mapping(field_payload)

    for column in raw_view_columns:
        if isinstance(column, str):
            field_id = column
        elif isinstance(column, dict):
            field_id = str(column.get("fieldId") or column.get("id") or "")
        else:
            continue
        if not field_id:
            continue
        view_field_ids.append(field_id)
        raw_columns.append(field_name_by_id.get(field_id, field_id))

    record_ids = [str(item) for item in sheet_payload.get("recordIds", []) if isinstance(item, str)]
    return DingtalkDocumentStructure(
        sheet_id=sheet_id,
        view_id=view_id,
        field_name_by_id=field_name_by_id,
        field_type_by_id=field_type_by_id,
        field_enum_label_by_id=field_enum_label_by_id,
        view_field_ids=view_field_ids,
        raw_columns=raw_columns,
        record_ids=record_ids,
    )


def decode_parallelv2_base64(base64_body: str, *, structure: DingtalkDocumentStructure) -> dict[str, Any]:
    try:
        raw_bytes = base64.b64decode(base64_body)
    except Exception as exc:  # pragma: no cover - defensive
        raise DingtalkParallelV2DecodeError("parallelV2 base64 body is invalid") from exc
    return decode_parallelv2_bytes(raw_bytes, structure=structure)


def decode_parallelv2_bytes(raw_bytes: bytes, *, structure: DingtalkDocumentStructure) -> dict[str, Any]:
    row_messages = [value for field_number, wire_type, value in _parse_message(raw_bytes) if field_number == 1 and wire_type == 2]
    rows: list[dict[str, Any]] = []
    field_ids_seen: set[str] = set()
    field_names_seen: set[str] = set()
    unknown_value_types: set[int] = set()
    undecoded_cells = 0

    def _increment_undecoded() -> None:
        nonlocal undecoded_cells
        undecoded_cells += 1

    # Decode rows after the counter closure is available.
    for row_message in row_messages:
        row = _decode_row_message(
            row_message,
            structure=structure,
            field_ids_seen=field_ids_seen,
            field_names_seen=field_names_seen,
            unknown_value_types=unknown_value_types,
            undecoded_counter=_increment_undecoded,
        )
        if row is not None:
            rows.append(row)

    return {
        "rows": rows,
        "raw_record_count": len(row_messages),
        "field_keys": sorted(field_names_seen),
        "diagnostics": {
            "decoder": "dingtalk_parallelv2",
            "sheet_id": structure.sheet_id,
            "view_id": structure.view_id,
            "row_message_count": len(row_messages),
            "decoded_row_count": len(rows),
            "field_ids_seen": sorted(field_ids_seen),
            "unknown_value_types": sorted(unknown_value_types),
            "undecoded_cells": undecoded_cells,
        },
    }


def _decode_row_message(
    row_message: bytes,
    *,
    structure: DingtalkDocumentStructure,
    field_ids_seen: set[str],
    field_names_seen: set[str],
    unknown_value_types: set[int],
    undecoded_counter,
) -> dict[str, Any] | None:
    row_id: str | None = None
    row: dict[str, Any] = {}
    for field_number, wire_type, value in _parse_message(row_message):
        if field_number == 1 and wire_type == 2:
            row_id = _safe_decode_utf8(value)
            continue
        if field_number != 2 or wire_type != 2:
            continue
        field_id, decoded_value, decoded_type = _decode_cell_message(value, structure=structure)
        if not field_id:
            continue
        field_ids_seen.add(field_id)
        field_name = structure.field_name_by_id.get(field_id, field_id)
        field_names_seen.add(field_name)
        if decoded_type is not None:
            unknown_value_types.add(decoded_type)
        if decoded_value is None:
            undecoded_counter()
            continue
        row[field_name] = decoded_value

    if row_id is None:
        return None
    return {"row_id": row_id, **row}


def _decode_cell_message(cell_message: bytes, *, structure: DingtalkDocumentStructure) -> tuple[str | None, Any, int | None]:
    field_id: str | None = None
    wrapper: bytes | None = None
    for field_number, wire_type, value in _parse_message(cell_message):
        if field_number == 5 and wire_type == 2:
            field_id = _safe_decode_utf8(value)
        elif field_number == 3 and wire_type == 2:
            wrapper = value
    if not field_id or wrapper is None:
        return field_id, None, None

    type_code: int | None = None
    payload: bytes | None = None
    extra_varints: dict[int, int] = {}
    for field_number, wire_type, value in _parse_message(wrapper):
        if field_number == 1 and wire_type == 0:
            type_code = int(value)
        elif field_number == 2 and wire_type == 2:
            payload = value
        elif wire_type == 0:
            extra_varints[field_number] = int(value)

    if type_code is None:
        return field_id, None, None
    decoded_value = _decode_wrapper_value(
        type_code=type_code,
        payload=payload,
        extra_varints=extra_varints,
        field_id=field_id,
        structure=structure,
    )
    return field_id, decoded_value, type_code if decoded_value is None else None


def _decode_wrapper_value(
    *,
    type_code: int,
    payload: bytes | None,
    extra_varints: dict[int, int],
    field_id: str,
    structure: DingtalkDocumentStructure,
) -> Any:
    field_type = structure.field_type_by_id.get(field_id)
    enum_map = structure.field_enum_label_by_id.get(field_id, {})

    if type_code == 1:
        return _safe_decode_utf8(payload or b"")

    if type_code == 2:
        if payload:
            text = _safe_decode_utf8(payload)
            return text or None
        # Date-like wrapper in the sampled document stores the actual timestamp in other fields.
        # Keep a stable textual representation instead of leaking raw protobuf internals.
        if extra_varints:
            return json.dumps(extra_varints, ensure_ascii=False, sort_keys=True)
        return None

    if type_code == 6:
        if not payload:
            return None
        raw_text = _safe_decode_utf8(payload)
        if not raw_text:
            return None
        try:
            raw_data = json.loads(raw_text)
        except json.JSONDecodeError:
            return raw_text

        if field_type == "link":
            if isinstance(raw_data, dict):
                return raw_data.get("url") or raw_data.get("text") or raw_data.get("data") or raw_text
            return raw_text

        if field_type == "select":
            option_id = None
            if isinstance(raw_data, dict):
                option_id = str(raw_data.get("data") or raw_data.get("identifier") or raw_data.get("sequence") or "")
            return enum_map.get(option_id, option_id or raw_text)

        if field_type == "multiSelect":
            option_ids: list[str] = []
            if isinstance(raw_data, dict):
                data_value = raw_data.get("data")
                if isinstance(data_value, str):
                    try:
                        parsed = json.loads(data_value)
                    except json.JSONDecodeError:
                        parsed = [data_value]
                elif isinstance(data_value, list):
                    parsed = data_value
                else:
                    parsed = []
                option_ids = [str(item) for item in parsed]
            labels = [enum_map.get(item, item) for item in option_ids]
            return "、".join([item for item in labels if item]) if labels else raw_text

        if isinstance(raw_data, dict):
            return raw_data.get("data") or raw_data.get("text") or raw_data.get("url") or raw_text
        return raw_text

    if payload:
        text = _safe_decode_utf8(payload)
        if text:
            return text
    return None


def _extract_document_content(payload: dict[str, Any]) -> dict[str, Any]:
    if "sheetMap" in payload:
        if not isinstance(payload["sheetMap"], dict):
            raise DingtalkParallelV2DecodeError("document content sheetMap must be an object")
        return payload

    content = (
        payload.get("data", {})
        .get("documentContent", {})
        .get("checkpoint", {})
        .get("content")
    )
    if isinstance(content, dict):
        return content
    if not isinstance(content, str) or not content.strip():
        raise DingtalkParallelV2DecodeError("document/data missing checkpoint content")
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise DingtalkParallelV2DecodeError("document/data checkpoint content is not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise DingtalkParallelV2DecodeError("document/data checkpoint content must be an object")
    return parsed


def _build_enum_mapping(field_payload: dict[str, Any]) -> dict[str, str]:
    props = (((field_payload.get("config") or {}).get("renderFieldConfig") or {}).get("props") or {})
    enums = props.get("enums")
    mapping: dict[str, str] = {}
    if not isinstance(enums, list):
        return mapping
    for item in enums:
        if not isinstance(item, dict):
            continue
        option_id = str(item.get("id") or "")
        option_label = str(item.get("value") or item.get("label") or option_id)
        if option_id:
            mapping[option_id] = option_label
    return mapping


def _safe_decode_utf8(value: bytes) -> str:
    return value.decode("utf-8", "ignore")


def _parse_message(payload: bytes) -> list[tuple[int, int, Any]]:
    index = 0
    fields: list[tuple[int, int, Any]] = []
    while index < len(payload):
        key, index = _read_varint(payload, index)
        field_number = key >> 3
        wire_type = key & 0x07
        if wire_type == 0:
            value, index = _read_varint(payload, index)
        elif wire_type == 1:
            if index + 8 > len(payload):
                raise DingtalkParallelV2DecodeError("invalid 64-bit protobuf field length")
            value = payload[index : index + 8]
            index += 8
        elif wire_type == 2:
            length, index = _read_varint(payload, index)
            if index + length > len(payload):
                raise DingtalkParallelV2DecodeError("invalid length-delimited protobuf field length")
            value = payload[index : index + length]
            index += length
        elif wire_type == 5:
            if index + 4 > len(payload):
                raise DingtalkParallelV2DecodeError("invalid 32-bit protobuf field length")
            value = payload[index : index + 4]
            index += 4
        else:
            raise DingtalkParallelV2DecodeError(f"unsupported protobuf wire type: {wire_type}")
        fields.append((field_number, wire_type, value))
    return fields


def _read_varint(payload: bytes, index: int) -> tuple[int, int]:
    shift = 0
    result = 0
    while True:
        if index >= len(payload):
            raise DingtalkParallelV2DecodeError("unexpected end of protobuf varint")
        current = payload[index]
        index += 1
        result |= (current & 0x7F) << shift
        if not (current & 0x80):
            return result, index
        shift += 7
        if shift > 63:
            raise DingtalkParallelV2DecodeError("protobuf varint is too large")
