from typing import Any

from pydantic import BaseModel, Field


class CollectResult(BaseModel):
    module_code: str
    source_url: str
    source_doc_key: str
    source_view_key: str | None = None
    data_source: str
    sync_status: str
    raw_columns: list[dict[str, Any] | str]
    raw_rows: list[dict[str, Any]]
    raw_meta: dict[str, Any] = Field(default_factory=dict)
    sync_error: str | None = None


class RecognitionResult(BaseModel):
    normalized_records: list[dict[str, Any]] = Field(default_factory=list)
    field_mapping: dict[str, Any] = Field(default_factory=dict)
    field_confidence: dict[str, Any] = Field(default_factory=dict)
    field_evidence: dict[str, Any] = Field(default_factory=dict)
    field_samples: dict[str, Any] = Field(default_factory=dict)
    unresolved_fields: list[str] = Field(default_factory=list)
    recognition_status: str = "full"


class TaskPlanDTO(BaseModel):
    module_code: str
    source_row_id: str
    task_type: str
    eligibility: bool
    skip_reason: str | None = None
    planner_version: str = "v1"
    plan_status: str = "planned"
    planned_payload: dict[str, Any] = Field(default_factory=dict)


class SyncRunRequest(BaseModel):
    module_code: str
    force: bool = False


class SyncRunResponse(BaseModel):
    ok: bool = True

    class SnapshotSummary(BaseModel):
        snapshot_id: str
        module_code: str
        sync_status: str
        data_source: str
        row_count: int

    class RecognitionStats(BaseModel):
        record_count: int
        full_count: int
        partial_count: int
        failed_count: int
        unresolved_field_count: int

    class TaskPlanStats(BaseModel):
        total_count: int
        planned_count: int
        skipped_count: int

    class RunContext(BaseModel):
        trigger: str
        attempt: int
        retry_count: int = 0
        retried: bool = False
        retryable: bool = False

    snapshot: SnapshotSummary
    recognition: RecognitionStats
    task_plans: TaskPlanStats
    run_context: RunContext | None = None
