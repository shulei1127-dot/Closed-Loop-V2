from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from models.task_batch import TaskBatch
from models.task_batch_job import TaskBatchJob
from repositories.base import BaseRepository


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TaskBatchRepository(BaseRepository):
    def __init__(self, db: Session) -> None:
        super().__init__(db)

    def create_batch(
        self,
        *,
        batch_id: uuid.UUID,
        module_code: str,
        trigger: str,
        dry_run: bool,
        note: str | None = None,
    ) -> TaskBatch:
        batch = TaskBatch(
            id=batch_id,
            module_code=module_code,
            trigger=trigger,
            dry_run=dry_run,
            status="queued",
            note=note,
        )
        self.db.add(batch)
        self.db.flush()
        return batch

    def create_job(
        self,
        *,
        job_id: uuid.UUID,
        batch_id: uuid.UUID,
        task_plan_id: uuid.UUID,
    ) -> TaskBatchJob:
        job = TaskBatchJob(
            id=job_id,
            batch_id=batch_id,
            task_plan_id=task_plan_id,
            status="queued",
        )
        self.db.add(job)
        self.db.flush()
        self._refresh_batch_counters(batch_id)
        return job

    def increment_duplicate_count(self, batch_id: uuid.UUID, amount: int = 1) -> None:
        batch = self.db.get(TaskBatch, batch_id)
        if batch is None:
            return
        batch.duplicate_count = max(0, int(batch.duplicate_count or 0) + int(amount or 0))
        self.db.flush()
        self._refresh_batch_counters(batch_id)

    def mark_job_running(self, job_id: uuid.UUID) -> None:
        job = self.db.get(TaskBatchJob, job_id)
        if job is None:
            return
        job.status = "running"
        job.started_at = _utc_now()
        self.db.flush()
        self._refresh_batch_counters(job.batch_id)

    def mark_job_finished(
        self,
        job_id: uuid.UUID,
        *,
        run_status: str,
        task_run_id: uuid.UUID | None,
        manual_required: bool,
        terminal_status: str,
        error_message: str | None,
    ) -> None:
        job = self.db.get(TaskBatchJob, job_id)
        if job is None:
            return
        job.status = "finished"
        job.run_status = run_status
        job.task_run_id = task_run_id
        job.manual_required = bool(manual_required)
        job.terminal_status = terminal_status
        job.error_message = error_message
        job.finished_at = _utc_now()
        self.db.flush()
        self._refresh_batch_counters(job.batch_id)

    def recover_stale_incomplete_jobs(self) -> int:
        statement = select(TaskBatchJob).where(TaskBatchJob.status.in_(("queued", "running")))
        jobs = list(self.db.scalars(statement).all())
        if not jobs:
            return 0
        affected_batch_ids: set[uuid.UUID] = set()
        now = _utc_now()
        for job in jobs:
            job.status = "finished"
            job.run_status = "failed"
            job.terminal_status = "failed"
            job.error_message = "服务重启导致后台队列状态丢失，请重新提交该任务。"
            job.finished_at = now
            affected_batch_ids.add(job.batch_id)
        self.db.flush()
        for batch_id in affected_batch_ids:
            batch = self.db.get(TaskBatch, batch_id)
            if batch is not None:
                batch.note = "服务重启后已将未完成队列任务标记为失败，请按需重新提交。"
            self._refresh_batch_counters(batch_id)
        return len(jobs)

    def get_batch_status(self, batch_id: uuid.UUID) -> dict | None:
        self._refresh_batch_counters(batch_id)
        batch = self.db.get(TaskBatch, batch_id)
        if batch is None:
            return None
        statement = (
            select(TaskBatchJob)
            .where(TaskBatchJob.batch_id == batch_id)
            .order_by(TaskBatchJob.created_at.asc(), TaskBatchJob.id.asc())
        )
        jobs = list(self.db.scalars(statement).all())
        done = batch.finished_count >= batch.enqueued_count
        return {
            "batch_id": str(batch.id),
            "module_code": batch.module_code,
            "created_at": batch.created_at.isoformat() if batch.created_at else None,
            "completed_at": batch.completed_at.isoformat() if batch.completed_at else None,
            "requested_count": batch.requested_count,
            "enqueued_count": batch.enqueued_count,
            "duplicate_count": batch.duplicate_count,
            "queued_count": batch.queued_count,
            "running_count": batch.running_count,
            "finished_count": batch.finished_count,
            "closed_success_count": batch.closed_success_count,
            "failed_count": batch.failed_count,
            "manual_required_count": batch.manual_required_count,
            "pending_confirmation_count": batch.pending_confirmation_count,
            "status": batch.status,
            "done": done,
            "jobs": [
                {
                    "job_id": str(job.id),
                    "task_plan_id": str(job.task_plan_id),
                    "status": job.status,
                    "terminal_status": job.terminal_status,
                    "run_status": job.run_status,
                    "manual_required": job.manual_required,
                    "task_run_id": str(job.task_run_id) if job.task_run_id else None,
                    "error_message": job.error_message,
                    "created_at": job.created_at.isoformat() if job.created_at else None,
                    "started_at": job.started_at.isoformat() if job.started_at else None,
                    "finished_at": job.finished_at.isoformat() if job.finished_at else None,
                }
                for job in jobs
            ],
            "persistent": True,
            "note": batch.note
            or "批次状态已持久化：服务重启后仍可查看已入队/已完成/失败明细。",
        }

    def _refresh_batch_counters(self, batch_id: uuid.UUID) -> None:
        self.db.flush()
        batch = self.db.get(TaskBatch, batch_id)
        if batch is None:
            return
        statement = (
            select(
                TaskBatchJob.status,
                TaskBatchJob.terminal_status,
                func.count(TaskBatchJob.id),
            )
            .where(TaskBatchJob.batch_id == batch_id)
            .group_by(TaskBatchJob.status, TaskBatchJob.terminal_status)
        )
        rows = self.db.execute(statement).all()
        queued_count = 0
        running_count = 0
        finished_count = 0
        closed_success_count = 0
        failed_count = 0
        manual_required_count = 0
        pending_confirmation_count = 0
        for status, terminal_status, count in rows:
            if status == "queued":
                queued_count += int(count)
            elif status == "running":
                running_count += int(count)
            elif status == "finished":
                finished_count += int(count)
                if terminal_status == "closed_success":
                    closed_success_count += int(count)
                elif terminal_status == "manual_required":
                    manual_required_count += int(count)
                elif terminal_status == "pending_confirmation":
                    pending_confirmation_count += int(count)
                else:
                    failed_count += int(count)
        batch.enqueued_count = queued_count + running_count + finished_count
        batch.requested_count = batch.enqueued_count + int(batch.duplicate_count or 0)
        batch.queued_count = queued_count
        batch.running_count = running_count
        batch.finished_count = finished_count
        batch.closed_success_count = closed_success_count
        batch.failed_count = failed_count
        batch.manual_required_count = manual_required_count
        batch.pending_confirmation_count = pending_confirmation_count
        if batch.enqueued_count == 0 and batch.duplicate_count > 0:
            batch.status = "completed"
            if batch.completed_at is None:
                batch.completed_at = _utc_now()
            return
        if batch.finished_count >= batch.enqueued_count and batch.enqueued_count > 0:
            batch.status = "completed"
            if batch.completed_at is None:
                batch.completed_at = _utc_now()
        elif batch.running_count > 0:
            batch.status = "running"
            batch.completed_at = None
        elif batch.queued_count > 0:
            batch.status = "queued"
            batch.completed_at = None
        else:
            batch.status = "pending"
            batch.completed_at = None
