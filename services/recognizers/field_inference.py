from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
from typing import Any, Callable


EMPTY_MARKERS = {
    "",
    "-",
    "--",
    "—",
    "n/a",
    "na",
    "null",
    "none",
    "nil",
    "未填写",
    "未填",
    "空",
    "暂无",
}

URL_PATTERN = re.compile(r"https?://[^\s\"'<>)}\\]+", re.IGNORECASE)
PHONE_PATTERN = re.compile(r"1\d{10}")


@dataclass(frozen=True, slots=True)
class FieldSpec:
    aliases: tuple[str, ...]
    kind: str = "text"
    enum_map: dict[str, str] = field(default_factory=dict)
    allow_empty: bool = False
    normalizer: Callable[[Any], Any] | None = None
    preserve_debug: bool = False


def normalize_column_name(value: Any) -> str:
    text = str(value or "").strip().lower()
    return re.sub(r"[\s_\-:/（）()\[\]【】·]+", "", text)


def is_empty_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        normalized = value.strip().lower()
        return normalized in EMPTY_MARKERS
    return False


def normalize_text(value: Any) -> str | None:
    if is_empty_value(value):
        return None
    return str(value).strip()


def normalize_phone(value: Any) -> str | None:
    text = normalize_text(value)
    if text is None:
        return None
    match = PHONE_PATTERN.search(text)
    return match.group(0) if match else text


def normalize_url(value: Any) -> str | None:
    return _extract_url(value)


def normalize_identifier(value: Any) -> str | None:
    text = normalize_text(value)
    if text is None:
        return None
    if URL_PATTERN.search(text):
        return None
    return text


def normalize_enum(value: Any, enum_map: dict[str, str]) -> str | None:
    text = normalize_text(value)
    if text is None:
        return None
    key = normalize_column_name(text)
    if key in enum_map:
        return enum_map[key]
    return text


def normalize_boolean(value: Any) -> bool | None:
    text = normalize_text(value)
    if text is None:
        return None
    key = normalize_column_name(text)
    truthy = {"是", "true", "1", "已完成", "完成", "完成了", "done", "yes", "y"}
    falsy = {"否", "false", "0", "未完成", "未", "未做", "pending", "no", "n"}
    if key in truthy:
        return True
    if key in falsy:
        return False
    return None


def normalize_field_value(value: Any, spec: FieldSpec) -> Any:
    if spec.normalizer is not None:
        return spec.normalizer(value)
    if spec.kind == "url":
        return normalize_url(value)
    if spec.kind == "id":
        return normalize_identifier(value)
    if spec.kind == "bool":
        return normalize_boolean(value)
    if spec.kind == "enum":
        return normalize_enum(value, spec.enum_map)
    if spec.kind == "phone":
        return normalize_phone(value)
    return normalize_text(value)


def build_field_metadata(
    raw_columns: list[dict[str, Any] | str],
    raw_rows: list[dict[str, Any]],
    field_specs: dict[str, FieldSpec],
) -> tuple[dict[str, str | None], dict[str, float], dict[str, str], dict[str, Any], list[str]]:
    available_columns = _collect_available_columns(raw_columns, raw_rows)
    field_mapping: dict[str, str | None] = {}
    field_confidence: dict[str, float] = {}
    field_evidence: dict[str, str] = {}
    field_samples: dict[str, Any] = {}
    unresolved_fields: list[str] = []

    for field_name, spec in field_specs.items():
        matched_column, confidence, evidence = _match_column(field_name, spec, available_columns, raw_rows)
        field_mapping[field_name] = matched_column
        field_confidence[field_name] = confidence
        field_evidence[field_name] = evidence
        field_samples[field_name] = _sample_value(raw_rows, matched_column)
        if matched_column is None:
            unresolved_fields.append(field_name)

    return field_mapping, field_confidence, field_evidence, field_samples, unresolved_fields


def build_normalized_record(
    *,
    row: dict[str, Any],
    field_mapping: dict[str, str | None],
    field_specs: dict[str, FieldSpec],
) -> tuple[dict[str, Any], dict[str, bool], list[str]]:
    normalized_data: dict[str, Any] = {}
    resolved_fields: dict[str, bool] = {}
    unresolved_fields: list[str] = []

    for field_name, spec in field_specs.items():
        source_column = field_mapping.get(field_name)
        raw_value = row.get(source_column) if source_column else None
        normalized_value = normalize_field_value(raw_value, spec)
        normalized_data[field_name] = normalized_value
        if spec.preserve_debug:
            normalized_data[f"debug_{field_name}_raw"] = _serialize_debug_value(raw_value)
            normalized_data[f"debug_{field_name}_normalized"] = _serialize_debug_value(normalized_value)
        resolved = _is_field_resolved(source_column, raw_value, normalized_value, spec)
        resolved_fields[field_name] = resolved
        if not resolved:
            unresolved_fields.append(field_name)

    return normalized_data, resolved_fields, unresolved_fields


def evaluate_recognition_status(
    *,
    resolved_fields: dict[str, bool],
    key_groups: list[tuple[str, ...]],
) -> str:
    if not key_groups:
        return "full"

    satisfied_groups = sum(1 for group in key_groups if any(resolved_fields.get(field_name, False) for field_name in group))
    customer_name_resolved = any(resolved_fields.get(field_name, False) for field_name in key_groups[0])

    if satisfied_groups == len(key_groups):
        return "full"
    if customer_name_resolved and satisfied_groups >= 2:
        return "partial"
    return "failed"


def summarize_recognition_status(record_statuses: list[str]) -> str:
    if not record_statuses:
        return "failed"
    if all(status == "full" for status in record_statuses):
        return "full"
    if all(status == "failed" for status in record_statuses):
        return "failed"
    return "partial"


def merge_unresolved_fields(*groups: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for item in group:
            if item in seen:
                continue
            seen.add(item)
            merged.append(item)
    return merged


def _collect_available_columns(raw_columns: list[dict[str, Any] | str], raw_rows: list[dict[str, Any]]) -> list[str]:
    columns: list[str] = []
    for item in raw_columns:
        if isinstance(item, str):
            columns.append(item)
            continue
        if isinstance(item, dict):
            candidate = item.get("name") or item.get("title") or item.get("key")
            if isinstance(candidate, str):
                columns.append(candidate)
    for row in raw_rows:
        for key in row:
            if key == "row_id":
                continue
            if key not in columns:
                columns.append(key)
    return columns


def _match_column(
    field_name: str,
    spec: FieldSpec,
    available_columns: list[str],
    raw_rows: list[dict[str, Any]],
) -> tuple[str | None, float, str]:
    normalized_columns = {column: normalize_column_name(column) for column in available_columns}
    alias_tokens = [normalize_column_name(alias) for alias in spec.aliases]

    for index, alias_token in enumerate(alias_tokens):
        for column, normalized_column in normalized_columns.items():
            if normalized_column == alias_token:
                confidence = 1.0 if index == 0 else 0.96
                return column, confidence, f"matched alias `{spec.aliases[index]}`"

    for index, alias_token in enumerate(alias_tokens):
        fuzzy_hits = [
            column
            for column, normalized_column in normalized_columns.items()
            if alias_token and (alias_token in normalized_column or normalized_column in alias_token)
        ]
        if len(fuzzy_hits) == 1:
            return fuzzy_hits[0], 0.88, f"fuzzy matched alias `{spec.aliases[index]}`"

    heuristic_match = _match_by_value_pattern(spec, available_columns, raw_rows)
    if heuristic_match is not None:
        column, evidence = heuristic_match
        return column, 0.72, evidence

    return None, 0.0, f"no reliable match found for `{field_name}`"


def _match_by_value_pattern(
    spec: FieldSpec,
    available_columns: list[str],
    raw_rows: list[dict[str, Any]],
) -> tuple[str, str] | None:
    if not raw_rows:
        return None

    candidates: list[tuple[str, int]] = []
    for column in available_columns:
        values = [row.get(column) for row in raw_rows if column in row]
        score = _score_values(values, spec)
        if score > 0:
            candidates.append((column, score))
    if len(candidates) != 1:
        return None
    column, _ = candidates[0]
    return column, f"matched by {spec.kind} value pattern"


def _score_values(values: list[Any], spec: FieldSpec) -> int:
    non_empty_values = [value for value in values if not is_empty_value(value)]
    if not non_empty_values:
        return 0
    normalized_values = [normalize_field_value(value, spec) for value in non_empty_values]
    matched_values = [value for value in normalized_values if _has_meaningful_value(value)]
    if spec.kind in {"url", "id", "bool", "enum", "phone"} and len(matched_values) == len(non_empty_values):
        return len(matched_values)
    return 0


def _sample_value(raw_rows: list[dict[str, Any]], column: str | None) -> Any:
    if column is None:
        return None
    for row in raw_rows:
        if column not in row:
            continue
        return row.get(column)
    return None


def _has_meaningful_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return True
    if isinstance(value, str):
        return value.strip() != ""
    return True


def _is_field_resolved(
    source_column: str | None,
    raw_value: Any,
    normalized_value: Any,
    spec: FieldSpec,
) -> bool:
    if source_column is None:
        return False
    if spec.allow_empty and source_column:
        return True
    if isinstance(normalized_value, bool):
        return True
    if _has_meaningful_value(normalized_value):
        return True
    return not is_empty_value(raw_value) and _has_meaningful_value(raw_value)


def _extract_url(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, dict):
        for key in ("url", "href", "link", "value", "text"):
            extracted = _extract_url(value.get(key))
            if extracted is not None:
                return extracted
        return None
    if isinstance(value, list):
        for item in value:
            extracted = _extract_url(item)
            if extracted is not None:
                return extracted
        return None
    text = normalize_text(value)
    if text is None:
        return None
    if text.startswith("{") or text.startswith("["):
        try:
            return _extract_url(json.loads(text))
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    match = URL_PATTERN.search(text)
    if match is None:
        return None
    return match.group(0).rstrip('",]}')


def _serialize_debug_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return str(value)
