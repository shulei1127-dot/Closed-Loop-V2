from pydantic import BaseModel

from schemas.common import RecordDetail, RecordItem


class RecordListResponse(BaseModel):
    ok: bool = True
    items: list[RecordItem]


class RecordDetailResponse(BaseModel):
    ok: bool = True
    item: RecordDetail
