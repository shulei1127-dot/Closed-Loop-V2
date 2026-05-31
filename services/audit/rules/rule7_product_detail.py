"""规则 7：产品详情信息完整性校验"""

from services.audit.schemas import ProductInfo, ProductType, RuleResult
from services.audit.product_type import (
    extract_short_product_name, get_key_product_name,
    is_key_product, is_mainstream_product,
)

PLACEHOLDER_VALUES = {"1", "2", "NA", "N/A", "无", "---", "", "undefined", "null"}

INFO_TYPE_MAP = {"delivery": "交付", "test": "测试"}
LICENSE_TYPE_MAP = {
    "formal_delivery_permanent": "正式交付-永久",
    "formal_delivery_non_permanent": "正式交付-非永久",
    "formal_delivery_non_permanent_license": "正式交付-非永久",
    "formal_delivery_not_permanent": "正式交付-非永久",
}
VALID_INFO_TYPES = ["交付", "delivery"]
VALID_LICENSE_TYPES = [
    "正式交付-永久", "正式交付-非永久",
    "formal_delivery_permanent", "formal_delivery_non_permanent",
    "formal_delivery_not_permanent",
]


def _is_empty(value: str | None) -> bool:
    if value is None:
        return True
    return value.strip() in PLACEHOLDER_VALUES


def rule7_product_detail(products: list[ProductInfo]) -> RuleResult:
    details: list[str] = []
    all_pass = True

    for product in products:
        label = extract_short_product_name(product.product_category or product.product_id)

        # 续保记录：只检查售后有效服务期
        if product.is_renewal_record:
            if _is_empty(product.after_sales_service_period):
                details.append(f"{label}(续保): 无售后有效服务期")
                all_pass = False
            else:
                details.append(f"{label}(续保): 通过")
            continue

        # SaaS：自动通过
        if product.type == ProductType.saas:
            details.append(f"{label}: SaaS/服务产品，自动通过")
            continue

        issues: list[str] = []

        # 关联信息
        if product.info_type and product.info_type not in VALID_INFO_TYPES:
            display = INFO_TYPE_MAP.get(product.info_type, product.info_type)
            issues.append(f'关联信息测试/交付为"{display}"，必须为"交付"')

        # 主流产品软硬件信息
        if is_mainstream_product(product):
            hw_issues = _check_hw_sw_info(product)
            issues.extend(hw_issues)

        # 关键产品差异化校验
        if is_key_product(product):
            completeness_issues = _check_by_product(product)
            issues.extend(completeness_issues)

        # License 信息
        license_issues = _check_license(product)
        issues.extend(license_issues)

        if issues:
            all_pass = False
            details.append(f"{label}: {'，'.join(issues)}")
        else:
            extra = "，信息完整" if is_key_product(product) else ""
            details.append(f"{label}: 通过{extra}")

    return RuleResult(
        rule_id=7, rule_name="产品详情完整性",
        result="通过" if all_pass else "不通过",
        message="; ".join(details),
    )


def _check_hw_sw_info(product: ProductInfo) -> list[str]:
    issues: list[str] = []
    if product.type == ProductType.hardware:
        if _is_empty(product.serial_number):
            issues.append("无序列号")
        if _is_empty(product.machine_code) and not _has_combined_machine_code(product.serial_number):
            issues.append("无机器码")
    if product.type == ProductType.software:
        if _is_empty(product.machine_code):
            issues.append("无机器码")
    if _is_empty(product.model):
        issues.append("无型号")
    if _is_empty(product.product_version):
        issues.append("无产品版本")
    return issues


def _check_license(product: ProductInfo) -> list[str]:
    issues: list[str] = []
    if _is_empty(product.license_type):
        issues.append("无License性质")
    elif product.license_type not in VALID_LICENSE_TYPES:
        issues.append('License性质非"正式交付-永久"或"正式交付-非永久"')

    if _is_empty(product.license_expiry):
        issues.append("无License有效期")
    if _is_empty(product.license_id):
        issues.append("无License ID")
    return issues


def _check_by_product(product: ProductInfo) -> list[str]:
    issues: list[str] = []
    name = get_key_product_name(product)

    if name == "雷池":
        if _is_empty(product.engine_version): issues.append("无引擎版本")
        if _is_empty(product.deploy_mode): issues.append("无部署模式")
        if _is_empty(product.is_ha): issues.append("无是否HA")
        if _is_empty(product.after_sales_service_period): issues.append("无售后有效服务期")
        if _is_empty(product.is_overdue): issues.append("无是否过保")
    elif name == "洞鉴":
        if _is_empty(product.engine_version): issues.append("无引擎版本")
        if _is_empty(product.after_sales_service_period): issues.append("无售后有效服务期")
        if _is_empty(product.is_overdue): issues.append("无是否过保")
    elif name in ("牧云", "谛听", "全悉", "万象"):
        if _is_empty(product.after_sales_service_period): issues.append("无售后有效服务期")
        if _is_empty(product.is_overdue): issues.append("无是否过保")

    return issues


def _has_combined_machine_code(serial_number: str | None) -> bool:
    if not serial_number:
        return False
    parts = serial_number.split(",")
    if len(parts) >= 2:
        second = parts[1].strip()
        return len(second) > 0 and second not in PLACEHOLDER_VALUES
    return False