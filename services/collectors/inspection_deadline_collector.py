from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

from core.config import Settings, get_settings
from services.executors.visit_real_runner import _PtsBrowserSession
from services.reminders.rules import normalize_status_text, parse_local_date
from services.reminders.schemas import InspectionDeadlineCandidate


RawFetchCallable = Callable[[int], Awaitable[list[dict[str, Any]]]]


class InspectionDeadlineCollector:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        raw_fetcher: RawFetchCallable | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._raw_fetcher = raw_fetcher

    async def collect(self, *, limit: int | None = None) -> list[InspectionDeadlineCandidate]:
        resolved_limit = int(limit or self.settings.inspection_deadline_reminder_query_limit)
        rows = await self._fetch_rows(resolved_limit)
        items: list[InspectionDeadlineCandidate] = []
        seen_ids: set[str] = set()
        for row in rows:
            item = self._normalize_row(row)
            if item is None or item.pts_work_order_id in seen_ids:
                continue
            seen_ids.add(item.pts_work_order_id)
            items.append(item)
        return items

    async def _fetch_rows(self, limit: int) -> list[dict[str, Any]]:
        if self._raw_fetcher is not None:
            return await self._raw_fetcher(limit)

        async with _PtsBrowserSession(self.settings) as session:
            open_result = await session.open_project(self._entry_url())
            if open_result.get("status") != "success":
                raise RuntimeError(str(open_result.get("error_message") or "打开 PTS 页面失败"))
            payload = await session.graphql_payload(self._build_query_payload(limit))
        return self._extract_rows(payload)

    def _entry_url(self) -> str:
        return f"{self.settings.pts_base_url.rstrip('/')}/project"

    def _build_query_payload(self, limit: int) -> dict[str, Any]:
        custom_payload = str(self.settings.inspection_deadline_reminder_query_payload_json or "").strip()
        if custom_payload:
            payload = json.loads(custom_payload)
            variables = payload.setdefault("variables", {})
            if isinstance(variables, dict):
                variables.setdefault("limit", limit)
                variables.setdefault("pageSize", limit)
            return payload
        return {
            "operationName": "InspectionDeadlineReminderList",
            "variables": {"page": 1, "pageSize": limit, "limit": limit},
            "query": (
                "query InspectionDeadlineReminderList($page: Int, $pageSize: Int, $limit: Int) { "
                "workOrderList(page: $page, pageSize: $pageSize, limit: $limit) { "
                "items { "
                "id "
                "name "
                "customer_name "
                "service_type "
                "status "
                "status_name "
                "plan_finish_time "
                "is_finished "
                "current_stage { name } "
                "link "
                "} "
                "} "
                "}"
            ),
        }

    def _extract_rows(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            return []
        rows: list[dict[str, Any]] = []

        def visit(node: Any) -> None:
            if isinstance(node, dict):
                if self._looks_like_row(node):
                    rows.append(node)
                    return
                for value in node.values():
                    visit(value)
            elif isinstance(node, list):
                for item in node:
                    visit(item)

        visit(data)
        return rows

    @staticmethod
    def _looks_like_row(node: dict[str, Any]) -> bool:
        keys = set(node.keys())
        if not any(key in keys for key in ("id", "work_order_id", "pts_work_order_id")):
            return False
        return any(key in keys for key in ("plan_finish_time", "planFinishTime", "deadline", "deadline_time"))

    def _normalize_row(self, row: dict[str, Any]) -> InspectionDeadlineCandidate | None:
        pts_work_order_id = self._pick_first_text(row, "pts_work_order_id", "work_order_id", "id")
        if not pts_work_order_id:
            return None
        raw_plan_finish_time = self._pick_first_value(row, "plan_finish_time", "planFinishTime", "deadline", "deadline_time")
        current_stage = row.get("current_stage")
        status_text = normalize_status_text(
            self._pick_first_value(row, "status_text", "status_name", "status")
            or (current_stage or {}).get("name")
            or ("已完成" if row.get("is_finished") else None)
        )
        plan_finish_date = parse_local_date(raw_plan_finish_time)
        return InspectionDeadlineCandidate(
            pts_work_order_id=pts_work_order_id,
            pts_work_order_link=self._pick_first_text(row, "pts_work_order_link", "work_order_link", "link", "url")
            or f"{self.settings.pts_base_url.rstrip('/')}/project/{pts_work_order_id}",
            customer_name=self._pick_first_text(row, "customer_name", "customerName", "name", "title"),
            service_type=self._pick_first_text(row, "service_type", "serviceType", "biz_type"),
            status_text=status_text,
            plan_finish_time_raw=None if raw_plan_finish_time is None else str(raw_plan_finish_time),
            plan_finish_date=plan_finish_date,
            raw_payload=dict(row),
        )

    @staticmethod
    def _pick_first_text(row: dict[str, Any], *keys: str) -> str | None:
        value = InspectionDeadlineCollector._pick_first_value(row, *keys)
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _pick_first_value(row: dict[str, Any], *keys: str):
        for key in keys:
            if key in row and row.get(key) not in (None, ""):
                return row.get(key)
        return None
