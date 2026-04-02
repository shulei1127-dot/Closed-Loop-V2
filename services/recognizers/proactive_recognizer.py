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
        },
    ),
    "visit_link": FieldSpec(aliases=("回访链接", "闭环链接", "工单链接", "回访单链接"), kind="url", allow_empty=True),
    "feedback_note": FieldSpec(aliases=("反馈备注", "备注", "客户反馈", "反馈内容")),
    "contact_name": FieldSpec(aliases=("联系人", "客户联系人", "联络人")),
    "contact_phone": FieldSpec(aliases=("联系电话", "联系人电话", "手机号"), kind="phone"),
    "engineer_name": FieldSpec(aliases=("工程师", "负责人", "对接工程师")),
}

KEY_GROUPS = [
    ("customer_name",),
    ("liaison_status",),
    ("visit_link",),
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
