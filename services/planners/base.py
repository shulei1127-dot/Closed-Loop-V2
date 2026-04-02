from typing import Protocol

from schemas.sync import TaskPlanDTO


class BasePlanner(Protocol):
    def plan(self, normalized_records: list[dict]) -> list[TaskPlanDTO]: ...

