from typing import Any

from pydantic import BaseModel, Field


class ReviewNormalizedRecord(BaseModel):
    """Normalized record for review module — projects pending post-sales audit."""

    project_id: str | None = None
    project_name: str | None = None
    customer_name: str | None = None
    delivery_stage: str | None = None
    stage_status: str | None = None
    after_sales_leader: str | None = None
    assigner_name: str | None = None
    assigner_username: str | None = None
    person_in_charge_name: str | None = None
    person_in_charge_username: str | None = None


class ReviewAuditPayload(BaseModel):
    """Payload embedded in a review_audit task plan."""

    project_id: str
    project_name: str | None = None
    customer_name: str | None = None
    after_sales_leader: str | None = None
    assigner_name: str | None = None
    assigner_username: str | None = None
    person_in_charge_name: str | None = None


class ReviewAuditResultPayload(BaseModel):
    """Result payload for review_audit execution, wrapping AuditResult from the audit engine."""

    project_id: str
    audit_passed: bool
    audit_details: dict[str, Any] = Field(default_factory=dict)
    audit_errors: list[str] = Field(default_factory=list)
    dingtalk_writeback: dict[str, Any] | None = None