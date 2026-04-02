import uuid

from fastapi import APIRouter, Depends, HTTPException

from apps.api.deps import get_task_execution_service
from core.exceptions import ResourceNotFoundError
from schemas.task import TaskRunResponse
from services.task_execution_service import TaskExecutionService


router = APIRouter()


@router.get("/task-runs/{run_id}", response_model=TaskRunResponse)
def get_task_run(
    run_id: uuid.UUID,
    service: TaskExecutionService = Depends(get_task_execution_service),
) -> TaskRunResponse:
    try:
        return TaskRunResponse(item=service.get_task_run_detail(run_id))
    except ResourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
