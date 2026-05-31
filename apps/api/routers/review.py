from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from core.config import get_settings
from core.db import get_db
from core.exceptions import OperationConflictError, UnsupportedModuleError
from schemas.sync import SyncRunRequest, SyncRunResponse
from services.ops_service import clear_ops_read_cache
from services.sync_service import SyncService

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/review/sync/run", response_model=SyncRunResponse)
async def run_review_sync(request: SyncRunRequest, db: Session = Depends(get_db)) -> SyncRunResponse:
    service = SyncService(db)
    try:
        response = await service.run_sync("review", request.force)
        clear_ops_read_cache(module_code="review")
        return response
    except OperationConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except UnsupportedModuleError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/review/status")
def review_status(db: Session = Depends(get_db)) -> dict[str, Any]:
    settings = get_settings()
    from services.collectors.review_collector import ReviewCollector
    from services.module_registry import get_module_definition
    from services.executors.review_executor import ReviewExecutor

    module_def = get_module_definition("review")
    from repositories.module_config_repo import ModuleConfigRepository
    source_config = ModuleConfigRepository(db).get_source_config("review")
    collector = ReviewCollector(source_config) if source_config else None
    executor = ReviewExecutor()

    return {
        "ok": True,
        "module_code": "review",
        "module_name": module_def.get("module_name", ""),
        "collector_health": collector.healthcheck() if collector else {"ok": False, "reason": "source_config not found"},
        "executor_health": executor.healthcheck(),
        "pipeline_enabled": settings.review_pipeline_enabled,
        "real_execution_enabled": settings.review_real_execution_enabled,
        "writeback_enabled": settings.review_writeback_enabled,
    }