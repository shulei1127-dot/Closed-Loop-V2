"""数据提取器 — GraphQL 响应到 Python 模型映射"""

from .review_api_mapper import (
    extract_config_items,
    map_product_from_graphql,
    map_project_from_graphql,
)
from .review_main_page import extract_project_data
from .review_product_detail import extract_product_detail
from .review_project_list import extract_pending_projects
from .review_approval_time import extract_approval_time

__all__ = [
    "extract_config_items",
    "map_product_from_graphql",
    "map_project_from_graphql",
    "extract_project_data",
    "extract_product_detail",
    "extract_pending_projects",
    "extract_approval_time",
]
