import uuid

from fastapi import APIRouter, Depends, Query
from fastapi import HTTPException
from sqlalchemy.orm import Session

from core.db import get_db
from core.exceptions import ResourceNotFoundError
from repositories.source_snapshot_repo import SourceSnapshotRepository
from schemas.common import SnapshotItem
from schemas.snapshot import SnapshotDetailResponse, SnapshotListResponse
from services.sync_service import SyncService


router = APIRouter()


@router.get("/snapshots", response_model=SnapshotListResponse)
def list_snapshots(
    module_code: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    ) -> SnapshotListResponse:
    repo = SourceSnapshotRepository(db)
    items = repo.list_recent(module_code=module_code, limit=limit)
    return SnapshotListResponse(
        items=[
            SnapshotItem(
                snapshot_id=str(item.id),
                module_code=item.module_code,
                sync_time=item.sync_time,
                sync_status=item.sync_status,
                data_source=item.data_source,
                row_count=item.row_count,
            )
            for item in items
        ]
    )


@router.get("/snapshots/{snapshot_id}", response_model=SnapshotDetailResponse)
def get_snapshot(snapshot_id: uuid.UUID, db: Session = Depends(get_db)) -> SnapshotDetailResponse:
    service = SyncService(db)
    try:
        return SnapshotDetailResponse(item=service.get_snapshot_detail(snapshot_id))
    except ResourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
