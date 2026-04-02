import json

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
    for key in ("realName", "name", "title", "text"):
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return None


FIELD_SPECS = {
    "customer_name": FieldSpec(aliases=("客户名称", "客户名", "企业名称", "公司名称")),
    "pts_link": FieldSpec(
        aliases=("PTS链接", "PTS交付链接", "PTS地址", "PTS", "交付链接"),
        kind="url",
        preserve_debug=True,
    ),
    "delivery_id": FieldSpec(aliases=("交付单号", "交付单编号", "交付编号", "delivery_id"), kind="id"),
    "visit_owner": FieldSpec(
        aliases=("回访人", "回访负责人", "负责人", "owner"),
        normalizer=_normalize_visit_owner,
        preserve_debug=True,
    ),
    "visit_status": FieldSpec(
        aliases=("回访状态", "状态", "回访结果", "完成状态"),
        kind="enum",
        enum_map={
            "已回访": "已回访",
            "回访完成": "已回访",
            "完成回访": "已回访",
            "待回访": "待回访",
            "未回访": "待回访",
        },
    ),
    "visit_link": FieldSpec(
        aliases=("回访链接", "闭环链接", "工单链接", "回访单链接"),
        kind="url",
        allow_empty=True,
        preserve_debug=True,
    ),
    "visit_type": FieldSpec(aliases=("回访类型", "类型", "工单类型")),
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
    "feedback_note": FieldSpec(aliases=("反馈备注", "备注", "客户反馈", "反馈内容")),
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
