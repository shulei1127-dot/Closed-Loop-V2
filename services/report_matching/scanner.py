from __future__ import annotations

from pathlib import Path

from services.report_matching.normalizer import (
    DEFAULT_RULES,
    canonicalize_filename,
    extract_customer_name_candidate,
    file_type_for,
    is_allowed_extension,
    is_temporary_file,
    normalize_filename_stem,
)
from services.report_matching.schemas import ReportFileIndexItem, ReportMatchingRules


class InspectionReportScanner:
    def __init__(self, root_path: str | Path, rules: ReportMatchingRules = DEFAULT_RULES) -> None:
        self.root_path = Path(root_path)
        self.rules = rules

    def scan(self) -> list[ReportFileIndexItem]:
        if not self.root_path.exists():
            return []

        items: list[ReportFileIndexItem] = []
        for path in sorted(self.root_path.rglob("*")):
            if not path.is_file():
                continue
            filename = path.name
            if is_temporary_file(filename, self.rules):
                continue
            if not is_allowed_extension(filename, self.rules):
                continue
            file_type = file_type_for(filename)
            if file_type is None:
                continue
            items.append(
                ReportFileIndexItem(
                    path=str(path),
                    filename=filename,
                    canonical_filename=canonicalize_filename(filename),
                    extension=path.suffix.lower(),
                    file_type=file_type,
                    normalized_name=normalize_filename_stem(filename, self.rules),
                    customer_name_candidate=extract_customer_name_candidate(filename, self.rules),
                    is_archived="已上传的文档" in path.parts,
                )
            )
        return items
