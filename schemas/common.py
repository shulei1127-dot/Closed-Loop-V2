from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class HealthzResponse(BaseModel):
    ok: bool = True
    service: str
    db: str


class EnvironmentCheckResponse(BaseModel):
    ok: bool
    app_env: str
    app_debug: bool
    database: dict[str, Any] = Field(default_factory=dict)
    real_execution: dict[str, Any] = Field(default_factory=dict)
    scheduler: dict[str, Any] = Field(default_factory=dict)


class SnapshotItem(BaseModel):
    snapshot_id: str
    module_code: str
    sync_time: datetime
    sync_status: str
    data_source: str
    row_count: int


class SnapshotDetail(SnapshotItem):
    source_url: str
    source_doc_key: str
    source_view_key: str | None = None
    sync_error: str | None = None
    raw_columns: list[Any] = Field(default_factory=list)
    raw_rows: list[dict[str, Any]] = Field(default_factory=list)
    raw_meta: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class RecordItem(BaseModel):
    record_id: str
    snapshot_id: str
    module_code: str
    source_row_id: str
    customer_name: str | None = None
    normalized_data: dict[str, Any]
    field_mapping: dict[str, Any]
    field_confidence: dict[str, Any]
    recognition_status: str


class RecordDetail(RecordItem):
    field_evidence: dict[str, Any] = Field(default_factory=dict)
    field_samples: dict[str, Any] = Field(default_factory=dict)
    unresolved_fields: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class TaskItem(BaseModel):
    task_plan_id: str
    module_code: str
    normalized_record_id: str
    task_type: str
    eligibility: bool
    skip_reason: str | None = None
    planner_version: str
    plan_status: str
    planned_payload: dict[str, Any] = Field(default_factory=dict)


class TaskDetail(TaskItem):
    created_at: datetime
    updated_at: datetime


class TaskRunItem(BaseModel):
    task_run_id: str
    task_plan_id: str
    run_status: str
    manual_required: bool
    result_payload: dict[str, Any] = Field(default_factory=dict)
    final_link: str | None = None
    error_message: str | None = None
    executor_version: str | None = None


class TaskRunDetail(TaskRunItem):
    run_time: datetime
    created_at: datetime


class ModuleSummaryItem(BaseModel):
    module_code: str
    module_name: str
    snapshot_id: str | None = None
    latest_snapshot_time: datetime | None = None
    sync_status: str | None = None
    row_count: int = 0
    full_records: int = 0
    partial_records: int = 0
    failed_records: int = 0
    planned_tasks: int = 0
    skipped_tasks: int = 0
