from __future__ import annotations

from pydantic import BaseModel, Field


class ReportMatchingRules(BaseModel):
    noise_words: tuple[str, ...]
    suffix_patterns: tuple[str, ...]
    extension_whitelist: tuple[str, ...]
    temp_file_prefixes: tuple[str, ...]
    temp_file_suffixes: tuple[str, ...]


class ReportFileIndexItem(BaseModel):
    path: str
    filename: str
    canonical_filename: str
    extension: str
    file_type: str
    normalized_name: str
    customer_name_candidate: str
    is_archived: bool = False


class ReportMatchResult(BaseModel):
    matched: bool
    manual_required: bool
    customer_name: str
    matched_files: dict[str, list[str]] = Field(default_factory=dict)
    missing_file_types: list[str] = Field(default_factory=list)
    match_strategy: str
    confidence: float
    error_message: str | None = None
