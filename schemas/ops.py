from datetime import datetime

from pydantic import BaseModel, Field


class OpsOverviewItem(BaseModel):
    module_code: str
    module_name: str
    latest_snapshot_time: datetime | None = None
    latest_sync_status: str | None = None
    latest_sync_status_label: str | None = None
    latest_execute_status: str | None = None
    latest_execute_status_label: str | None = None
    latest_execute_explanation: str | None = None
    row_count: int = 0
    planned_tasks: int = 0
    skipped_tasks: int = 0
    manual_required_count: int = 0
    failed_task_count: int = 0
    retryable_task_count: int = 0
    sync_running: bool = False
    schedule_enabled: bool = False
    schedule_type: str | None = None
    schedule_value: str | None = None


class OpsEventItem(BaseModel):
    kind: str
    module_code: str
    title: str
    status: str
    occurred_at: datetime
    message: str | None = None
    retryable: bool = False
    manual_required: bool = False
    customer_name: str | None = None
    display_status: str | None = None
    status_tone: str | None = None
    error_type: str | None = None
    business_explanation: str | None = None
    detail_url: str | None = None
    rerun_available: bool = False
    snapshot_id: str | None = None
    task_plan_id: str | None = None
    task_run_id: str | None = None


class OpsOverviewResponse(BaseModel):
    ok: bool = True
    items: list[OpsOverviewItem] = Field(default_factory=list)


class OpsEventListResponse(BaseModel):
    ok: bool = True
    items: list[OpsEventItem] = Field(default_factory=list)


class PendingTaskItem(BaseModel):
    task_plan_id: str
    module_code: str
    task_type: str
    customer_name: str | None = None
    delivery_id: str | None = None
    visit_type: str | None = None
    inspection_month: str | None = None
    executor_name: str | None = None
    work_order_link: str | None = None
    work_order_closed: bool | None = None
    report_word_file: str | None = None
    planned_payload: dict = Field(default_factory=dict)
    latest_run_status: str | None = None
    latest_run_status_label: str | None = None
    latest_run_time: datetime | None = None
    business_explanation: str | None = None
    state_code: str | None = None
    state_label: str | None = None
    state_tone: str | None = None
    can_execute: bool = True
    detail_url: str | None = None


class RecentVisitLinkItem(BaseModel):
    customer_name: str | None = None
    visit_type: str | None = None
    final_link: str
    occurred_at: datetime
    detail_url: str | None = None
    task_plan_id: str | None = None
    task_run_id: str | None = None


class RecentInspectionClosureItem(BaseModel):
    customer_name: str | None = None
    inspection_month: str | None = None
    final_link: str
    occurred_at: datetime
    detail_url: str | None = None
    task_plan_id: str | None = None
    task_run_id: str | None = None


class PtsSessionStatusResponse(BaseModel):
    ok: bool = True
    configured: bool = False
    base_url: str | None = None
    source: str = "env_file"
    updated_at: datetime | None = None
    message: str | None = None


class PtsSessionUpdateRequest(BaseModel):
    cookie_header: str = Field(min_length=1)
