from fastapi import Depends
from sqlalchemy.orm import Session

from core.db import get_db
from services.ops_service import OpsService
from services.sync_service import SyncService
from services.task_execution_service import TaskExecutionService


def get_sync_service(db: Session = Depends(get_db)) -> SyncService:
    return SyncService(db)


def get_task_execution_service(db: Session = Depends(get_db)) -> TaskExecutionService:
    return TaskExecutionService(db)


def get_ops_service(db: Session = Depends(get_db)) -> OpsService:
    return OpsService(db)
