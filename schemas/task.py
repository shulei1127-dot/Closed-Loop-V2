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
