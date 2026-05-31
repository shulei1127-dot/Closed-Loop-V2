"""规则 8：售后有效服务期偏差校验"""

from __future__ import annotations

from datetime import date, timedelta

from services.audit.schemas import DeliveryItem, ProductInfo, ProductType, RuleResult
from services.audit.product_type import extract_short_product_name
from services.audit.service_package import parse_service_packages, calculate_total_months


def rule8_service_period(
    delivery_items: list[DeliveryItem],
    products: list[ProductInfo],
    approval_time: str | None,
) -> RuleResult:
    if not approval_time:
        return RuleResult(
            rule_id=8, rule_name="售后有效服务期偏差", result="无法判定",
            message="无法获取审核通过时间",
        )

    all_saas = products and all(p.type == ProductType.saas for p in products)
    if all_saas:
        return _check_saas(products, delivery_items)

    return _check_hw_sw(delivery_items, products, approval_time)


def _add_months(d: date, months: int) -> date:
    """Add months to a date (handles month overflow like JS setMonth)."""
    total_months = d.year * 12 + d.month - 1 + months
    y = total_months // 12
    m = total_months % 12 + 1
    # clamp day to max for resulting month
    max_day = (date(y, m + 1, 1) - timedelta(days=1)).day if m < 12 else (date(y + 1, 1, 1) - timedelta(days=1)).day
    return date(y, m, min(d.day, max_day))


def _parse_date(s: str) -> date | None:
    import re
    m = re.search(r"(\d{4}-\d{2}-\d{2})", s)
    if not m:
        return None
    try:
        return date.fromisoformat(m.group(1))
    except ValueError:
        return None


def _format_date(d: date) -> str:
    return d.isoformat()


def _is_date_in_range(date_str: str, range_start: date, range_end: date) -> bool:
    d = _parse_date(date_str)
    return d is not None and range_start <= d <= range_end


def _get_service_period_range(approval_date_str: str, total_months: int, deviation: int = 3) -> tuple[date, date, date]:
    approval = _parse_date(approval_date_str)
    if approval is None:
        raise ValueError(f"Cannot parse approval date: {approval_date_str}")
    expected = _add_months(approval, total_months)
    range_start = _add_months(expected, -deviation)
    range_end = _add_months(expected, deviation)
    return expected, range_start, range_end


def _check_saas(products: list[ProductInfo], delivery_items: list[DeliveryItem]) -> RuleResult:
    is_operation = any(
        parse_service_packages(ci.text).is_operation_service
        for item in delivery_items for ci in item.config_items
    )
    if is_operation:
        return RuleResult(rule_id=8, rule_name="售后有效服务期偏差", result="通过", message="运营服务类，售后有效服务期允许为空")

    empty = [p for p in products if not p.after_sales_service_period or p.after_sales_service_period == "---"]
    if empty:
        return RuleResult(rule_id=8, rule_name="售后有效服务期偏差", result="不通过", message=f"{len(empty)}个产品无售后有效服务期")

    return RuleResult(rule_id=8, rule_name="售后有效服务期偏差", result="通过", message="SaaS/服务产品，售后有效服务期均不为空")


def _is_subscription_item(product_category: str) -> bool:
    parts = product_category.split("-")
    return "订阅" in (parts[-1] if parts else "")


def _check_hw_sw(delivery_items: list[DeliveryItem], products: list[ProductInfo], approval_time: str) -> RuleResult:
    non_saas = [p for p in products if p.type != ProductType.saas]
    details: list[str] = []
    all_pass = True
    has_undetermined = False

    # Group products by category
    by_type: dict[str, list[ProductInfo]] = {}
    for p in non_saas:
        by_type.setdefault(p.product_category, []).append(p)

    product_index = 0
    for item in delivery_items:
        is_sub = _is_subscription_item(item.product_category)
        for ci in item.config_items:
            parsed = parse_service_packages(ci.text)
            total_months = calculate_total_months(parsed)

            matching = by_type.get(item.product_category, [])
            products_for = matching[product_index:product_index + (ci.quantity or 1)]

            if total_months == 0 and is_sub:
                total_months = 12

            if total_months == 0 and not parsed.is_operation_service:
                has_undetermined = True
                details.append(f"{extract_short_product_name(item.product_category)}: 未找到服务包年限")
                continue

            if total_months == 0 and parsed.is_operation_service:
                details.append(f"{extract_short_product_name(item.product_category)}: 运营服务类，豁免")
                continue

            for product in products_for:
                period = product.after_sales_service_period
                if not period or period == "---":
                    all_pass = False
                    details.append(f"{extract_short_product_name(product.product_category)}: 无售后有效服务期")
                    continue

                expected, range_start, range_end = _get_service_period_range(approval_time, total_months)
                if _is_date_in_range(period, range_start, range_end):
                    details.append(f"{extract_short_product_name(product.product_category)}: 服务期 {period} 在 [{_format_date(range_start)}, {_format_date(range_end)}] 范围内")
                else:
                    all_pass = False
                    details.append(f"{extract_short_product_name(product.product_category)}: 服务期偏差")

    if not all_pass:
        return RuleResult(rule_id=8, rule_name="售后有效服务期偏差", result="不通过", message="; ".join(details))
    if has_undetermined:
        return RuleResult(rule_id=8, rule_name="售后有效服务期偏差", result="无法判定", message="; ".join(details))

    return RuleResult(rule_id=8, rule_name="售后有效服务期偏差", result="通过", message="; ".join(details) or "校验通过")