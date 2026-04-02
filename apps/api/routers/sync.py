from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from core.db import get_db
from core.exceptions import OperationConflictError, UnsupportedModuleError
from schemas.sync import SyncRunRequest, SyncRunResponse
from services.sync_service import SyncService


router = APIRouter()


@router.post("/sync/run", response_model=SyncRunResponse)
async def run_sync(request: SyncRunRequest, db: Session = Depends(get_db)) -> SyncRunResponse:
    service = SyncService(db)
    try:
        return await service.run_sync(request.module_code, request.force)
    except OperationConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except UnsupportedModuleError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
