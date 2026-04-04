from pydantic import BaseModel

from schemas.common import TaskDetail, TaskItem, TaskRunDetail


class TaskListResponse(BaseModel):
    ok: bool = True
    items: list[TaskItem]


class TaskDetailResponse(BaseModel):
    ok: bool = True
    item: TaskDetail


class TaskExecuteRequest(BaseModel):
    dry_run: bool = False


class TaskRunResponse(BaseModel):
    ok: bool = True
    item: TaskRunDetail


class TaskBatchExecuteRequest(BaseModel):
    module_code: str
    dry_run: bool = False


class TaskBatchExecuteResponse(BaseModel):
    ok: bool = True
    module_code: str
    total_count: int
    success_count: int
    failed_count: int
    manual_required_count: int
    items: list[TaskRunDetail]
