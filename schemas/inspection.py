from pydantic import BaseModel


class InspectionNormalizedRecord(BaseModel):
    customer_name: str | None = None
    work_order_link: str | None = None
    work_order_id: str | None = None
    inspection_done: bool | None = None
    report_match_name: str | None = None

