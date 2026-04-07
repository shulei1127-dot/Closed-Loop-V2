import uuid

from fastapi import APIRouter, Depends, Query
from fastapi import HTTPException
from sqlalchemy.orm import Session

from apps.api.deps import get_task_execution_service
from core.db import get_db
from core.exceptions import OperationConflictError, ResourceNotFoundError
from repositories.task_plan_repo import TaskPlanRepository
from schemas.task import (
    TaskBatchExecuteRequest,
    TaskBatchExecuteResponse,
    TaskDetailResponse,
    TaskExecuteRequest,
    TaskListResponse,
    TaskRunResponse,
)
from schemas.common import TaskItem
from services.ops_service import OpsService
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
    items = repo.list_latest_by_business_key(module_code=module_code, status=status)
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


@router.post("/tasks/batch/execute-pending", response_model=TaskBatchExecuteResponse)
async def execute_pending_tasks(
    request: TaskBatchExecuteRequest,
    db: Session = Depends(get_db),
    service: TaskExecutionService = Depends(get_task_execution_service),
) -> TaskBatchExecuteResponse:
    ops_service = OpsService(db)
    pending_items = ops_service.list_pending_tasks(module_code=request.module_code, limit=5000, month=request.month)
    if request.module_code == "inspection":
        pending_items = [item for item in pending_items if item.can_execute]
    task_ids = [uuid.UUID(item.task_plan_id) for item in pending_items]

    results = []
    for task_id in task_ids:
        try:
            results.append(await service.execute_task(task_id, dry_run=request.dry_run))
        except OperationConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ResourceNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    success_count = sum(1 for item in results if item.run_status in {"success", "simulated_success"})
    manual_required_count = sum(1 for item in results if item.manual_required)
    failed_count = len(results) - success_count
    return TaskBatchExecuteResponse(
        module_code=request.module_code,
        total_count=len(results),
        success_count=success_count,
        failed_count=failed_count,
        manual_required_count=manual_required_count,
        items=results,
    )
