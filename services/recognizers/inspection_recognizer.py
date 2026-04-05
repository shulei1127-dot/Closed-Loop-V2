import json
from datetime import datetime
from zoneinfo import ZoneInfo

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

LOCAL_TZ = ZoneInfo("Asia/Shanghai")


def _normalize_month(value):
    if value is None:
        return None
    if isinstance(value, dict):
        timestamp = value.get("3")
        if isinstance(timestamp, (int, float)):
            return datetime.fromtimestamp(timestamp / 1000, tz=LOCAL_TZ).strftime("%Y-%m")
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.startswith("{"):
        try:
            return _normalize_month(json.loads(text))
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
    text = text.replace("年", "-").replace("月", "")
    try:
        if len(text) == 7:
            return datetime.strptime(text, "%Y-%m").strftime("%Y-%m")
        if len(text) == 10:
            return datetime.strptime(text, "%Y-%m-%d").strftime("%Y-%m")
    except ValueError:
        return None
    return None


def _normalize_executor_name(value):
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
            return _normalize_executor_name(json.loads(text))
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
    "inspection_month": FieldSpec(
        aliases=("启动月份", "启动 月份", "月份", "服务月份"),
        normalizer=_normalize_month,
        preserve_debug=True,
    ),
    "customer_name": FieldSpec(aliases=("客户名称", "客户名", "公司名称", "企业名称")),
    "service_type": FieldSpec(aliases=("增值服务类型1", "增值服务类型", "服务类型")),
    "executor_name": FieldSpec(
        aliases=("执行人", "负责人", "处理人"),
        normalizer=_normalize_executor_name,
        preserve_debug=True,
    ),
    "work_order_link": FieldSpec(
        aliases=("巡检工单链接", "工单链接", "工单地址", "链接", "任务链接"),
        kind="url",
        preserve_debug=True,
    ),
    "work_order_id": FieldSpec(aliases=("工单ID", "工单编号", "工单号", "work_order_id"), kind="id"),
    "inspection_done": FieldSpec(aliases=("巡检是否完成", "巡检完成", "是否完成", "完成状态"), kind="bool"),
    "work_order_closed": FieldSpec(
        aliases=("工单是否闭环", "巡检工单是否闭环", "是否闭环"),
        kind="bool",
        preserve_debug=True,
    ),
    "report_match_name": FieldSpec(aliases=("报告匹配名", "报告名称", "报告文件名")),
    "remark": FieldSpec(aliases=("备注", "预约备注", "巡检备注")),
}

KEY_GROUPS = [
    ("customer_name",),
    ("executor_name",),
    ("inspection_done",),
    ("work_order_link", "work_order_id"),
    ("work_order_closed",),
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
