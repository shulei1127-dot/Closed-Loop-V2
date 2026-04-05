from __future__ import annotations

import re
import shutil
import tempfile
from typing import Any, Awaitable, Callable
from pathlib import Path

from core.config import Settings, get_settings
from services.executors.visit_real_runner import _PtsBrowserSession, _PtsRunnerError
from services.recognizers.visit_delivery_backfill import (
    _copy_chrome_profile,
    _find_local_chrome_user_data_dir,
    strip_url_fragment,
)


InspectionStageReader = Callable[[str], Awaitable[tuple[str | None, str, str | None]]]

_CLOSED_MARKERS = (
    ("审核工单", "完成工单审核"),
    ("审核工单", "工单当前阶段: 审核工单"),
    ("审核工单", "工单当前阶段：审核工单"),
)
_STAGE_MARKERS = (
    ("处理工单", "完成工单处理"),
    ("处理中", "开始工单处理"),
    ("待处理", "指定工单负责人"),
    ("发起工单", "发起工单"),
)
_WHITESPACE_RE = re.compile(r"\s+")


class InspectionWorkOrderStageBackfill:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        stage_reader: InspectionStageReader | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._stage_reader = stage_reader or self._read_stage_from_pts
        self._stage_cache: dict[str, tuple[str | None, str, str | None]] = {}

    async def enrich_records(self, normalized_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not normalized_records:
            return normalized_records

        for item in normalized_records:
            data = item.get("normalized_data", {})
            if not isinstance(data, dict):
                continue
            self._populate_debug_defaults(data)
            if data.get("work_order_closed") is True:
                data["debug_work_order_stage_source"] = "raw_field"
                data["debug_work_order_stage_normalized"] = data.get("work_order_stage") or "审核工单"
                if not data.get("work_order_stage"):
                    data["work_order_stage"] = "审核工单"
                continue
            if not self._should_check_stage(item):
                data["debug_work_order_stage_source"] = "not_applicable"
                continue

            work_order_link = data.get("work_order_link")
            if not isinstance(work_order_link, str) or not work_order_link:
                data["debug_work_order_stage_source"] = "work_order_link_missing"
                continue

            cached = self._stage_cache.get(work_order_link)
            if cached is None:
                try:
                    cached = await self._stage_reader(work_order_link)
                except Exception:
                    cached = (None, "stage_lookup_error", None)
                self._stage_cache[work_order_link] = cached

            stage, source, raw_value = cached
            data["debug_work_order_stage_source"] = source
            data["debug_work_order_stage_raw"] = raw_value
            data["debug_work_order_stage_normalized"] = stage
            if stage:
                data["work_order_stage"] = stage
            if stage == "审核工单":
                data["work_order_closed"] = True
                data["debug_work_order_closed_normalized"] = True
        return normalized_records

    @staticmethod
    def _should_check_stage(item: dict[str, Any]) -> bool:
        data = item.get("normalized_data", {})
        if not isinstance(data, dict):
            return False
        return (
            item.get("recognition_status") != "failed"
            and "巡检" in str(data.get("service_type") or "")
            and str(data.get("executor_name") or "") == "舒磊"
            and data.get("inspection_done") is True
        )

    async def _read_stage_from_pts(self, work_order_link: str) -> tuple[str | None, str, str | None]:
        stage, source, raw_value = await self._read_stage_from_local_chrome_profile(work_order_link)
        if stage or source != "browser_session_unavailable":
            return stage, source, raw_value
        try:
            async with _PtsBrowserSession(self.settings) as session:
                open_result = await session.open_project(work_order_link)
                if open_result.get("status") != "success":
                    return None, open_result.get("error_type", "open_failed"), open_result.get("error_message")
                page_text = await session.read_page_text(limit=6000)
        except _PtsRunnerError as exc:
            return None, exc.error_type, exc.error_message
        stage, raw_marker = extract_inspection_stage_from_text(page_text)
        if stage:
            return stage, "pts_browser_session", raw_marker
        return None, "not_found", None

    async def _read_stage_from_local_chrome_profile(
        self,
        work_order_link: str,
    ) -> tuple[str | None, str, str | None]:
        profile_root = _find_local_chrome_user_data_dir()
        if profile_root is None:
            return None, "browser_session_unavailable", None

        temp_user_data_dir = Path(tempfile.mkdtemp(prefix="inspection-pts-profile-"))
        try:
            if not _copy_chrome_profile(profile_root, temp_user_data_dir):
                return None, "browser_session_unavailable", None
            try:
                from playwright.async_api import async_playwright
            except ImportError:
                return None, "browser_session_unavailable", None

            async with async_playwright() as playwright:
                context = await playwright.chromium.launch_persistent_context(
                    user_data_dir=str(temp_user_data_dir),
                    channel="chrome",
                    headless=True,
                    args=["--profile-directory=Default"],
                    ignore_https_errors=not self.settings.pts_verify_ssl,
                )
                page = await context.new_page()
                await page.goto(strip_url_fragment(work_order_link), wait_until="domcontentloaded", timeout=45000)
                await page.wait_for_timeout(2000)
                if "auth.chaitin.net/login" in page.url:
                    await context.close()
                    return None, "session_expired", "PTS 会话已失效，请重新登录 PTS 或更新 Cookie"
                page_text = await page.evaluate("document.body ? document.body.innerText.slice(0, 6000) : ''")
                await context.close()
        except Exception:
            return None, "stage_lookup_error", None
        finally:
            shutil.rmtree(temp_user_data_dir, ignore_errors=True)

        stage, raw_marker = extract_inspection_stage_from_text(page_text)
        if stage:
            return stage, "pts_local_chrome_profile", raw_marker
        return None, "not_found", None

    @staticmethod
    def _populate_debug_defaults(data: dict[str, Any]) -> None:
        data.setdefault("debug_work_order_stage_source", None)
        data.setdefault("debug_work_order_stage_raw", None)
        data.setdefault("debug_work_order_stage_normalized", data.get("work_order_stage"))


def extract_inspection_stage_from_text(text: str | None) -> tuple[str | None, str | None]:
    if not isinstance(text, str) or not text.strip():
        return None, None
    normalized_text = _WHITESPACE_RE.sub(" ", text)
    for stage, marker in _CLOSED_MARKERS:
        if marker in normalized_text:
            return stage, marker
    for stage, marker in _STAGE_MARKERS:
        if marker in normalized_text:
            return stage, marker
    return None, None
