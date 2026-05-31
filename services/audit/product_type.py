"""产品类型识别 — 关键词匹配"""

from __future__ import annotations

from services.audit.schemas import ProductInfo, ProductType

SAAS_KEYWORDS = [
    "云图", "百川云", "大观", "无锋", "MSS标准版",
    "互联网暴露面检测评估服务", "虚拟坐席重保版", "产品运营服务",
]
HARDWARE_KEYWORDS = ["硬件版", "硬件租用版"]
SOFTWARE_KEYWORDS = ["软件版", "软件订阅版"]

KEY_PRODUCT_KEYWORDS = ["雷池", "洞鉴", "牧云", "谛听", "全悉", "万象"]
MAINSTREAM_PRODUCT_KEYWORDS = [
    "雷池", "洞鉴", "牧云", "万象", "全悉", "谛听",
    "长亭运维审计", "长亭流量分析预警", "长亭日志审计", "长亭网页防篡改",
    "长亭第二代防火墙", "长亭漏洞管理", "长亭上网行为审计",
    "长亭数据库审计", "长亭安全认证网关", "入侵检测防御",
]


def identify_product_type(category: str, summary: str) -> ProductType:
    text = f"{category} {summary}"
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


def is_mainstream_product(product: ProductInfo) -> bool:
    text = product.product_category or product.summary or ""
    return any(kw in text for kw in MAINSTREAM_PRODUCT_KEYWORDS)


def is_key_product(product: ProductInfo) -> bool:
    text = product.summary or product.product_category
    return any(kw in text for kw in KEY_PRODUCT_KEYWORDS)


def get_key_product_name(product: ProductInfo) -> str:
    text = product.summary or product.product_category or ""
    for name in KEY_PRODUCT_KEYWORDS:
        if name in text:
            return name
    return ""


def extract_short_product_name(product_category: str) -> str:
    for kw in KEY_PRODUCT_KEYWORDS:
        if kw in product_category:
            return kw
    dash_idx = product_category.find("-")
    return product_category[:dash_idx] if dash_idx > 0 else product_category
