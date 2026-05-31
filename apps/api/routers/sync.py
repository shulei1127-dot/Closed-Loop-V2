from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from core.db import get_db
from core.exceptions import OperationConflictError, UnsupportedModuleError
from repositories.source_snapshot_repo import SourceSnapshotRepository
from schemas.sync import SyncRunRequest, SyncRunResponse
from services.ops_service import clear_ops_read_cache
from services.sync_service import SyncService


router = APIRouter()


@router.post("/sync/run", response_model=SyncRunResponse)
async def run_sync(request: SyncRunRequest, db: Session = Depends(get_db)) -> SyncRunResponse:
    service = SyncService(db)
    try:
        response = await service.run_sync(request.module_code, request.force, sync_months=request.sync_months)
        clear_ops_read_cache(module_code=request.module_code)
        return response
    except OperationConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except UnsupportedModuleError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/sync/purge/{module_code}")
def purge_old_snapshots(module_code: str, db: Session = Depends(get_db)) -> dict:
    """One-time cleanup: keep only the latest successful snapshot, delete all older ones."""
    repo = SourceSnapshotRepository(db)
    deleted = repo.delete_all_but_latest_for_module(module_code)
    db.commit()
    clear_ops_read_cache(module_code=module_code)
    return {"module_code": module_code, "deleted_snapshots": deleted}
