import json

from schemas.proactive import ProactiveNormalizedRecord
from schemas.sync import RecognitionResult
from services.recognizers.field_inference import (
    FieldSpec,
    build_field_metadata,
    build_normalized_record,
    evaluate_recognition_status,
    merge_unresolved_fields,
    summarize_recognition_status,
)


def _normalize_visit_owner(value):
    if value is None:
        return None
    if isinstance(value, list):
        names = [_extract_mention_name(item) for item in value]
        names = [name for name in names if name]
        return "、".join(dict.fromkeys(names)) or None
    if isinstance(value, dict):
        return _extract_mention_name(value)
    text = str(value).strip()
    if not text:
        return None
    if text.startswith("[") or text.startswith("{"):
        try:
            return _normalize_visit_owner(json.loads(text))
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    return text


def _extract_mention_name(value):
    if not isinstance(value, dict):
        return None
    for key in ("realName", "name", "nick", "title", "text", "sequence", "displayName"):
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    # DWS CLI returns {"corpId": "...", "userId": "lei.shu"} without name fields
    # Extract userId as fallback (e.g. "lei.shu" → "Lei Shu")
    user_id = value.get("userId")
    if isinstance(user_id, str) and user_id.strip():
        return _format_user_id(user_id.strip())
    nested_data = value.get("data")
    if isinstance(nested_data, str) and nested_data.strip().startswith(("{", "[")):
        try:
            return _extract_mention_name(json.loads(nested_data))
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
    if isinstance(nested_data, dict):
        return _extract_mention_name(nested_data)
    return None


def _format_user_id(user_id: str) -> str:
    """Convert dingtalk userId like 'lei.shu' or 'min.zhang01' to readable name."""
    # userId format: "firstname.lastname" or "firstname.lastname01"
    # Convert dot-separated parts, capitalize first letter, strip numeric suffix
    parts = user_id.split(".")
    formatted_parts = []
    for part in parts:
        # Strip trailing digits (e.g. "zhang01" → "zhang")
        stripped = part.rstrip("0123456789")
        if stripped:
            formatted_parts.append(stripped.capitalize())
    return " ".join(formatted_parts) if formatted_parts else user_id


def _normalize_visit_link(value):
    if value is None:
        return None
    if isinstance(value, list):
        for item in value:
            normalized = _normalize_visit_link(item)
            if normalized:
                return normalized
        return None
    if isinstance(value, dict):
        for key in ("url", "href", "link", "value", "text"):
            candidate = value.get(key)
            normalized = _normalize_visit_link(candidate)
            if normalized:
                return normalized
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.startswith("[") or text.startswith("{"):
        try:
            return _normalize_visit_link(json.loads(text))
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    return text


FIELD_SPECS = {
    "customer_name": FieldSpec(aliases=("客户名称", "客户名", "公司名称", "企业名称")),
    "product_link": FieldSpec(aliases=("产品链接", "产品地址", "产品页面"), kind="url"),
    "product_info_id": FieldSpec(aliases=("产品信息ID", "产品ID", "信息ID"), kind="id"),
    "liaison_status": FieldSpec(
        aliases=("客户建联状态", "建联状态", "联系状态"),
        kind="enum",
        enum_map={
            "已建联": "已建联",
            "已联系": "已建联",
            "建联完成": "已建联",
            "未建联": "未建联",
            "未联系": "未建联",
            "不用回访": "不用回访",
            "售后断联": "售后断联",
        },
    ),
    "visit_link": FieldSpec(
        aliases=("回访链接", "闭环链接", "工单链接", "回访单链接"),
        normalizer=_normalize_visit_link,
        allow_empty=True,
    ),
    "visit_owner": FieldSpec(aliases=("回访人", "回访负责人", "负责人"), normalizer=_normalize_visit_owner),
    "feedback_note": FieldSpec(
        aliases=("备注（异常详情+其他备注）", "异常详情+其他备注", "反馈备注", "备注", "客户反馈", "反馈内容")
    ),
    "contact_name": FieldSpec(aliases=("联系人", "客户联系人", "联络人")),
    "contact_phone": FieldSpec(aliases=("联系电话", "联系人电话", "手机号"), kind="phone"),
    "engineer_name": FieldSpec(aliases=("工程师", "对接工程师")),
}

KEY_GROUPS = [
    ("customer_name",),
    ("liaison_status",),
    ("visit_link",),
    ("visit_owner",),
    ("feedback_note",),
]


class ProactiveRecognizer:
    def recognize(self, raw_columns: list, raw_rows: list[dict]) -> RecognitionResult:
        field_mapping, field_confidence, field_evidence, field_samples, unresolved_fields = build_field_metadata(
            raw_columns,
            raw_rows,
            FIELD_SPECS,
        )

        records: list[dict] = []
        row_unresolved_fields: list[str] = []
        record_statuses: list[str] = []

        for row in raw_rows:
            normalized_data, resolved_fields, row_unresolved = build_normalized_record(
                row=row,
                field_mapping=field_mapping,
                field_specs=FIELD_SPECS,
            )
            row_status = evaluate_recognition_status(
                resolved_fields=resolved_fields,
                key_groups=KEY_GROUPS,
            )
            record_statuses.append(row_status)
            row_unresolved_fields = merge_unresolved_fields(row_unresolved_fields, row_unresolved)

            normalized = ProactiveNormalizedRecord(**normalized_data)
            records.append(
                {
                    "source_row_id": row.get("row_id", ""),
                    "customer_name": normalized.customer_name,
                    "normalized_data": normalized.model_dump(),
                    "recognition_status": row_status,
                }
            )

        return RecognitionResult(
            normalized_records=records,
            field_mapping=field_mapping,
            field_confidence=field_confidence,
            field_evidence=field_evidence,
            field_samples=field_samples,
            unresolved_fields=merge_unresolved_fields(unresolved_fields, row_unresolved_fields),
            recognition_status=summarize_recognition_status(record_statuses),
        )
