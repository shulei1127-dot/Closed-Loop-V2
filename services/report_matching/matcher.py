from __future__ import annotations

from services.report_matching.normalizer import DEFAULT_RULES, normalize_customer_name
from services.report_matching.schemas import ReportFileIndexItem, ReportMatchResult, ReportMatchingRules


DEFAULT_REQUIRED_FILE_TYPES = ("word", "pdf")


class InspectionReportMatcher:
    def __init__(
        self,
        rules: ReportMatchingRules = DEFAULT_RULES,
        *,
        required_file_types: tuple[str, ...] = DEFAULT_REQUIRED_FILE_TYPES,
    ) -> None:
        self.rules = rules
        self.required_file_types = required_file_types

    def match(self, customer_name: str, files: list[ReportFileIndexItem]) -> ReportMatchResult:
        cleaned_customer_name = (customer_name or "").strip()
        normalized_customer = normalize_customer_name(cleaned_customer_name)
        if not cleaned_customer_name:
            return ReportMatchResult(
                matched=False,
                manual_required=True,
                customer_name=cleaned_customer_name,
                matched_files={},
                missing_file_types=list(self.required_file_types),
                match_strategy="no_customer_name",
                confidence=0.0,
                error_message="customer_name 为空，无法匹配巡检报告",
            )

        exact_candidates = [item for item in files if item.customer_name_candidate == cleaned_customer_name]
        if exact_candidates:
            return self._build_result(cleaned_customer_name, exact_candidates, "exact", 1.0)

        normalized_candidates = [
            item
            for item in files
            if normalize_customer_name(item.customer_name_candidate) == normalized_customer
        ]
        if normalized_candidates:
            return self._build_result(cleaned_customer_name, normalized_candidates, "normalized", 0.92)

        fuzzy_candidates = [
            item
            for item in files
            if normalized_customer
            and (
                normalized_customer in normalize_customer_name(item.customer_name_candidate)
                or normalize_customer_name(item.customer_name_candidate) in normalized_customer
            )
        ]
        if fuzzy_candidates:
            return self._build_result(cleaned_customer_name, fuzzy_candidates, "fuzzy_like", 0.75)

        return ReportMatchResult(
            matched=False,
            manual_required=True,
            customer_name=cleaned_customer_name,
            matched_files={},
            missing_file_types=list(self.required_file_types),
            match_strategy="no_match",
            confidence=0.0,
            error_message="未找到匹配的巡检报告",
        )

    def _build_result(
        self,
        customer_name: str,
        candidates: list[ReportFileIndexItem],
        strategy: str,
        confidence: float,
    ) -> ReportMatchResult:
        candidates = self._dedupe_copy_candidates(candidates)
        grouped: dict[str, list[str]] = {"word": [], "pdf": []}
        for item in candidates:
            grouped.setdefault(item.file_type, []).append(item.path)

        conflicts = [file_type for file_type, items in grouped.items() if len(items) > 1]
        missing = [file_type for file_type in self.required_file_types if not grouped.get(file_type)]

        if conflicts:
            return ReportMatchResult(
                matched=False,
                manual_required=True,
                customer_name=customer_name,
                matched_files={key: value for key, value in grouped.items() if value},
                missing_file_types=missing,
                match_strategy="multiple_candidates",
                confidence=min(confidence, 0.6),
                error_message=f"存在多个候选文件: {', '.join(conflicts)}",
            )

        if missing:
            return ReportMatchResult(
                matched=False,
                manual_required=True,
                customer_name=customer_name,
                matched_files={key: value for key, value in grouped.items() if value},
                missing_file_types=missing,
                match_strategy="missing_files",
                confidence=confidence,
                error_message=f"缺少文件类型: {', '.join(missing)}",
            )

        return ReportMatchResult(
            matched=True,
            manual_required=False,
            customer_name=customer_name,
            matched_files={key: value for key, value in grouped.items() if value},
            missing_file_types=[],
            match_strategy=strategy,
            confidence=confidence,
            error_message=None,
        )

    @staticmethod
    def _dedupe_copy_candidates(candidates: list[ReportFileIndexItem]) -> list[ReportFileIndexItem]:
        deduped: dict[tuple[str, str], ReportFileIndexItem] = {}
        for item in candidates:
            key = (item.file_type, item.canonical_filename)
            existing = deduped.get(key)
            if existing is None:
                deduped[key] = item
                continue
            # Prefer the original filename over Finder-generated copies like "(1)".
            if "(1)" in existing.filename and "(1)" not in item.filename:
                deduped[key] = item
        return list(deduped.values())
