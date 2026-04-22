import json
from datetime import datetime, timezone

from schemas.sync import RecognitionResult
from schemas.visit import VisitNormalizedRecord
from services.recognizers.field_inference import (
    FieldSpec,
    build_field_metadata,
    build_normalized_record,
    evaluate_recognition_status,
    merge_unresolved_fields,
    summarize_recognition_status,
)


DINGTALK_UID_NAME_MAP = {
    # Current visit sheet stores the real visit owner as a DingTalk user id
    # in the parallelV2 cell payload. Keep this narrow so delivery assignees
    # are not accidentally treated as visit owners.
    "2747525037": "舒磊",
}


def _normalize_visit_owner(value):
    if value is None:
        return None
    if isinstance(value, list):
        names = [_extract_mention_name(item) for item in value]
        names = [name for name in names if name]
        return "、".join(dict.fromkeys(names)) or None
    if isinstance(value, dict):
        name = _extract_mention_name(value)
        if name:
            return name
        user_id = value.get("uid") or value.get("userId") or value.get("user_id") or value.get("6")
        if user_id is not None:
            return DINGTALK_UID_NAME_MAP.get(str(user_id), str(user_id))
    text = str(value).strip()
    if not text:
        return None
    if text.startswith("[") or text.startswith("{"):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                user_id = parsed.get("uid") or parsed.get("userId") or parsed.get("user_id") or parsed.get("6")
                if user_id is not None:
                    return DINGTALK_UID_NAME_MAP.get(str(user_id), str(user_id))
            return _normalize_visit_owner(parsed)
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
    nested_data = value.get("data")
    if isinstance(nested_data, str) and nested_data.strip().startswith(("{", "[")):
        try:
            return _extract_mention_name(json.loads(nested_data))
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
    if isinstance(nested_data, dict):
        return _extract_mention_name(nested_data)
    return None


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


def _normalize_due_text(value):
    if value is None:
        return None
    if isinstance(value, dict):
        millis = value.get("3") or value.get("timestamp") or value.get("date")
        if isinstance(millis, (int, float)):
            return datetime.fromtimestamp(millis / 1000, tz=timezone.utc).date().isoformat()
    text = str(value).strip()
    if text.startswith("{"):
        try:
            return _normalize_due_text(json.loads(text))
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    return text or None


FIELD_SPECS = {
    "customer_name": FieldSpec(aliases=("客户名称", "客户名", "企业名称", "公司名称")),
    "pts_link": FieldSpec(
        aliases=("PTS链接", "PTS交付链接", "PTS地址", "PTS", "交付链接"),
        kind="url",
        preserve_debug=True,
    ),
    "delivery_id": FieldSpec(aliases=("交付单号", "交付单编号", "交付编号", "delivery_id"), kind="id"),
    "visit_owner": FieldSpec(
        aliases=("回访人", "回访负责人", "owner"),
        normalizer=_normalize_visit_owner,
        preserve_debug=True,
    ),
    "visit_status": FieldSpec(
        aliases=("回访状态", "状态", "回访结果", "完成状态"),
        kind="enum",
        enum_map={
            "已回访": "已回访",
            "khlT6gz2Ab": "已回访",
            "回访完成": "已回访",
            "完成回访": "已回访",
            "XsfOG0cfXM": "不用回访",
            "不用回访": "不用回访",
            "yFf8EjlZfW": "审核拒绝",
            "审核拒绝": "审核拒绝",
            "U6CKROpuOs": "跟进中",
            "跟进中": "跟进中",
            "待回访": "待回访",
            "未回访": "待回访",
        },
    ),
    "visit_link": FieldSpec(
        aliases=("回访链接", "闭环链接", "工单链接", "回访单链接"),
        normalizer=_normalize_visit_link,
        allow_empty=True,
        preserve_debug=True,
    ),
    "visit_type": FieldSpec(
        aliases=("回访类型", "类型", "工单类型"),
        kind="enum",
        enum_map={
            "F0DThMHIqf": "交付满意度评价",
            "交付满意度评价": "交付满意度评价",
            "Xwqaxv3hxC": "客户满意度调研",
            "客户满意度调研": "客户满意度调研",
        },
    ),
    "visit_contact": FieldSpec(aliases=("回访联系人", "联系人", "客户联系人")),
    "satisfaction": FieldSpec(
        aliases=("满意度", "满意情况", "客户满意度", "PTS选择的满意度", "pts选择的满意度"),
        kind="enum",
        enum_map={
            "十分满意": "十分满意",
            "非常满意": "十分满意",
            "满意": "满意",
            "一般": "一般",
            "不满意": "不满意",
            "非常不满意": "非常不满意",
            "十分不满意": "非常不满意",
        },
    ),
    "feedback_note": FieldSpec(aliases=("jxdh8qmijds6w92j5szwb", "反馈备注", "备注", "客户反馈", "反馈内容")),
    "due_date_primary": FieldSpec(
        aliases=("审核日期", "开始回访时间", "售后服务有效期"),
        normalizer=_normalize_due_text,
    ),
    "due_date_secondary": FieldSpec(
        aliases=("8q5udpxjhanq5eyn0k98a", "售后有效服务期异常"),
        normalizer=_normalize_due_text,
    ),
}

KEY_GROUPS = [
    ("customer_name",),
    ("visit_owner",),
    ("visit_status",),
    ("visit_link",),
]


class VisitRecognizer:
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

            normalized = VisitNormalizedRecord(**normalized_data)
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
