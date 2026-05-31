from __future__ import annotations

import logging
from typing import Any

from schemas.review import ReviewNormalizedRecord
from schemas.sync import RecognitionResult

logger = logging.getLogger(__name__)

# Mapping from PTS project dict keys to normalized record fields.
# PTS returns camelCase keys; we normalize to snake_case for internal use.
_FIELD_MAP: dict[str, str] = {
    "projectId": "project_id",
    "projectName": "project_name",
    "customerName": "customer_name",
    "deliveryStage": "delivery_stage",
    "stageStatus": "stage_status",
    "afterSalesLeader": "after_sales_leader",
    "assignerName": "assigner_name",
    "assignerUsername": "assigner_username",
    "personInChargeName": "person_in_charge_name",
    "personInChargeUsername": "person_in_charge_username",
    # Alternative / fallback keys (snake_case from extractors)
    "project_id": "project_id",
    "project_name": "project_name",
    "customer_name": "customer_name",
    "delivery_stage": "delivery_stage",
    "stage_status": "stage_status",
    "after_sales_leader": "after_sales_leader",
    "assigner_name": "assigner_name",
    "assigner_username": "assigner_username",
    "person_in_charge_name": "person_in_charge_name",
    "person_in_charge_username": "person_in_charge_username",
}


class ReviewRecognizer:
    """Recognizer for review module.

    Maps PTS project dicts to normalized records with structured fields.
    Unlike DingTalk-based recognizers, PTS data is already semi-structured
    so field inference / fuzzy matching is not needed.
    """

    def recognize(self, raw_columns: list, raw_rows: list[dict]) -> RecognitionResult:
        records: list[dict[str, Any]] = []
        record_statuses: list[str] = []

        for row in raw_rows:
            mapped_data = self._map_row(row)
            normalized = ReviewNormalizedRecord(**mapped_data)
            status = self._evaluate_status(normalized)
            record_statuses.append(status)
            records.append(
                {
                    "source_row_id": str(row.get("project_id") or row.get("projectId") or row.get("row_id", "")),
                    "normalized_data": normalized.model_dump(),
                    "recognition_status": status,
                }
            )

        overall_status = self._summarize_status(record_statuses)
        return RecognitionResult(
            normalized_records=records,
            field_mapping=dict(_FIELD_MAP),
            field_confidence={field: 1.0 for field in ReviewNormalizedRecord.model_fields},
            recognition_status=overall_status,
        )

    @staticmethod
    def _map_row(row: dict[str, Any]) -> dict[str, Any]:
        """Map raw PTS project dict to ReviewNormalizedRecord field names."""
        mapped: dict[str, Any] = {}
        for raw_key, value in row.items():
            normalized_key = _FIELD_MAP.get(raw_key)
            if normalized_key is not None:
                mapped[normalized_key] = value
        return mapped

    @staticmethod
    def _evaluate_status(record: ReviewNormalizedRecord) -> str:
        """Determine recognition status: full, partial, or failed."""
        key_fields = [
            record.project_id,
            record.project_name,
            record.customer_name,
        ]
        filled = sum(1 for field in key_fields if field)
        if filled == len(key_fields):
            return "full"
        if filled > 0:
            return "partial"
        return "failed"

    @staticmethod
    def _summarize_status(statuses: list[str]) -> str:
        """Summarize overall recognition status across all records."""
        if not statuses:
            return "empty"
        if all(s == "full" for s in statuses):
            return "full"
        if any(s == "failed" for s in statuses):
            return "partial"
        return "partial"
