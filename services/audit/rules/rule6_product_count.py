"""规则 6：产品信息关联个数 = 配置项下单套数"""

from services.audit.schemas import DeliveryItem, ProductInfo, RuleResult
from services.audit.product_type import extract_short_product_name


def rule6_product_count(delivery_items: list[DeliveryItem], product_details: list[ProductInfo]) -> RuleResult:
    new_purchase_items = [item for item in delivery_items if not _is_renewal_item(item.product_category)]

    # 预期数量（按产品名累加配置项套数）
    config_count: dict[str, int] = {}
    for item in new_purchase_items:
        name = extract_short_product_name(item.product_category)
        qty = sum(ci.quantity or 1 for ci in item.config_items) if item.config_items else 1
        config_count[name] = config_count.get(name, 0) + qty

    # 实际数量（续保记录不算设备）
    product_count: dict[str, int] = {}
    for product in product_details:
        if product.is_renewal_record:
            continue
        name = extract_short_product_name(product.product_category)
        product_count[name] = product_count.get(name, 0) + 1

    # 纯续保项目
    if not new_purchase_items:
        actual_devices = len([p for p in product_details if not p.is_renewal_record])
        if actual_devices == 0:
            return RuleResult(rule_id=6, rule_name="产品个数=配置项套数", result="不通过", message="纯续保项目但无产品实例")
        return RuleResult(rule_id=6, rule_name="产品个数=配置项套数", result="通过", message=f"续保项目，{actual_devices} 台设备")

    mismatches: list[str] = []
    for name, expected in config_count.items():
        actual = product_count.get(name, 0)
        if actual != expected:
            mismatches.append(f"{name}套数{expected}但产品{actual}个")

    if mismatches:
        return RuleResult(rule_id=6, rule_name="产品个数=配置项套数", result="不通过", message=f"不匹配: {'; '.join(mismatches)}")

    summary = ", ".join(f"{name}: {count}" for name, count in config_count.items())
    return RuleResult(rule_id=6, rule_name="产品个数=配置项套数", result="通过", message=f"各类型匹配: {summary or '无新购交付项'}")


def _is_renewal_item(product_category: str) -> bool:
    parts = product_category.split("-")
    form_name = parts[-1] if parts else ""
    return "续保" in form_name