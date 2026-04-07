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
    month: str | None = None
    visit_owner: str | None = None
    dry_run: bool = False


class TaskBatchExecuteResponse(BaseModel):
    ok: bool = True
    module_code: str
    total_count: int
    success_count: int
    failed_count: int
    manual_required_count: int
    items: list[TaskRunDetail]


class TaskEnqueueItem(BaseModel):
    job_id: str | None = None
    task_plan_id: str
    accepted: bool
    status: str
    message: str | None = None


class TaskEnqueueResponse(BaseModel):
    ok: bool = True
    batch_id: str
    module_code: str
    item: TaskEnqueueItem


class TaskBatchEnqueueResponse(BaseModel):
    ok: bool = True
    batch_id: str
    module_code: str
    requested_count: int
    enqueued_count: int
    duplicate_count: int
    items: list[TaskEnqueueItem]


class TaskBatchStatusResponse(BaseModel):
    ok: bool = True
    item: dict
