from __future__ import annotations

from typing import Any, Protocol

from services.executors.schemas import ExecutionResult, ExecutorContext


class BaseExecutor(Protocol):
    module_code: str
    task_type: str
    executor_version: str

    def precheck(self, context: ExecutorContext) -> ExecutionResult: ...

    async def dry_run(self, context: ExecutorContext) -> ExecutionResult: ...

    async def execute(self, context: ExecutorContext) -> ExecutionResult: ...

    def healthcheck(self) -> dict[str, Any]: ...
