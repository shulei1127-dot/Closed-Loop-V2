from __future__ import annotations

from typing import Any

from core.config import Settings, get_settings
from services.report_matching.matcher import InspectionReportMatcher
from services.report_matching.scanner import InspectionReportScanner


class InspectionLocalReportBackfill:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        scanner: InspectionReportScanner | None = None,
        matcher: InspectionReportMatcher | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.scanner = scanner or InspectionReportScanner(self.settings.inspection_report_root)
        self.matcher = matcher or InspectionReportMatcher(required_file_types=("word",))
        self._files_cache = None

    def _get_files(self):
        if self._files_cache is None:
            self._files_cache = self.scanner.scan()
        return self._files_cache

    async def enrich_records(self, normalized_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not normalized_records:
            return normalized_records
        files = self._get_files()
        for item in normalized_records:
            data = item.get("normalized_data", {})
            if not isinstance(data, dict):
                continue

            service_type = str(data.get("service_type") or "")
            executor_name = str(data.get("executor_name") or "")
            is_inspection = "巡检" in service_type
            is_target_executor = executor_name == "舒磊"
            if not is_inspection or not is_target_executor:
                data["inspection_sync_state"] = "out_of_scope"
                data["report_status_hint"] = "out_of_scope"
                data["report_word_file"] = None
                continue

            if data.get("inspection_done") is not True:
                data["inspection_sync_state"] = "no_action"
                data["report_status_hint"] = "inspection_not_done"
                data["report_word_file"] = None
                continue

            customer_name = str(data.get("customer_name") or "").strip()
            match_result = self.matcher.match(customer_name, files)
            word_files = match_result.matched_files.get("word") if match_result.matched else None
            if isinstance(word_files, list) and word_files:
                data["inspection_sync_state"] = "actionable"
                data["report_status_hint"] = "report_ready"
                data["report_word_file"] = str(word_files[0])
            else:
                data["inspection_sync_state"] = "missing_report"
                data["report_status_hint"] = "missing_report"
                data["report_word_file"] = None
        return normalized_records
