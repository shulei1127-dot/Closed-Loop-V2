"""第 2 步：产品详情数据提取

通过 PTS GraphQL API 直接获取产品详情（含 Meta 字段：序列号、机器码、服务期等）。
"""

from __future__ import annotations

import logging

from services.audit.schemas import ProductInfo
from services.extractors.review_api_mapper import map_product_from_graphql

logger = logging.getLogger(__name__)


async def extract_product_detail(client: "PtsGraphQLClient", product_id: str) -> ProductInfo | None:
    """通过 GraphQL 查询获取产品详情并映射为 ProductInfo

    Args:
        client: PtsGraphQLClient 实例（带限流和认证）
        product_id: PTS 产品信息 ID

    Returns:
        ProductInfo 产品详情，查询失败时返回 None

    Raises:
        ValueError: 无法获取产品详情时
    """
    result = await client.query_product_info_by_id(product_id)

    if result and result.get("productInfoByID"):
        logger.info("GraphQL 提取产品详情成功: product_id=%s", product_id)
        return map_product_from_graphql(result["productInfoByID"])

    raise ValueError(f"无法获取产品详情：product_id={product_id}")