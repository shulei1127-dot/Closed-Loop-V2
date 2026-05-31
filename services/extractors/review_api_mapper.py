"""GraphQL 响应 → Python 数据模型映射

PTS 使用 GraphQL，核心查询为 DeliveryById + ProductInfoByID。
本模块将 GraphQL JSON 响应映射为 services.audit.schemas 中的 Pydantic 模型。
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from services.audit.product_type import (
    HARDWARE_KEYWORDS,
    SAAS_KEYWORDS,
    SOFTWARE_KEYWORDS,
)
from services.audit.schemas import (
    ConfigItem,
    Contact,
    DeliveryItem,
    ProductInfo,
    ProductListItem,
    ProductType,
    ProjectData,
)


# ── 交付阶段 / 阶段状态映射 ────────────────────────────────

def _map_delivery_stage(status: str) -> str:
    """将 delivery_status 枚举值映射为中文交付阶段文字"""
    mapping = {
        "to_after_sale_review": "转售后审核",
        "implement": "实施阶段",
        "after_sale": "售后阶段",
    }
    return mapping.get(status, status)


def _map_stage_status(status: str) -> str:
    """将 delivery_status 枚举值映射为中文阶段状态文字"""
    mapping = {
        "to_after_sale_review": "等待审核是否转售后",
        "implement": "实施中",
        "after_sale": "已转售后",
    }
    return mapping.get(status, status)


# ── 项目主页数据映射 ───────────────────────────────────────

def map_project_from_graphql(project_id: str, data: dict[str, Any]) -> ProjectData:
    """将 GraphQL product_delivery_by_id 响应映射为 ProjectData

    Args:
        project_id: PTS 交付 ID
        data: GraphQL product_delivery_by_id 返回的 JSON 对象

    Returns:
        ProjectData 完整项目主页数据
    """
    delivery_status = data.get("delivery_status", "")
    after_sale = data.get("after_sale") or {}
    project = data.get("project") or {}
    company = project.get("company") or {}
    assigner = data.get("assigner") or {}
    person_in_charge = data.get("person_in_charge") or {}

    return ProjectData(
        project_id=project_id,
        project_name=project.get("name", ""),
        customer_name=company.get("name", ""),
        delivery_stage=_map_delivery_stage(delivery_status),
        stage_status=_map_stage_status(delivery_status),
        after_sales_leader=after_sale.get("name", ""),
        assigner_username=assigner.get("username", ""),
        assigner_name=assigner.get("name", ""),
        person_in_charge_username=person_in_charge.get("username", ""),
        person_in_charge_name=person_in_charge.get("name", ""),
        delivery_items=_map_delivery_items(data),
        contacts=_map_contacts(data),
        products=_map_product_list(data),
        partner_delivery_type=project.get("delivery_type"),
    )


def _map_delivery_items(data: dict[str, Any]) -> list[DeliveryItem]:
    """从 item_list + product_delivery_project.products 提取交付项和配置项"""
    item_list = data.get("item_list") or []
    products = (data.get("product_delivery_project") or {}).get("products") or []

    return [
        DeliveryItem(
            row_id=item.get("id", ""),
            product_category=_format_product_category(item.get("product_detail") or {}),
            is_overdue=bool(item.get("over_guarantee", False)),
            config_items=extract_config_items(products, item.get("product_detail") or {}),
        )
        for item in item_list
    ]


def _format_product_category(product_detail: dict[str, Any]) -> str:
    """格式化商品类别为 "产品名-形态名" """
    product = product_detail.get("product") or {}
    form = product_detail.get("form") or {}
    return f"{product.get('name', '')}-{form.get('name', '')}"


def extract_config_items(
    products_data: list[dict[str, Any]] | None,
    current_product_detail: dict[str, Any],
) -> list[ConfigItem]:
    """从 products 数据中提取与当前交付项匹配的配置项

    按产品 ID 和形态 ID 精确匹配，只取当前交付项对应产品的配置项。
    配置项文本格式为 "模块名*数量"，多个模块用 "+" 连接。

    Args:
        products_data: product_delivery_project.products 数组
        current_product_detail: 当前交付项的 product_detail（含 product.id 和 form.id）

    Returns:
        ConfigItem 列表
    """
    if not products_data:
        return []

    target_product_id = (current_product_detail.get("product") or {}).get("id", "")
    target_form_id = (current_product_detail.get("form") or {}).get("id", "")

    configs: list[ConfigItem] = []
    for product in products_data:
        prod = product.get("product") or {}
        form = product.get("form") or {}
        if prod.get("id") != target_product_id or form.get("id") != target_form_id:
            continue

        infos = product.get("infos") or []
        for info in infos:
            parts: list[str] = []
            version = info.get("version") or {}
            module_groups = version.get("module_groups") or []
            for group in module_groups:
                modules = group.get("modules") or []
                for module in modules:
                    name = module.get("name", "")
                    number = module.get("number", 0)
                    parts.append(f"{name}*{number}")

            if parts:
                configs.append(
                    ConfigItem(
                        text=" + ".join(parts),
                        quantity=info.get("number", 1),
                    )
                )

    return configs


def _map_contacts(data: dict[str, Any]) -> list[Contact]:
    """提取企业联系人，优先使用 contact_list，兜底使用 company.contact"""
    contact_list = data.get("contact_list") or []
    if contact_list:
        return [
            Contact(
                name=(c.get("contact") or {}).get("name", ""),
                phone=(c.get("contact") or {}).get("phone", ""),
                email=(c.get("contact") or {}).get("email"),
            )
            for c in contact_list
        ]

    company = (data.get("project") or {}).get("company") or {}
    company_contacts = company.get("contact") or []
    if company_contacts:
        return [
            Contact(
                name=c.get("name", ""),
                phone=c.get("phone", ""),
                email=c.get("email"),
            )
            for c in company_contacts
        ]

    return []


def _map_product_list(data: dict[str, Any]) -> list[ProductListItem]:
    """从 product_info 提取产品列表项"""
    product_info = data.get("product_info") or []
    customer_name = ((data.get("project") or {}).get("company") or {}).get("name", "")

    return [
        ProductListItem(
            product_id=pi.get("id", ""),
            product_type=_format_product_category(pi.get("product_detail") or {}),
            customer=customer_name,
        )
        for pi in product_info
    ]


# ── 产品详情映射 ────────────────────────────────────────────

def map_product_from_graphql(data: dict[str, Any]) -> ProductInfo:
    """将 GraphQL productInfoByID 响应映射为 ProductInfo

    Args:
        data: GraphQL productInfoByID 返回的 JSON 对象

    Returns:
        ProductInfo 产品详情，含 is_renewal_record 检测
    """
    product_detail = data.get("product_detail") or {}
    category = (product_detail.get("form") or {}).get("name", "")
    product_name = (product_detail.get("product") or {}).get("name", "")
    product_category = f"{product_name}-{category}"

    after_info = data.get("after_info")
    summary = (after_info or {}).get("desc", "") or data.get("desc", "") if after_info else (data.get("desc", "") or "")

    # 产品类型识别：需要同时传入 category + product_name + summary
    combined_text = f"{category} {product_name} {summary}"
    product_type = _identify_product_type_combined(combined_text)

    # SaaS 产品没有硬件字段，serial_number/machine_code/type_number 天然为 null，
    # 不应仅凭硬件字段为空就判定为续保记录；只有硬件类产品才需要此检查
    is_renewal_record = False
    if after_info is None:
        is_renewal_record = True
    elif _is_after_info_all_nullish(after_info) and product_type != ProductType.saas:
        is_renewal_record = True

    return ProductInfo(
        product_id=data.get("id", ""),
        product_category=product_category,
        summary=summary,
        number=data.get("number", 1),
        is_renewal_record=is_renewal_record,
        info_type=data.get("type", ""),
        model=(after_info or {}).get("type_number", "") or "",
        serial_number=(after_info or {}).get("serial_number", "") or "",
        machine_code=(after_info or {}).get("machine_code", "") or "",
        product_version=(after_info or {}).get("product_version", "") or "",
        engine_version=(after_info or {}).get("engine_version", "") or "",
        deploy_mode=(after_info or {}).get("stage_mode", "") or "",
        is_ha=str(after_info.get("is_ha")) if after_info and after_info.get("is_ha") is not None else "",
        license_type=(after_info or {}).get("license_nature", "") or "",
        license_expiry=_format_license_validity((after_info or {}).get("license_validity")),
        license_id=(after_info or {}).get("license_id", "") or "",
        after_sales_service_period=_format_date_field((after_info or {}).get("after_sales_validity")),
        is_overdue=str(data.get("over_guarantee", "")),
        type=product_type,
    )


def _identify_product_type_combined(text: str) -> ProductType:
    """通过组合文本匹配关键词识别产品类型

    调用 services.audit.product_type.identify_product_type 需要两步：
    先将 category+name+summary 组合文本用关键词匹配。
    """
    for kw in SAAS_KEYWORDS:
        if kw in text:
            return ProductType.saas
    for kw in HARDWARE_KEYWORDS:
        if kw in text:
            return ProductType.hardware
    for kw in SOFTWARE_KEYWORDS:
        if kw in text:
            return ProductType.software
    return ProductType.hardware


def _is_after_info_all_nullish(info: dict[str, Any]) -> bool:
    """判断 after_info 的关键设备字段是否全部为 null 或字符串 "null"

    续保记录的 after_info 存在，但 serial_number/machine_code/type_number 全是 "null"。
    真实设备即使 number=0，这些字段也会有实际值。
    """
    def _nullish(v: Any) -> bool:
        return v is None or v == "null" or v == ""

    return _nullish(info.get("serial_number")) and _nullish(info.get("machine_code")) and _nullish(info.get("type_number"))


def _format_license_validity(value: str | None | Any) -> str:
    """License 有效期特殊处理：年份 < 1970 表示永久

    PTS 中永久 License 的 license_validity 字段年份为 1969 等 < 1970 的值，
    需要转换为"永久"文字。
    """
    if not value:
        return ""
    try:
        # 尝试解析为日期，检查年份
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00").split("+")[0])
        if dt.year < 1970:
            return "永久"
    except (ValueError, TypeError):
        pass
    return _format_date_field(value)


def _format_date_field(value: str | None | Any) -> str:
    """从 ISO 格式日期字符串提取 YYYY-MM-DD 部分"""
    if not value:
        return ""
    match = re.search(r"(\d{4}-\d{2}-\d{2})", str(value))
    return match.group(1) if match else str(value)