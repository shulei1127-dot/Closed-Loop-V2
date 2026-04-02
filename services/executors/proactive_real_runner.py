from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, Field

from core.config import Settings
from services.executors.runner_contract import (
    apply_validation_result,
    build_runner_diagnostics,
    mark_runner_failure,
    mark_runner_success,
    normalize_action_result,
    refresh_runner_diagnostics,
)
from services.executors.schemas import ExecutorContext


class ProactiveRealRunOutcome(BaseModel):
    run_status: str
    final_link: str | None = None
    error_message: str | None = None
    retryable: bool = False
    action_results: list[dict[str, Any]] = Field(default_factory=list)
    runner_diagnostics: dict[str, Any] = Field(default_factory=dict)


class ProactiveRealRunner:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def validate(self) -> tuple[bool, dict[str, Any], str | None]:
        diagnostics = self._base_diagnostics()
        missing_fields = []
        if not self.settings.proactive_real_base_url:
            missing_fields.append("proactive_real_base_url")
        if not self.settings.proactive_real_create_endpoint:
            missing_fields.append("proactive_real_create_endpoint")
        if not self.settings.proactive_real_assign_endpoint_template:
            missing_fields.append("proactive_real_assign_endpoint_template")
        if not self.settings.proactive_real_feedback_endpoint_template:
            missing_fields.append("proactive_real_feedback_endpoint_template")
        if not self.settings.proactive_real_token:
            missing_fields.append("proactive_real_token")
        apply_validation_result(diagnostics, missing_fields)
        if missing_fields:
            return False, diagnostics, "proactive 真实执行配置缺失"
        return True, diagnostics, None

    async def run(self, context: ExecutorContext, actions: list[dict[str, Any]]) -> ProactiveRealRunOutcome:
        valid, diagnostics, error_message = self.validate()
        if not valid:
            return ProactiveRealRunOutcome(
                run_status="failed",
                error_message=error_message,
                retryable=False,
                runner_diagnostics=diagnostics,
            )

        headers = {self.settings.proactive_real_token_header: self.settings.proactive_real_token}
        action_results: list[dict[str, Any]] = []
        try:
            async with httpx.AsyncClient(
                base_url=self.settings.proactive_real_base_url,
                timeout=self.settings.proactive_real_timeout_seconds,
                verify=self.settings.proactive_real_verify_ssl,
                headers=headers,
            ) as client:
                create_result = normalize_action_result(await self._create_work_order(client, context, actions[0]))
                action_results.append(create_result)
                if create_result["status"] != "success":
                    return self._failure_outcome(
                        diagnostics=diagnostics,
                        action_results=action_results,
                        action_result=create_result,
                        fallback_message="创建 proactive 工单失败",
                    )

                final_link = create_result.get("final_link")
                work_order_id = _resolve_work_order_id(final_link)
                if not work_order_id:
                    action_results = refresh_runner_diagnostics(diagnostics, action_results)
                    mark_runner_failure(
                        diagnostics,
                        error_type="response_invalid",
                        failed_action="create_proactive_work_order",
                        last_error="无法解析 proactive work_order_id",
                    )
                    return ProactiveRealRunOutcome(
                        run_status="failed",
                        error_message="无法解析 proactive 工单标识",
                        retryable=False,
                        action_results=action_results,
                        runner_diagnostics=diagnostics,
                    )

                assign_result = normalize_action_result(
                    await self._assign_owner(client, context, actions[1], work_order_id)
                )
                action_results.append(assign_result)
                if assign_result["status"] != "success":
                    return self._failure_outcome(
                        diagnostics=diagnostics,
                        action_results=action_results,
                        action_result=assign_result,
                        fallback_message="指派 proactive 负责人失败",
                    )

                feedback_result = normalize_action_result(
                    await self._fill_feedback(client, context, actions[2], work_order_id, final_link)
                )
                action_results.append(feedback_result)
                if feedback_result["status"] != "success":
                    return self._failure_outcome(
                        diagnostics=diagnostics,
                        action_results=action_results,
                        action_result=feedback_result,
                        fallback_message="写入 proactive 反馈失败",
                    )

                action_results = refresh_runner_diagnostics(diagnostics, action_results)
                mark_runner_success(diagnostics)
                return ProactiveRealRunOutcome(
                    run_status="success",
                    final_link=final_link,
                    retryable=False,
                    action_results=action_results,
                    runner_diagnostics=diagnostics,
                )
        except httpx.TimeoutException as exc:
            action_results = refresh_runner_diagnostics(diagnostics, action_results)
            mark_runner_failure(diagnostics, error_type="timeout", last_error=str(exc))
            return ProactiveRealRunOutcome(
                run_status="failed",
                error_message="proactive real runner 请求超时",
                retryable=True,
                action_results=action_results,
                runner_diagnostics=diagnostics,
            )
        except httpx.HTTPError as exc:
            action_results = refresh_runner_diagnostics(diagnostics, action_results)
            mark_runner_failure(diagnostics, error_type="http_error", last_error=str(exc))
            return ProactiveRealRunOutcome(
                run_status="failed",
                error_message="proactive real runner 请求失败",
                retryable=True,
                action_results=action_results,
                runner_diagnostics=diagnostics,
            )

    async def _create_work_order(
        self,
        client: httpx.AsyncClient,
        context: ExecutorContext,
        action: dict[str, Any],
    ) -> dict[str, Any]:
        payload = {
            "task_plan_id": context.task_plan_id,
            "customer_name": context.normalized_data.get("customer_name"),
            "work_order_type": action.get("work_order_type"),
            "product_info_id": context.normalized_data.get("product_info_id"),
            "product_link": context.normalized_data.get("product_link"),
            "contact_name": context.normalized_data.get("contact_name"),
            "contact_phone": context.normalized_data.get("contact_phone"),
        }
        try:
            response = await client.post(self.settings.proactive_real_create_endpoint, json=payload)
            if response.status_code >= 400:
                return {
                    "action": "create_proactive_work_order",
                    "status": "failed",
                    "http_status": response.status_code,
                    "error_message": f"创建 proactive 工单失败: {response.status_code}",
                    "retryable": response.status_code >= 500,
                }
            data = response.json()
            final_link = _read_path(data, self.settings.proactive_real_final_link_path)
            if not final_link:
                return {
                    "action": "create_proactive_work_order",
                    "status": "failed",
                    "http_status": response.status_code,
                    "error_type": "response_invalid",
                    "error_message": "创建 proactive 工单成功但缺少 final_link",
                    "retryable": False,
                }
            return {
                "action": "create_proactive_work_order",
                "status": "success",
                "http_status": response.status_code,
                "final_link": final_link,
            }
        except httpx.TimeoutException:
            return {
                "action": "create_proactive_work_order",
                "status": "failed",
                "error_type": "timeout",
                "error_message": "创建 proactive 工单超时",
                "retryable": True,
            }
        except ValueError:
            return {
                "action": "create_proactive_work_order",
                "status": "failed",
                "error_type": "response_invalid",
                "error_message": "创建 proactive 工单返回非法 JSON",
                "retryable": False,
            }

    async def _assign_owner(
        self,
        client: httpx.AsyncClient,
        context: ExecutorContext,
        action: dict[str, Any],
        work_order_id: str,
    ) -> dict[str, Any]:
        endpoint = self.settings.proactive_real_assign_endpoint_template.format(work_order_id=work_order_id)
        payload = {
            "task_plan_id": context.task_plan_id,
            "work_order_id": work_order_id,
            "owner": action.get("owner") or "舒磊",
        }
        try:
            response = await client.post(endpoint, json=payload)
            if response.status_code >= 400:
                return {
                    "action": "assign_owner",
                    "status": "failed",
                    "http_status": response.status_code,
                    "error_message": f"指派 proactive 负责人失败: {response.status_code}",
                    "retryable": response.status_code >= 500,
                }
            return {
                "action": "assign_owner",
                "status": "success",
                "http_status": response.status_code,
                "owner": payload["owner"],
            }
        except httpx.TimeoutException:
            return {
                "action": "assign_owner",
                "status": "failed",
                "error_type": "timeout",
                "error_message": "指派 proactive 负责人超时",
                "retryable": True,
            }

    async def _fill_feedback(
        self,
        client: httpx.AsyncClient,
        context: ExecutorContext,
        action: dict[str, Any],
        work_order_id: str,
        final_link: str | None,
    ) -> dict[str, Any]:
        endpoint = self.settings.proactive_real_feedback_endpoint_template.format(work_order_id=work_order_id)
        payload = {
            "task_plan_id": context.task_plan_id,
            "work_order_id": work_order_id,
            "final_link": final_link,
            "feedback_note": action.get("feedback_note") or context.normalized_data.get("feedback_note"),
        }
        try:
            response = await client.post(endpoint, json=payload)
            if response.status_code >= 400:
                return {
                    "action": "fill_feedback",
                    "status": "failed",
                    "http_status": response.status_code,
                    "error_message": f"写入 proactive 反馈失败: {response.status_code}",
                    "retryable": response.status_code >= 500,
                }
            return {
                "action": "fill_feedback",
                "status": "success",
                "http_status": response.status_code,
                "feedback_note": payload["feedback_note"],
            }
        except httpx.TimeoutException:
            return {
                "action": "fill_feedback",
                "status": "failed",
                "error_type": "timeout",
                "error_message": "写入 proactive 反馈超时",
                "retryable": True,
            }

    def _base_diagnostics(self) -> dict[str, Any]:
        return build_runner_diagnostics(
            module_code="proactive",
            runner="ProactiveRealRunner",
            mode="real",
            base_url=self.settings.proactive_real_base_url,
            create_endpoint=self.settings.proactive_real_create_endpoint,
            assign_endpoint_template=self.settings.proactive_real_assign_endpoint_template,
            feedback_endpoint_template=self.settings.proactive_real_feedback_endpoint_template,
            token_header=self.settings.proactive_real_token_header,
        )

    def _failure_outcome(
        self,
        *,
        diagnostics: dict[str, Any],
        action_results: list[dict[str, Any]],
        action_result: dict[str, Any],
        fallback_message: str,
    ) -> ProactiveRealRunOutcome:
        action_results = refresh_runner_diagnostics(diagnostics, action_results)
        mark_runner_failure(diagnostics, action_result=action_result)
        return ProactiveRealRunOutcome(
            run_status="failed",
            error_message=action_result.get("error_message") or fallback_message,
            retryable=bool(action_result.get("retryable", False)),
            action_results=action_results,
            runner_diagnostics=diagnostics,
        )


def _resolve_work_order_id(final_link: str | None) -> str | None:
    if not final_link:
        return None
    parsed = urlparse(str(final_link))
    tail = parsed.path.rstrip("/").split("/")[-1]
    return tail or None


def _read_path(data: dict[str, Any], path: str) -> Any:
    current: Any = data
    for key in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current
