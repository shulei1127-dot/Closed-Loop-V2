from schemas.inspection import InspectionNormalizedRecord
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
    "work_order_link": FieldSpec(aliases=("工单链接", "工单地址", "链接", "任务链接"), kind="url"),
    "work_order_id": FieldSpec(aliases=("工单ID", "工单编号", "工单号", "work_order_id"), kind="id"),
    "inspection_done": FieldSpec(aliases=("巡检是否完成", "巡检完成", "是否完成", "完成状态"), kind="bool"),
    "report_match_name": FieldSpec(aliases=("报告匹配名", "报告名称", "报告文件名")),
}

KEY_GROUPS = [
    ("customer_name",),
    ("inspection_done",),
    ("work_order_link", "work_order_id"),
]


class InspectionRecognizer:
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

            normalized = InspectionNormalizedRecord(**normalized_data)
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
