from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from core.db import get_db
from core.exceptions import OperationConflictError, ResourceNotFoundError, UnsupportedModuleError
from schemas.sync import SyncRerunRequest
from services.sync_service import SyncService


router = APIRouter()


@router.get("/modules/summary")
def module_summary(db: Session = Depends(get_db)) -> dict:
    service = SyncService(db)
    return {
        "ok": True,
        "items": [item.model_dump() for item in service.build_module_summaries()],
    }


@router.get("/modules/{module_code}/latest")
def module_latest(module_code: str, db: Session = Depends(get_db)) -> dict:
    service = SyncService(db)
    try:
        return {
            "ok": True,
            "item": service.get_latest_module_summary(module_code).model_dump(),
        }
    except UnsupportedModuleError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ResourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/modules/{module_code}/sync/rerun")
async def module_sync_rerun(
    module_code: str,
    request: SyncRerunRequest,
    db: Session = Depends(get_db),
) -> dict:
    service = SyncService(db)
    try:
        response = await service.run_sync(module_code, trigger="rerun", sync_months=request.sync_months)
        return response.model_dump()
    except OperationConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except UnsupportedModuleError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ResourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
