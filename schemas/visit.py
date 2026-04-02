from pydantic import BaseModel


class VisitNormalizedRecord(BaseModel):
    customer_name: str | None = None
    pts_link: str | None = None
    debug_pts_link_raw: str | None = None
    debug_pts_link_normalized: str | None = None
    delivery_id: str | None = None
    debug_pts_project_id: str | None = None
    debug_delivery_id_source: str | None = None
    debug_delivery_id_raw: str | None = None
    debug_delivery_id_normalized: str | None = None
    visit_owner: str | None = None
    debug_visit_owner_raw: str | None = None
    debug_visit_owner_normalized: str | None = None
    visit_status: str | None = None
    visit_link: str | None = None
    debug_visit_link_raw: str | None = None
    debug_visit_link_normalized: str | None = None
    visit_type: str | None = None
    visit_contact: str | None = None
    satisfaction: str | None = None
    feedback_note: str | None = None
