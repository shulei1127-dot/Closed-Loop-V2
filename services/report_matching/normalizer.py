from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from services.report_matching.schemas import ReportMatchingRules


DEFAULT_RULES = ReportMatchingRules(
    noise_words=(
        "巡检报告",
        "运行状态巡检",
        "运行状态",
        "巡检",
        "谛听",
        "墨攻",
        "牧云",
        "雷池",
        "已审核",
        "最终版",
        "终版",
        "定稿",
        "word",
        "pdf",
    ),
    suffix_patterns=(
        r"[-_ ]?\d{4}[.\-_]\d{1,2}[.\-_]\d{1,2}$",
        r"[-_ ]?20\d{2}年\d{1,2}月\d{1,2}日$",
        r"[-_ ]?20\d{2}\d{2}\d{2}$",
        r"[-_ ]?v\d+$",
        r"[-_ ]?版本\d+$",
        r"[-_ ]?第\d+季度$",
        r"\(\d+\)$",
    ),
    extension_whitelist=(".doc", ".docx", ".pdf"),
    temp_file_prefixes=("~$", ".~", "."),
    temp_file_suffixes=(".tmp", ".temp", ".crdownload"),
)

FILE_TYPE_MAP = {
    ".doc": "word",
    ".docx": "word",
    ".pdf": "pdf",
}

CORPORATE_SUFFIXES = (
    "股份有限公司",
    "有限责任公司",
    "有限公司",
    "集团有限公司",
    "集团股份有限公司",
)


def normalize_filename_stem(filename: str, rules: ReportMatchingRules = DEFAULT_RULES) -> str:
    stem = Path(filename).stem
    stem = unicodedata.normalize("NFKC", stem)
    stem = stem.strip()
    for noise_word in rules.noise_words:
        stem = stem.replace(noise_word, " ")
    for pattern in rules.suffix_patterns:
        stem = re.sub(pattern, " ", stem, flags=re.IGNORECASE)
    stem = re.sub(r"[()（）\[\]【】]", " ", stem)
    stem = re.sub(r"[-_]+", " ", stem)
    stem = re.sub(r"\s+", " ", stem).strip()
    return stem


def normalize_customer_name(value: str) -> str:
    text = unicodedata.normalize("NFKC", value or "").strip()
    text = re.sub(r"[()（）\[\]【】\s\-_.]", "", text)
    return text.lower()


def extract_customer_name_candidate(filename: str, rules: ReportMatchingRules = DEFAULT_RULES) -> str:
    normalized = normalize_filename_stem(filename, rules)
    if not normalized:
        return ""

    for suffix in CORPORATE_SUFFIXES:
        index = normalized.find(suffix)
        if index >= 0:
            return normalized[: index + len(suffix)].strip()

    tokens = normalized.split()
    if not tokens:
        return normalized
    return tokens[0] if len(tokens) == 1 else "".join(tokens)


def is_allowed_extension(filename: str, rules: ReportMatchingRules = DEFAULT_RULES) -> bool:
    return Path(filename).suffix.lower() in rules.extension_whitelist


def is_temporary_file(filename: str, rules: ReportMatchingRules = DEFAULT_RULES) -> bool:
    lower = filename.lower()
    return lower.startswith(rules.temp_file_prefixes) or lower.endswith(rules.temp_file_suffixes)


def file_type_for(filename: str) -> str | None:
    return FILE_TYPE_MAP.get(Path(filename).suffix.lower())


def canonicalize_filename(filename: str) -> str:
    path = Path(filename)
    stem = unicodedata.normalize("NFKC", path.stem)
    stem = re.sub(r"\(\d+\)$", "", stem).strip()
    return f"{stem}{path.suffix.lower()}"
