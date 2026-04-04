from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ExecutionResult(BaseModel):
    run_status: str
    manual_required: bool = False
    result_payload: dict[str, Any] = Field(default_factory=dict)
    final_link: str | None = None
    error_message: str | None = None
    executor_version: str | None = None
    retryable: bool = False


class ExecutorContext(BaseModel):
    task_plan_id: str
    module_code: str
    task_type: str
    plan_status: str
    normalized_record_id: str
    source_row_id: str = ""
    recognition_status: str
    planned_payload: dict[str, Any] = Field(default_factory=dict)
    normalized_data: dict[str, Any] = Field(default_factory=dict)
    source_url: str | None = None
    source_doc_key: str | None = None
    source_view_key: str | None = None
    source_collector_type: str | None = None
    source_extra_config: dict[str, Any] = Field(default_factory=dict)
