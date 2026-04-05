from pydantic import BaseModel


class InspectionNormalizedRecord(BaseModel):
    inspection_month: str | None = None
    debug_inspection_month_raw: str | None = None
    debug_inspection_month_normalized: str | None = None
    customer_name: str | None = None
    service_type: str | None = None
    executor_name: str | None = None
    debug_executor_name_raw: str | None = None
    debug_executor_name_normalized: str | None = None
    work_order_link: str | None = None
    debug_work_order_link_raw: str | None = None
    debug_work_order_link_normalized: str | None = None
    work_order_id: str | None = None
    work_order_stage: str | None = None
    debug_work_order_stage_source: str | None = None
    debug_work_order_stage_raw: str | None = None
    debug_work_order_stage_normalized: str | None = None
    inspection_done: bool | None = None
    work_order_closed: bool | None = None
    debug_work_order_closed_raw: str | None = None
    debug_work_order_closed_normalized: bool | None = None
    report_match_name: str | None = None
    remark: str | None = None
