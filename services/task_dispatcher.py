from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any

from core.config import get_settings
from core.db import SessionLocal
from core.runtime_state import runtime_state
from repositories.task_batch_repo import TaskBatchRepository
from services.task_execution_service import TaskExecutionService


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _classify_terminal_status(run_detail) -> tuple[str, bool]:
    if run_detail.manual_required:
        return "manual_required", True
    if run_detail.run_status == "pending_confirmation":
        return "pending_confirmation", False
    if run_detail.run_status == "success":
        return "closed_success", False
    return "failed", False


class TaskDispatcher:
    """
    Phase-1 in-process dispatcher.

    Notes:
    - Uses asyncio.Queue and in-memory batch/job state only.
    - Queue and status data are NOT persistent.
    - After process restart, queued/running/batch states are lost (accepted in phase-1).
    """

    def __init__(self, worker_count: int = 4) -> None:
        self.worker_count = max(1, int(worker_count))
        self._queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self._workers: list[asyncio.Task[Any]] = []
        self._started = False
        self._lock = asyncio.Lock()
    async def start(self) -> None:
        async with self._lock:
            if self._started:
                return
            with SessionLocal() as db:
                TaskBatchRepository(db).recover_stale_incomplete_jobs()
                db.commit()
            self._started = True
            self._workers = [
                asyncio.create_task(self._worker_loop(index), name=f"task-dispatcher-{index}")
                for index in range(self.worker_count)
            ]

    async def stop(self) -> None:
        async with self._lock:
            if not self._started:
                return
            self._started = False
            workers = list(self._workers)
            self._workers = []
        for _ in workers:
            await self._queue.put(None)
        for worker in workers:
            try:
                await worker
            except asyncio.CancelledError:
                pass

    async def enqueue_tasks(
        self,
        *,
        module_code: str,
        task_plan_ids: list[str],
        dry_run: bool,
        trigger: str,
    ) -> dict[str, Any]:
        batch_id = str(uuid.uuid4())
        items: list[dict[str, Any]] = []
        enqueued_jobs: list[dict[str, Any]] = []
        with SessionLocal() as db:
            repo = TaskBatchRepository(db)
            repo.create_batch(
                batch_id=uuid.UUID(batch_id),
                module_code=module_code,
                dry_run=dry_run,
                trigger=trigger,
                note="批次状态已持久化：服务重启后仍可查看执行结果与失败明细。",
            )

            for task_plan_id in task_plan_ids:
                accepted = runtime_state.acquire_queued_task(task_plan_id)
                if not accepted:
                    repo.increment_duplicate_count(uuid.UUID(batch_id))
                    items.append(
                        {
                            "job_id": None,
                            "task_plan_id": task_plan_id,
                            "accepted": False,
                            "status": "duplicate",
                            "message": "该任务已在队列或执行中",
                        }
                    )
                    continue

                job_id = str(uuid.uuid4())
                repo.create_job(
                    job_id=uuid.UUID(job_id),
                    batch_id=uuid.UUID(batch_id),
                    task_plan_id=uuid.UUID(task_plan_id),
                )
                job = {
                    "job_id": job_id,
                    "batch_id": batch_id,
                    "module_code": module_code,
                    "task_plan_id": task_plan_id,
                    "dry_run": dry_run,
                    "trigger": trigger,
                }
                enqueued_jobs.append(job)
                items.append(
                    {
                        "job_id": job_id,
                        "task_plan_id": task_plan_id,
                        "accepted": True,
                        "status": "queued",
                        "message": None,
                    }
                )
            db.commit()

        for job in enqueued_jobs:
            await self._queue.put(job)

        batch_status = await self.get_batch_status(batch_id)
        if batch_status is None:
            requested_count = len(task_plan_ids)
            enqueued_count = len(enqueued_jobs)
            duplicate_count = requested_count - enqueued_count
        else:
            requested_count = int(batch_status.get("requested_count") or 0)
            enqueued_count = int(batch_status.get("enqueued_count") or 0)
            duplicate_count = int(batch_status.get("duplicate_count") or 0)

        return {
            "batch_id": batch_id,
            "module_code": module_code,
            "requested_count": requested_count,
            "enqueued_count": enqueued_count,
            "duplicate_count": duplicate_count,
            "items": items,
        }

    async def get_batch_status(self, batch_id: str) -> dict[str, Any] | None:
        with SessionLocal() as db:
            return TaskBatchRepository(db).get_batch_status(uuid.UUID(batch_id))

    async def _worker_loop(self, worker_index: int) -> None:
        while True:
            job = await self._queue.get()
            if job is None:
                self._queue.task_done()
                return
            try:
                await self._mark_job_running(job)
                await self._execute_job(job, worker_index=worker_index)
            finally:
                runtime_state.release_queued_task(job["task_plan_id"])
                self._queue.task_done()

    async def _execute_job(self, job: dict[str, Any], *, worker_index: int) -> None:
        task_plan_id = job["task_plan_id"]
        try:
            task_uuid = uuid.UUID(task_plan_id)
            with SessionLocal() as db:
                service = TaskExecutionService(db)
                detail = await service.execute_task(
                    task_uuid,
                    dry_run=bool(job.get("dry_run", False)),
                    trigger=str(job.get("trigger") or "manual"),
                )
            terminal_status, manual_required = _classify_terminal_status(detail)
            await self._mark_job_finished(
                job,
                run_status=detail.run_status,
                task_run_id=detail.task_run_id,
                manual_required=manual_required,
                terminal_status=terminal_status,
                error_message=detail.error_message,
            )
        except Exception as exc:
            await self._mark_job_finished(
                job,
                run_status="failed",
                task_run_id=None,
                manual_required=False,
                terminal_status="failed",
                error_message=f"worker-{worker_index} 执行异常: {exc}",
            )

    async def _mark_job_running(self, job: dict[str, Any]) -> None:
        with SessionLocal() as db:
            repo = TaskBatchRepository(db)
            repo.mark_job_running(uuid.UUID(job["job_id"]))
            db.commit()

    async def _mark_job_finished(
        self,
        job: dict[str, Any],
        *,
        run_status: str,
        task_run_id: str | None,
        manual_required: bool,
        terminal_status: str,
        error_message: str | None,
    ) -> None:
        parsed_task_run_id = uuid.UUID(task_run_id) if task_run_id else None
        with SessionLocal() as db:
            repo = TaskBatchRepository(db)
            repo.mark_job_finished(
                uuid.UUID(job["job_id"]),
                run_status=run_status,
                task_run_id=parsed_task_run_id,
                manual_required=manual_required,
                terminal_status=terminal_status,
                error_message=error_message,
            )
            db.commit()


_dispatcher: TaskDispatcher | None = None


def get_task_dispatcher() -> TaskDispatcher:
    global _dispatcher
    if _dispatcher is None:
        settings = get_settings()
        _dispatcher = TaskDispatcher(worker_count=settings.task_dispatcher_worker_count)
    return _dispatcher
