import uuid

from sqlalchemy import select

from models.normalized_record import NormalizedRecord
from repositories.base import BaseRepository
from schemas.sync import RecognitionResult


class NormalizedRecordRepository(BaseRepository):
    def create_from_recognition(
        self,
        snapshot_id: str,
        module_code: str,
        recognition_result: RecognitionResult,
    ) -> dict[str, NormalizedRecord]:
        records: dict[str, NormalizedRecord] = {}
        for item in recognition_result.normalized_records:
            record = NormalizedRecord(
                snapshot_id=snapshot_id,
                module_code=module_code,
                source_row_id=item["source_row_id"],
                customer_name=item.get("customer_name"),
                normalized_data=item["normalized_data"],
                field_mapping=recognition_result.field_mapping,
                field_confidence=recognition_result.field_confidence,
                field_evidence=recognition_result.field_evidence,
                field_samples=recognition_result.field_samples,
                unresolved_fields=recognition_result.unresolved_fields,
                recognition_status=item.get("recognition_status", recognition_result.recognition_status),
            )
            self.db.add(record)
            records[record.source_row_id] = record
        self.db.flush()
        return records

    def list_by_filters(self, module_code: str | None, snapshot_id: str | None) -> list[NormalizedRecord]:
        statement = select(NormalizedRecord).order_by(NormalizedRecord.created_at.desc())
        if module_code:
            statement = statement.where(NormalizedRecord.module_code == module_code)
        if snapshot_id:
            statement = statement.where(NormalizedRecord.snapshot_id == snapshot_id)
        return list(self.db.scalars(statement).all())

    def get_by_id(self, record_id: uuid.UUID) -> NormalizedRecord | None:
        statement = select(NormalizedRecord).where(NormalizedRecord.id == record_id)
        return self.db.scalar(statement)

    def get_by_ids(self, record_ids: list[uuid.UUID]) -> dict[uuid.UUID, NormalizedRecord]:
        if not record_ids:
            return {}
        statement = select(NormalizedRecord).where(NormalizedRecord.id.in_(record_ids))
        return {record.id: record for record in self.db.scalars(statement).all()}
