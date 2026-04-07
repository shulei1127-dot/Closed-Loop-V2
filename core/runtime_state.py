from __future__ import annotations

from threading import RLock


class RuntimeStateRegistry:
    def __init__(self) -> None:
        self._lock = RLock()
        self._running_sync_modules: set[str] = set()
        self._running_task_ids: set[str] = set()
        self._queued_task_ids: set[str] = set()

    def acquire_sync(self, module_code: str) -> bool:
        with self._lock:
            if module_code in self._running_sync_modules:
                return False
            self._running_sync_modules.add(module_code)
            return True

    def release_sync(self, module_code: str) -> None:
        with self._lock:
            self._running_sync_modules.discard(module_code)

    def acquire_task(self, task_plan_id: str) -> bool:
        with self._lock:
            if task_plan_id in self._running_task_ids:
                return False
            self._running_task_ids.add(task_plan_id)
            return True

    def release_task(self, task_plan_id: str) -> None:
        with self._lock:
            self._running_task_ids.discard(task_plan_id)

    def acquire_queued_task(self, task_plan_id: str) -> bool:
        with self._lock:
            if task_plan_id in self._queued_task_ids or task_plan_id in self._running_task_ids:
                return False
            self._queued_task_ids.add(task_plan_id)
            return True

    def release_queued_task(self, task_plan_id: str) -> None:
        with self._lock:
            self._queued_task_ids.discard(task_plan_id)

    def snapshot(self) -> dict[str, list[str]]:
        with self._lock:
            return {
                "running_sync_modules": sorted(self._running_sync_modules),
                "running_task_ids": sorted(self._running_task_ids),
                "queued_task_ids": sorted(self._queued_task_ids),
            }

    def clear(self) -> None:
        with self._lock:
            self._running_sync_modules.clear()
            self._running_task_ids.clear()
            self._queued_task_ids.clear()


runtime_state = RuntimeStateRegistry()
