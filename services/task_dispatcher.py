from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any

from core.config import get_settings
from core.db import SessionLocal
from core.runtime_state import runtime_state
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
        self._jobs: dict[str, dict[str, Any]] = {}
        self._batches: dict[str, dict[str, Any]] = {}

    async def start(self) -> None:
        async with self._lock:
            if self._started:
                return
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
        batch = self._new_batch(batch_id=batch_id, module_code=module_code, dry_run=dry_run, trigger=trigger)
        async with self._lock:
            self._batches[batch_id] = batch
        items: list[dict[str, Any]] = []

        for task_plan_id in task_plan_ids:
            async with self._lock:
                batch["requested_count"] += 1
            accepted = runtime_state.acquire_queued_task(task_plan_id)
            if not accepted:
                async with self._lock:
                    batch["duplicate_count"] += 1
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
            now = _utc_now_iso()
            job = {
                "job_id": job_id,
                "batch_id": batch_id,
                "module_code": module_code,
                "task_plan_id": task_plan_id,
                "dry_run": dry_run,
                "trigger": trigger,
                "status": "queued",
                "run_status": None,
                "manual_required": False,
                "task_run_id": None,
                "error_message": None,
                "created_at": now,
                "started_at": None,
                "finished_at": None,
            }
            async with self._lock:
                batch["enqueued_count"] += 1
                batch["queued_count"] += 1
                batch["job_ids"].append(job_id)
                self._jobs[job_id] = job
            await self._queue.put(job)
            items.append(
                {
                    "job_id": job_id,
                    "task_plan_id": task_plan_id,
                    "accepted": True,
                    "status": "queued",
                    "message": None,
                }
            )

        return {
            "batch_id": batch_id,
            "module_code": module_code,
            "requested_count": batch["requested_count"],
            "enqueued_count": batch["enqueued_count"],
            "duplicate_count": batch["duplicate_count"],
            "items": items,
        }

    async def get_batch_status(self, batch_id: str) -> dict[str, Any] | None:
        async with self._lock:
            batch = self._batches.get(batch_id)
            if batch is None:
                return None
            job_ids = list(batch["job_ids"])
            jobs = [dict(self._jobs[job_id]) for job_id in job_ids if job_id in self._jobs]
            done = batch["finished_count"] >= batch["enqueued_count"]
            if done:
                status = "completed"
            elif batch["running_count"] > 0:
                status = "running"
            elif batch["queued_count"] > 0:
                status = "queued"
            else:
                status = "pending"
            return {
                "batch_id": batch_id,
                "module_code": batch["module_code"],
                "created_at": batch["created_at"],
                "completed_at": batch["completed_at"],
                "requested_count": batch["requested_count"],
                "enqueued_count": batch["enqueued_count"],
                "duplicate_count": batch["duplicate_count"],
                "queued_count": batch["queued_count"],
                "running_count": batch["running_count"],
                "finished_count": batch["finished_count"],
                "closed_success_count": batch["closed_success_count"],
                "failed_count": batch["failed_count"],
                "manual_required_count": batch["manual_required_count"],
                "pending_confirmation_count": batch["pending_confirmation_count"],
                "status": status,
                "done": done,
                "jobs": jobs,
                "ephemeral": True,
                "note": "进程内队列：服务重启后 queued/running/batch 状态会丢失（phase-1 可接受）。",
            }

    def _new_batch(self, *, batch_id: str, module_code: str, dry_run: bool, trigger: str) -> dict[str, Any]:
        return {
            "batch_id": batch_id,
            "module_code": module_code,
            "dry_run": dry_run,
            "trigger": trigger,
            "created_at": _utc_now_iso(),
            "completed_at": None,
            "requested_count": 0,
            "enqueued_count": 0,
            "duplicate_count": 0,
            "queued_count": 0,
            "running_count": 0,
            "finished_count": 0,
            "closed_success_count": 0,
            "failed_count": 0,
            "manual_required_count": 0,
            "pending_confirmation_count": 0,
            "job_ids": [],
        }

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
        now = _utc_now_iso()
        async with self._lock:
            current_job = self._jobs.get(job["job_id"])
            if current_job is None:
                return
            current_job["status"] = "running"
            current_job["started_at"] = now
            batch = self._batches.get(job["batch_id"])
            if batch is None:
                return
            batch["queued_count"] = max(0, batch["queued_count"] - 1)
            batch["running_count"] += 1

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
        now = _utc_now_iso()
        async with self._lock:
            current_job = self._jobs.get(job["job_id"])
            if current_job is None:
                return
            current_job["status"] = "finished"
            current_job["run_status"] = run_status
            current_job["task_run_id"] = task_run_id
            current_job["manual_required"] = bool(manual_required)
            current_job["error_message"] = error_message
            current_job["finished_at"] = now

            batch = self._batches.get(job["batch_id"])
            if batch is None:
                return
            batch["running_count"] = max(0, batch["running_count"] - 1)
            batch["finished_count"] += 1
            if terminal_status == "closed_success":
                batch["closed_success_count"] += 1
            elif terminal_status == "manual_required":
                batch["manual_required_count"] += 1
            elif terminal_status == "pending_confirmation":
                batch["pending_confirmation_count"] += 1
            else:
                batch["failed_count"] += 1
            if batch["finished_count"] >= batch["enqueued_count"] and batch["completed_at"] is None:
                batch["completed_at"] = now


_dispatcher: TaskDispatcher | None = None


def get_task_dispatcher() -> TaskDispatcher:
    global _dispatcher
    if _dispatcher is None:
        settings = get_settings()
        _dispatcher = TaskDispatcher(worker_count=settings.task_dispatcher_worker_count)
    return _dispatcher
