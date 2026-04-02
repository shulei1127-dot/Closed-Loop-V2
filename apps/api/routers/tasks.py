import uuid

from fastapi import APIRouter, Depends, Query
from fastapi import HTTPException
from sqlalchemy.orm import Session

from apps.api.deps import get_task_execution_service
from core.db import get_db
from core.exceptions import OperationConflictError, ResourceNotFoundError
from repositories.task_plan_repo import TaskPlanRepository
from schemas.task import TaskDetailResponse, TaskExecuteRequest, TaskListResponse, TaskRunResponse
from schemas.common import TaskItem
from services.sync_service import SyncService
from services.task_execution_service import TaskExecutionService


router = APIRouter()


@router.get("/tasks", response_model=TaskListResponse)
def list_tasks(
    module_code: str | None = Query(default=None),
    status: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> TaskListResponse:
    repo = TaskPlanRepository(db)
    items = repo.list_by_filters(module_code=module_code, status=status)
    return TaskListResponse(
        items=[
            TaskItem(
                task_plan_id=str(item.id),
                module_code=item.module_code,
                normalized_record_id=str(item.normalized_record_id),
                task_type=item.task_type,
                eligibility=item.eligibility,
                skip_reason=item.skip_reason,
                planner_version=item.planner_version,
                plan_status=item.plan_status,
                planned_payload=item.planned_payload,
            )
            for item in items
        ]
    )


@router.get("/tasks/{task_id}", response_model=TaskDetailResponse)
def get_task(task_id: uuid.UUID, db: Session = Depends(get_db)) -> TaskDetailResponse:
    service = SyncService(db)
    try:
        return TaskDetailResponse(item=service.get_task_detail(task_id))
    except ResourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/tasks/{task_id}/precheck", response_model=TaskRunResponse)
def precheck_task(
    task_id: uuid.UUID,
    service: TaskExecutionService = Depends(get_task_execution_service),
) -> TaskRunResponse:
    try:
        return TaskRunResponse(item=service.precheck_task(task_id))
    except OperationConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ResourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/tasks/{task_id}/execute", response_model=TaskRunResponse)
async def execute_task(
    task_id: uuid.UUID,
    request: TaskExecuteRequest,
    service: TaskExecutionService = Depends(get_task_execution_service),
) -> TaskRunResponse:
    try:
        return TaskRunResponse(item=await service.execute_task(task_id, dry_run=request.dry_run))
    except OperationConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ResourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/tasks/{task_id}/rerun", response_model=TaskRunResponse)
async def rerun_task(
    task_id: uuid.UUID,
    request: TaskExecuteRequest,
    service: TaskExecutionService = Depends(get_task_execution_service),
) -> TaskRunResponse:
    try:
        return TaskRunResponse(item=await service.rerun_task(task_id, dry_run=request.dry_run))
    except OperationConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ResourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
