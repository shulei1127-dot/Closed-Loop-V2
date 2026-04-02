import uuid

from sqlalchemy import desc, select

from models.source_snapshot import SourceSnapshot
from repositories.base import BaseRepository
from schemas.sync import CollectResult


class SourceSnapshotRepository(BaseRepository):
    def create_from_collect_result(self, collect_result: CollectResult) -> SourceSnapshot:
        snapshot = SourceSnapshot(
            module_code=collect_result.module_code,
            source_url=collect_result.source_url,
            source_doc_key=collect_result.source_doc_key,
            source_view_key=collect_result.source_view_key,
            data_source=collect_result.data_source,
            sync_status=collect_result.sync_status,
            sync_error=collect_result.sync_error,
            raw_columns=collect_result.raw_columns,
            raw_rows=collect_result.raw_rows,
            raw_meta=collect_result.raw_meta,
            row_count=len(collect_result.raw_rows),
        )
        self.db.add(snapshot)
        self.db.flush()
        return snapshot

    def list_recent(self, module_code: str | None, limit: int) -> list[SourceSnapshot]:
        statement = select(SourceSnapshot).order_by(SourceSnapshot.sync_time.desc()).limit(limit)
        if module_code:
            statement = statement.where(SourceSnapshot.module_code == module_code)
        return list(self.db.scalars(statement).all())

    def get_by_id(self, snapshot_id: uuid.UUID) -> SourceSnapshot | None:
        statement = select(SourceSnapshot).where(SourceSnapshot.id == snapshot_id)
        return self.db.scalar(statement)

    def latest_for_module(self, module_code: str) -> SourceSnapshot | None:
        statement = (
            select(SourceSnapshot)
            .where(SourceSnapshot.module_code == module_code)
            .order_by(SourceSnapshot.sync_time.desc())
            .limit(1)
        )
        return self.db.scalar(statement)

    def list_failed(self, limit: int = 20) -> list[SourceSnapshot]:
        statement = (
            select(SourceSnapshot)
            .where(SourceSnapshot.sync_status == "failed")
            .order_by(desc(SourceSnapshot.sync_time))
            .limit(limit)
        )
        return list(self.db.scalars(statement).all())
