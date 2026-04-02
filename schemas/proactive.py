from pydantic import BaseModel


class ProactiveNormalizedRecord(BaseModel):
    customer_name: str | None = None
    product_link: str | None = None
    product_info_id: str | None = None
    liaison_status: str | None = None
    visit_link: str | None = None
    feedback_note: str | None = None
    contact_name: str | None = None
    contact_phone: str | None = None
    engineer_name: str | None = None
