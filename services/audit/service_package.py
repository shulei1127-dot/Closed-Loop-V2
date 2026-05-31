"""服务包解析 — 从配置项文本中提取年限/月数"""

from __future__ import annotations

import re

from services.audit.schemas import ServicePackageResult

_KNOWN_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"标准服务包[（(](\d+)年[）)]"), "standard"),
    (re.compile(r"标准服务包（续保）\*(\d+)"), "standard_renewal"),
    (re.compile(r"标准服务包（续保/月）\*(\d+)"), "standard_renewal_month"),
    (re.compile(r"高级服务包（续保一）\*(\d+)"), "advanced_renewal1"),
    (re.compile(r"高级服务包（续保二）\*(\d+)"), "advanced_renewal2"),
    (re.compile(r"设备租赁.*?月.*?\*(\d+)"), "rental_month"),
    (re.compile(r"设备租用.*?月.*?\*(\d+)"), "rental_month"),
    (re.compile(r"订阅.*?年\*(\d+)"), "subscription_year"),
    (re.compile(r"产品巡检服务.*?(\d+)次"), "inspection"),
    (re.compile(r"产品巡检服务（套）.*?(\d+)套"), "inspection_set"),
    (re.compile(r"日志分析服务.*?(\d+)次"), "log_analysis"),
    (re.compile(r"互联网暴露面检测评估服务"), "operation_service"),
    (re.compile(r"产品运营服务"), "operation_service"),
    (re.compile(r"（服务框架）单次服务"), "operation_service"),
]


def parse_service_packages(config_text: str) -> ServicePackageResult:
    result = ServicePackageResult(unrecognized=[])

    parts = [p.strip() for p in config_text.split("+") if p.strip()]

    for part in parts:
        matched = False
        for pattern, category in _KNOWN_PATTERNS:
            m = pattern.search(part)
            if m:
                matched = True
                n = int(m.group(1)) if m.lastindex else 0
                _apply_category(result, category, n)
                break
        if not matched and not _is_known_non_service_part(part):
            result.unrecognized.append(part)

    return result


def _apply_category(result: ServicePackageResult, category: str, n: int) -> None:
    if category == "standard":
        result.years += n
    elif category == "standard_renewal":
        result.years += n
    elif category == "standard_renewal_month":
        result.months += n
    elif category == "advanced_renewal1":
        result.advanced_renewal1_years += n
        result.years += n
        result.has_value_added_service = True
    elif category == "advanced_renewal2":
        result.advanced_renewal2_years += n
        result.years += n
        result.has_value_added_service = True
    elif category == "rental_month":
        result.months += n
    elif category == "subscription_year":
        result.years += n
    elif category == "inspection":
        result.inspection_count += n
        result.has_value_added_service = True
    elif category == "inspection_set":
        result.inspection_set_count += n
        result.has_value_added_service = True
    elif category == "log_analysis":
        result.log_analysis_count += n
        result.has_value_added_service = True
    elif category == "operation_service":
        result.is_operation_service = True


def _is_known_non_service_part(part: str) -> bool:
    if re.match(r"^[A-Z]{2,3}-[A-Z0-9]+", part):
        return True
    if re.match(r"^\*?\d+$", part):
        return True
    if "安装服务包" in part:
        return True
    if "-系统*" in part or "-设备*" in part:
        return True
    if "集中管理平台" in part:
        return True
    return False


def calculate_total_months(result: ServicePackageResult) -> int:
    return result.years * 12 + result.months


def extract_quantity(config_text: str) -> int:
    m = re.search(r"(\d+)套", config_text)
    return int(m.group(1)) if m else 0
