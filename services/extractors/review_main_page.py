"""第 1 步：项目主页数据提取

通过 PTS GraphQL API 直接获取项目完整数据，然后映射为 ProjectData。
"""

from __future__ import annotations

import logging

from services.audit.schemas import ProjectData
from services.extractors.review_api_mapper import map_project_from_graphql

logger = logging.getLogger(__name__)


async def extract_project_data(client: "PtsGraphQLClient", project_id: str) -> ProjectData:
    """通过 GraphQL 查询获取项目主页数据并映射为 ProjectData

    Args:
        client: PtsGraphQLClient 实例（带限流和认证）
        project_id: PTS 交付 ID

    Returns:
        ProjectData 完整项目主页数据

    Raises:
        ValueError: 无法获取项目数据时
    """
    gql_data = await client.query_delivery_by_id(project_id)

    if gql_data and gql_data.get("product_delivery_by_id"):
        logger.info("GraphQL 提取项目数据成功: project_id=%s", project_id)
        return map_project_from_graphql(project_id, gql_data["product_delivery_by_id"])

    raise ValueError(f"无法获取项目数据：project_id={project_id}")


async def fetch_delivery_tasks(client: "PtsGraphQLClient", project_id: str) -> list[dict]:
    """获取交付任务列表（用于审核通过时间提取）

    Args:
        client: PtsGraphQLClient 实例
        project_id: PTS 交付 ID

    Returns:
        任务列表（原始 GraphQL 数据）
    """
    result = await client.query_related_delivery_task_list(project_id)

    if result and result.get("related_delivery_task_list"):
        return result["related_delivery_task_list"]

    return []