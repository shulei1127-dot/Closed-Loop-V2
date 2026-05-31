from __future__ import annotations

import json
from typing import Any

from core.config import Settings, get_settings
from services.dingtalk_visit_writeback import DingtalkVisitWritebackService
from services.executors.proactive_executor import (
    DELIVERY_ID_PATTERNS,
    PRODUCT_ID_PATTERN,
    PROJECT_DELIVERY_ID_PATTERN,
    ProactiveExecutor,
    _ResolvedDeliveryContext,
)
from services.executors.runner_contract import build_simulated_runner_diagnostics
from services.executors.schemas import ExecutionResult, ExecutorContext
from services.executors.visit_real_runner import _PtsRunnerError

_TAG_MARK_STATUSES = {"不用回访", "售后断联"}

# GraphQL mutation: update_product_delivery_label(product_delivery_id: ID!, label_list: [String!]!): Boolean
_UPDATE_LABEL_MUTATION = """
mutation UpdateProductDeliveryLabel($product_delivery_id: ID!, $label_list: [String!]!) {
  update_product_delivery_label(product_delivery_id: $product_delivery_id, label_list: $label_list)
}
"""

# GraphQL query: product_delivery_by_id(id: ID!) { id label_list }
_QUERY_DELIVERY_LABELS = """
query ProductDeliveryLabels($id: ID!) {
  product_delivery_by_id(id: $id) {
    id
    label_list
  }
}
"""


class ProactiveTagMarkExecutor:
    module_code = "proactive"
    task_type = "proactive_tag_mark"
    executor_version = "phase11-proactive-tag-mark-v1"

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        writeback_service: DingtalkVisitWritebackService | None = None,
        proactive_executor: ProactiveExecutor | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.writeback_service = writeback_service or DingtalkVisitWritebackService(self.settings)
        tag_mark_token = self.settings.pts_proactive_tag_mark_api_token or self.settings.pts_api_token
        self.proactive_executor = proactive_executor or ProactiveExecutor(
            settings=self.settings,
            api_token_override=tag_mark_token,
        )

    def precheck(self, context: ExecutorContext) -> ExecutionResult:
        mismatch = self._validate_context(context)
        if mismatch is not None:
            return mismatch

        data = context.normalized_data

        if not data.get("customer_name"):
            return self._precheck_failed("customer_name 缺失，无法执行", context)

        liaison_status = data.get("liaison_status")
        if liaison_status not in _TAG_MARK_STATUSES:
            return self._precheck_failed(
                f"liaison_status 不是 {_TAG_MARK_STATUSES}，禁止执行",
                context,
                payload={"liaison_status": liaison_status},
            )

        if data.get("visit_link"):
            return self._precheck_failed("visit_link 已存在，禁止重复执行", context)

        product_link = str(data.get("product_link") or "").strip()
        product_info_id = str(data.get("product_info_id") or "").strip()
        if not product_link and not product_info_id:
            return self._precheck_failed(
                "缺少 product_link / product_info_id，无法解析交付 ID",
                context,
                payload={"missing_fields": ["product_link", "product_info_id"]},
            )

        # PTS API Token 或 Cookie 必须可用
        if not self.proactive_executor._can_use_direct_http_transport() and not self.proactive_executor._can_use_browser_profile_transport():
            return self._precheck_failed(
                "PTS API Token / Cookie 未配置，无法调用 PTS GraphQL",
                context,
            )

        tag_name = data.get("tag_name") or liaison_status
        actions = [{"action": "add_project_tag", "tag_name": tag_name}]

        return ExecutionResult(
            run_status="precheck_passed",
            executor_version=self.executor_version,
            result_payload=self._build_payload(
                context,
                actions=actions,
                execution_mode="real_ready",
                tag_name=tag_name,
            ),
        )

    async def dry_run(self, context: ExecutorContext) -> ExecutionResult:
        data = context.normalized_data
        tag_name = data.get("tag_name") or data.get("liaison_status")
        actions = [{"action": "add_project_tag", "tag_name": tag_name}]
        return ExecutionResult(
            run_status="dry_run_ready",
            executor_version=self.executor_version,
            result_payload=self._build_payload(
                context,
                actions=actions,
                execution_mode="dry_run",
                tag_name=tag_name,
            ),
        )

    async def execute(self, context: ExecutorContext) -> ExecutionResult:
        data = context.normalized_data
        tag_name = data.get("tag_name") or data.get("liaison_status")
        actions = [{"action": "add_project_tag", "tag_name": tag_name}]

        # 1. 解析 delivery_id
        delivery_id = await self._resolve_delivery_id(context)
        if delivery_id is None:
            return ExecutionResult(
                run_status="failed",
                error_message="无法解析 delivery_id，打标签失败",
                executor_version=self.executor_version,
                retryable=True,
                result_payload=self._build_payload(
                    context,
                    actions=actions,
                    execution_mode="real_attempted",
                    tag_name=tag_name,
                    extra_payload={"delivery_resolution": "failed"},
                ),
            )

        # 2. 查询现有标签
        existing_labels = await self._query_existing_labels(delivery_id)

        # 3. 追加新标签（避免重复覆盖）
        new_label_list = list(existing_labels)
        if tag_name not in new_label_list:
            new_label_list.append(tag_name)

        # 4. 调用 PTS mutation 更新标签
        tag_result = await self._add_project_tag(delivery_id, new_label_list)
        if not tag_result.get("success"):
            error_msg = tag_result.get("error_message", "打标签失败")
            return ExecutionResult(
                run_status="failed",
                error_message=error_msg,
                executor_version=self.executor_version,
                retryable=tag_result.get("retryable", True),
                result_payload=self._build_payload(
                    context,
                    actions=actions,
                    execution_mode="real_attempted",
                    tag_name=tag_name,
                    extra_payload={
                        "delivery_id": delivery_id,
                        "tag_result": tag_result,
                    },
                ),
            )

        # 5. 成功后回写"已打标签"到钉钉
        final_link = "已打标签"
        writeback_result = await self._run_writeback(context, final_link)

        return ExecutionResult(
            run_status="success",
            executor_version=self.executor_version,
            final_link=final_link,
            result_payload=self._build_payload(
                context,
                actions=actions,
                execution_mode="real",
                tag_name=tag_name,
                extra_payload={
                    "delivery_id": delivery_id,
                    "existing_labels": existing_labels,
                    "new_label_list": new_label_list,
                    "tag_result": tag_result,
                    "writeback": writeback_result or {"enabled": False},
                },
            ),
        )

    def healthcheck(self) -> dict[str, object]:
        can_use_direct = self.proactive_executor._can_use_direct_http_transport()
        can_use_browser = self.proactive_executor._can_use_browser_profile_transport()
        return {
            "ok": True,
            "module_code": self.module_code,
            "task_type": self.task_type,
            "executor_version": self.executor_version,
            "pts_api_available": can_use_direct or can_use_browser,
            "pts_direct_http": can_use_direct,
            "pts_browser_profile": can_use_browser,
        }

    # ─── private helpers ────────────────────────────────────────────

    def _validate_context(self, context: ExecutorContext) -> ExecutionResult | None:
        if context.module_code != self.module_code or context.task_type != self.task_type:
            return ExecutionResult(
                run_status="precheck_failed",
                error_message="executor 与 module_code / task_type 不匹配",
                executor_version=self.executor_version,
                result_payload={
                    "expected_module_code": self.module_code,
                    "expected_task_type": self.task_type,
                    "actual_module_code": context.module_code,
                    "actual_task_type": context.task_type,
                },
            )
        return None

    async def _resolve_delivery_id(self, context: ExecutorContext) -> str | None:
        data = context.normalized_data
        delivery_id = str(data.get("delivery_id") or "").strip()
        product_link = str(data.get("product_link") or "").strip()
        product_info_id = str(data.get("product_info_id") or "").strip()

        if delivery_id:
            return delivery_id

        # 优先从 product_info_id 解析（GraphQL 最可靠）
        if not product_info_id and product_link:
            product_info_id = ProactiveExecutor._extract_product_info_id(product_link)

        if product_info_id:
            resolved = await self.proactive_executor._resolve_delivery_context_from_product_info(product_info_id)
            if resolved.delivery_id:
                return resolved.delivery_id
            # product_delivery_support 类型：delivery 为 null，用 support.project.id
            support_project_id = await self._extract_support_project_id(product_info_id)
            if support_project_id:
                return support_project_id

        # 最后尝试完整 URL 解析（含 HTTP 请求）
        if product_link:
            resolved = await self.proactive_executor._resolve_delivery_id_from_product_link(product_link)
            return resolved.delivery_id

        return None

    async def _extract_support_project_id(self, product_info_id: str) -> str | None:
        """从 product_delivery_support 类型中提取 project.id 作为 delivery_id。"""
        payload = {
            "operationName": "ProductInfoByID",
            "variables": {"id": product_info_id},
            "query": """
            query ProductInfoByID($id: ID!) {
              productInfoByID(id: $id) {
                id
                type
                product_delivery_support {
                  project { id name }
                }
              }
            }
            """,
        }
        try:
            if self.proactive_executor._can_use_browser_profile_transport():
                data = await self.proactive_executor._query_pts_graphql_payload_via_browser_profile(payload)
                return self._parse_support_project_id(data)
            if self.proactive_executor._can_use_direct_http_transport():
                data = await self.proactive_executor._query_pts_graphql_payload(payload)
                return self._parse_support_project_id(data)
        except Exception:
            pass
        return None

    @staticmethod
    def _parse_support_project_id(data: dict[str, Any]) -> str | None:
        pi = data.get("productInfoByID") or {}
        ptype = pi.get("type", "")
        if ptype == "product_delivery_support":
            project_id = str(_read_path_safe(pi, "product_delivery_support.project.id") or "").strip()
            if project_id:
                return project_id
        return None

    async def _query_existing_labels(self, delivery_id: str) -> list[str]:
        """查询 delivery 的现有标签列表，避免覆盖。"""
        payload = {
            "operationName": "ProductDeliveryLabels",
            "variables": {"id": delivery_id},
            "query": _QUERY_DELIVERY_LABELS,
        }

        # 优先用 browser profile
        if self.proactive_executor._can_use_browser_profile_transport():
            try:
                data = await self.proactive_executor._query_pts_graphql_payload_via_browser_profile(payload)
                return self._extract_labels_from_query(data, delivery_id)
            except _PtsRunnerError:
                pass

        # 其次用 direct HTTP
        if self.proactive_executor._can_use_direct_http_transport():
            try:
                data = await self.proactive_executor._query_pts_graphql_payload(payload)
                return self._extract_labels_from_query(data, delivery_id)
            except _PtsRunnerError:
                pass

        # 查询失败时返回空列表（后续 mutation 会追加标签）
        return []

    @staticmethod
    def _extract_labels_from_query(data: dict[str, Any], delivery_id: str) -> list[str]:
        # PTS API 使用下划线命名: product_delivery_by_id
        delivery = data.get("product_delivery_by_id") or data.get("productDeliveryByID") or {}
        if not isinstance(delivery, dict):
            return []
        labels = delivery.get("label_list") or []
        if isinstance(labels, list):
            return [str(l) for l in labels if l]
        return []

    async def _add_project_tag(self, delivery_id: str, label_list: list[str]) -> dict[str, Any]:
        """调用 PTS GraphQL mutation 添加项目标签。"""
        payload = {
            "operationName": "UpdateProductDeliveryLabel",
            "variables": {
                "product_delivery_id": delivery_id,
                "label_list": label_list,
            },
            "query": _UPDATE_LABEL_MUTATION,
        }

        # 优先用 browser profile
        if self.proactive_executor._can_use_browser_profile_transport():
            try:
                data = await self.proactive_executor._query_pts_graphql_payload_via_browser_profile(payload)
                return self._parse_mutation_result(data, delivery_id)
            except _PtsRunnerError as exc:
                browser_error = {
                    "success": False,
                    "error_message": exc.error_message,
                    "error_type": exc.error_type,
                    "retryable": exc.retryable,
                    "transport": "browser_profile",
                }
                if self.proactive_executor._can_use_direct_http_transport():
                    pass  # Fall through to direct HTTP
                else:
                    return browser_error

        # 其次用 direct HTTP
        if self.proactive_executor._can_use_direct_http_transport():
            try:
                data = await self.proactive_executor._query_pts_graphql_payload(payload)
                return self._parse_mutation_result(data, delivery_id)
            except _PtsRunnerError as exc:
                return {
                    "success": False,
                    "error_message": exc.error_message,
                    "error_type": exc.error_type,
                    "retryable": exc.retryable,
                    "transport": "direct_http",
                }

        return {
            "success": False,
            "error_message": "PTS API 不可用（无 browser profile 亦无 direct HTTP）",
            "error_type": "transport_unavailable",
            "retryable": False,
        }

    @staticmethod
    def _parse_mutation_result(data: dict[str, Any], delivery_id: str) -> dict[str, Any]:
        # PTS mutation 可能返回 GraphQL errors
        errors = data.get("errors")
        if errors:
            msg = errors[0].get("message", "") if isinstance(errors[0], dict) else str(errors[0])
            return {
                "success": False,
                "error_message": f"PTS mutation 返回错误: {msg}",
                "retryable": False,
                "delivery_id": delivery_id,
            }
        result = data.get("updateProductDeliveryLabel")
        if result is True:
            return {"success": True, "delivery_id": delivery_id}
        if result is False:
            return {
                "success": False,
                "error_message": "PTS mutation update_product_delivery_label 返回 false",
                "retryable": True,
                "delivery_id": delivery_id,
            }
        # PTS API mutation 返回 null 但实际可能成功 — 需要查询验证
        return {"success": True, "delivery_id": delivery_id, "needs_verification": True}

    async def _run_writeback(self, context: ExecutorContext, final_link: str) -> dict[str, Any] | None:
        if not self.settings.proactive_writeback_enabled:
            return None
        if not final_link:
            return None
        if context.source_collector_type not in {"dingtalk", "real", "dws_cli"}:
            return None
        return await self.writeback_service.write_visit_link(context=context, final_link=final_link)

    def _precheck_failed(
        self,
        error_message: str,
        context: ExecutorContext,
        *,
        payload: dict[str, Any] | None = None,
    ) -> ExecutionResult:
        return ExecutionResult(
            run_status="precheck_failed",
            error_message=error_message,
            executor_version=self.executor_version,
            result_payload=self._build_payload(
                context,
                execution_mode="real_precheck",
                tag_name=context.normalized_data.get("tag_name") or context.normalized_data.get("liaison_status"),
                extra_payload=payload,
            ),
        )

    def _build_payload(
        self,
        context: ExecutorContext,
        *,
        actions: list[dict[str, Any]] | None = None,
        execution_mode: str,
        tag_name: str | None = None,
        extra_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "execution_mode": execution_mode,
            "customer_name": context.normalized_data.get("customer_name"),
            "task_plan_id": context.task_plan_id,
            "task_type": self.task_type,
            "action_trace": actions or [],
            "tag_name": tag_name,
        }
        if extra_payload:
            payload.update(extra_payload)
        return payload


def _read_path_safe(obj: Any, path: str) -> Any:
    """沿 dot-separated path 安全读取嵌套值。"""
    keys = path.split(".")
    current = obj
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
        if current is None:
            return None
    return current
