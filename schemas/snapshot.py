from pydantic import BaseModel

from schemas.common import SnapshotDetail, SnapshotItem


class SnapshotListResponse(BaseModel):
    ok: bool = True
    items: list[SnapshotItem]


class SnapshotDetailResponse(BaseModel):
    ok: bool = True
    item: SnapshotDetail
