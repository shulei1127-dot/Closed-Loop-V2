"""规则 4：识别配置项关键字段"""

from services.audit.schemas import DeliveryItem, ProductInfo, ProductType, RuleResult
from services.audit.product_type import extract_short_product_name
from services.audit.service_package import parse_service_packages


def rule4_config_keywords(delivery_items: list[DeliveryItem], products: list[ProductInfo]) -> RuleResult:
    all_saas = products and all(p.type == ProductType.saas for p in products)

    if all_saas:
        return RuleResult(
            rule_id=4, rule_name="识别配置项关键字段", result="通过",
            message="SaaS/服务产品，配置项不为空即可",
        )

    all_results: list[str] = []
    has_service_package = False
    has_unrecognized = False

    for item in delivery_items:
        is_sub = _is_subscription_item(item.product_category)
        for ci in item.config_items:
            parsed = parse_service_packages(ci.text)
            total_months = parsed.years * 12 + parsed.months

            if total_months > 0:
                has_service_package = True
                months_str = f"+{parsed.months}月" if parsed.months else ""
                all_results.append(
                    f"{extract_short_product_name(item.product_category)}: {parsed.years}年{months_str} ({total_months}个月)"
                )
            elif is_sub:
                has_service_package = True
                all_results.append(f"{extract_short_product_name(item.product_category)}: 订阅1年 (12个月)")

            if parsed.unrecognized:
                has_unrecognized = True
                all_results.append(f"未识别: {', '.join(parsed.unrecognized)}")

    if has_service_package:
        return RuleResult(
            rule_id=4, rule_name="识别配置项关键字段", result="通过",
            message="; ".join(all_results),
        )

    if has_unrecognized:
        return RuleResult(
            rule_id=4, rule_name="识别配置项关键字段", result="无法判定",
            message="未匹配到服务包年限",
        )

    return RuleResult(
        rule_id=4, rule_name="识别配置项关键字段", result="无法判定",
        message="未找到服务包年限",
    )


def _is_subscription_item(product_category: str) -> bool:
    parts = product_category.split("-")
    form_name = parts[-1] if parts else ""
    return "订阅" in form_name