import uuid

from fastapi import APIRouter, Depends, Query
from fastapi import HTTPException
from sqlalchemy.orm import Session

from core.db import get_db
from core.exceptions import ResourceNotFoundError
from repositories.normalized_record_repo import NormalizedRecordRepository
from schemas.common import RecordItem
from schemas.record import RecordDetailResponse, RecordListResponse
from services.sync_service import SyncService


router = APIRouter()


@router.get("/records", response_model=RecordListResponse)
def list_records(
    module_code: str | None = Query(default=None),
    snapshot_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> RecordListResponse:
    repo = NormalizedRecordRepository(db)
    items = repo.list_by_filters(module_code=module_code, snapshot_id=snapshot_id)
    return RecordListResponse(
        items=[
            RecordItem(
                record_id=str(item.id),
                snapshot_id=str(item.snapshot_id),
                module_code=item.module_code,
                source_row_id=item.source_row_id,
                customer_name=item.customer_name,
                normalized_data=item.normalized_data,
                field_mapping=item.field_mapping,
                field_confidence=item.field_confidence,
                recognition_status=item.recognition_status,
            )
            for item in items
        ]
    )


@router.get("/records/{record_id}", response_model=RecordDetailResponse)
def get_record(record_id: uuid.UUID, db: Session = Depends(get_db)) -> RecordDetailResponse:
    service = SyncService(db)
    try:
        return RecordDetailResponse(item=service.get_record_detail(record_id))
    except ResourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
