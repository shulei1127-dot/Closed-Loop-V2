"""待审核项目列表提取

从 PTS API 获取交付阶段为"转售后审核"的项目列表。
delivery_status 枚举值映射为中文标签。
"""

from __future__ import annotations

import logging

from services.audit.schemas import PendingProject

logger = logging.getLogger(__name__)


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


async def extract_pending_projects(
    client: "PtsGraphQLClient",
    *,
    after_sale_ids: list[str] | None = None,
) -> list[PendingProject]:
    """获取待审核项目列表

    从 PTS API 获取交付阶段为"转售后审核"的项目列表。

    Args:
        client: PtsGraphQLClient 实例（带限流和认证）
        after_sale_ids: 售后负责人 PTS 用户 ID 列表，为 None 时不按售后负责人过滤

    Returns:
        PendingProject 列表
    """
    result = await client.query_pending_delivery_list(after_sale_ids=after_sale_ids)

    if not result or not result.get("list_product_delivery"):
        logger.warning("PTS 返回的待审核列表为空")
        return []

    list_data = result["list_product_delivery"]
    items = list_data.get("data") or []

    projects: list[PendingProject] = []
    for item in items:
        project = item.get("project") or {}
        company = project.get("company") or {}
        after_sale = item.get("after_sale") or {}
        assigner = item.get("assigner") or {}
        person_in_charge = item.get("person_in_charge") or {}
        delivery_status = item.get("delivery_status", "")

        projects.append(
            PendingProject(
                project_id=item.get("id", ""),
                project_name=project.get("name", ""),
                customer_name=company.get("name", ""),
                delivery_stage=_map_delivery_stage(delivery_status),
                stage_status=_map_stage_status(delivery_status),
                after_sales_leader=after_sale.get("name", ""),
                assigner_name=assigner.get("name") or None,
                person_in_charge_name=person_in_charge.get("name") or None,
            )
        )

    total = list_data.get("total", 0)
    logger.info("获取待审核项目列表成功: total=%d, count=%d", total, len(projects))

    return projects