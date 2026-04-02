from __future__ import annotations

from pathlib import Path
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
from services.report_matching.schemas import ReportMatchResult


class InspectionRealRunOutcome(BaseModel):
    run_status: str
    final_link: str | None = None
    error_message: str | None = None
    retryable: bool = False
    action_results: list[dict[str, Any]] = Field(default_factory=list)
    runner_diagnostics: dict[str, Any] = Field(default_factory=dict)


class InspectionRealRunner:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def validate(self) -> tuple[bool, dict[str, Any], str | None]:
        diagnostics = self._base_diagnostics()
        missing_fields = []
        if not self.settings.inspection_real_base_url:
            missing_fields.append("inspection_real_base_url")
        if not self.settings.inspection_real_assign_endpoint_template:
            missing_fields.append("inspection_real_assign_endpoint_template")
        if not self.settings.inspection_real_add_member_endpoint_template:
            missing_fields.append("inspection_real_add_member_endpoint_template")
        if not self.settings.inspection_real_upload_endpoint_template:
            missing_fields.append("inspection_real_upload_endpoint_template")
        if not self.settings.inspection_real_complete_endpoint_template:
            missing_fields.append("inspection_real_complete_endpoint_template")
        if not self.settings.inspection_real_token:
            missing_fields.append("inspection_real_token")
        apply_validation_result(diagnostics, missing_fields)
        if missing_fields:
            return False, diagnostics, "inspection 真实执行配置缺失"
        return True, diagnostics, None

    async def run(
        self,
        context: ExecutorContext,
        actions: list[dict[str, Any]],
        report_match: ReportMatchResult,
    ) -> InspectionRealRunOutcome:
        valid, diagnostics, error_message = self.validate()
        if not valid:
            return InspectionRealRunOutcome(
                run_status="failed",
                error_message=error_message,
                retryable=False,
                runner_diagnostics=diagnostics,
            )

        action_results: list[dict[str, Any]] = []
        work_order_id = _resolve_work_order_id(context)
        work_order_link = _resolve_work_order_link(context, self.settings, work_order_id)
        if not work_order_id or not work_order_link:
            refresh_runner_diagnostics(diagnostics, action_results)
            mark_runner_failure(
                diagnostics,
                error_type="response_invalid",
                failed_action="resolve_work_order",
                last_error="无法解析 work_order_id 或 work_order_link",
            )
            return InspectionRealRunOutcome(
                run_status="failed",
                error_message="无法解析巡检工单标识",
                retryable=False,
                runner_diagnostics=diagnostics,
            )
        headers = {self.settings.inspection_real_token_header: self.settings.inspection_real_token}
        try:
            async with httpx.AsyncClient(
                base_url=self.settings.inspection_real_base_url,
                timeout=self.settings.inspection_real_timeout_seconds,
                verify=self.settings.inspection_real_verify_ssl,
                headers=headers,
            ) as client:
                open_result = normalize_action_result(await self._open_work_order(client, work_order_link))
                action_results.append(open_result)
                if open_result["status"] != "success":
                    return self._failure_outcome(
                        diagnostics=diagnostics,
                        action_results=action_results,
                        action_result=open_result,
                        fallback_message="打开巡检工单失败",
                    )

                assign_result = normalize_action_result(await self._assign_owner(client, context, work_order_id))
                action_results.append(assign_result)
                if assign_result["status"] == "member_missing":
                    add_member_result = normalize_action_result(
                        await self._add_member_if_missing(client, context, work_order_id)
                    )
                    action_results.append(add_member_result)
                    if add_member_result["status"] == "manual_required":
                        return self._manual_required_outcome(
                            diagnostics=diagnostics,
                            action_results=action_results,
                            action_result=add_member_result,
                            fallback_message="巡检成员补充需要人工处理",
                        )
                    if add_member_result["status"] != "success":
                        return self._failure_outcome(
                            diagnostics=diagnostics,
                            action_results=action_results,
                            action_result=add_member_result,
                            fallback_message="添加巡检成员失败",
                        )
                    assign_result = normalize_action_result(
                        await self._assign_owner(client, context, work_order_id)
                    )
                    action_results.append(assign_result)

                if assign_result["status"] == "manual_required":
                    return self._manual_required_outcome(
                        diagnostics=diagnostics,
                        action_results=action_results,
                        action_result=assign_result,
                        fallback_message="巡检工单权限不足，需要人工处理",
                    )
                if assign_result["status"] != "success":
                    return self._failure_outcome(
                        diagnostics=diagnostics,
                        action_results=action_results,
                        action_result=assign_result,
                        fallback_message="指派巡检负责人失败",
                    )

                upload_result = normalize_action_result(
                    await self._upload_reports(client, context, report_match, work_order_id)
                )
                action_results.append(upload_result)
                if upload_result["status"] != "success":
                    return self._failure_outcome(
                        diagnostics=diagnostics,
                        action_results=action_results,
                        action_result=upload_result,
                        fallback_message="上传巡检报告失败",
                    )

                complete_result = normalize_action_result(
                    await self._complete_work_order(
                        client,
                        context,
                        work_order_id,
                        work_order_link,
                        upload_result.get("uploaded_files", []),
                    )
                )
                action_results.append(complete_result)
                if complete_result["status"] != "success":
                    return self._failure_outcome(
                        diagnostics=diagnostics,
                        action_results=action_results,
                        action_result=complete_result,
                        fallback_message="完成巡检工单处理失败",
                    )

                final_link = complete_result.get("final_link") or work_order_link
                action_results = refresh_runner_diagnostics(diagnostics, action_results)
                mark_runner_success(diagnostics)
                return InspectionRealRunOutcome(
                    run_status="success",
                    final_link=final_link,
                    retryable=False,
                    action_results=action_results,
                    runner_diagnostics=diagnostics,
                )
        except httpx.TimeoutException as exc:
            action_results = refresh_runner_diagnostics(diagnostics, action_results)
            mark_runner_failure(diagnostics, error_type="timeout", last_error=str(exc))
            return InspectionRealRunOutcome(
                run_status="failed",
                error_message="inspection real runner 请求超时",
                retryable=True,
                action_results=action_results,
                runner_diagnostics=diagnostics,
            )
        except httpx.HTTPError as exc:
            action_results = refresh_runner_diagnostics(diagnostics, action_results)
            mark_runner_failure(diagnostics, error_type="http_error", last_error=str(exc))
            return InspectionRealRunOutcome(
                run_status="failed",
                error_message="inspection real runner 请求失败",
                retryable=True,
                action_results=action_results,
                runner_diagnostics=diagnostics,
            )

    async def _open_work_order(self, client: httpx.AsyncClient, work_order_link: str) -> dict[str, Any]:
        try:
            response = await client.get(work_order_link)
            if response.status_code >= 400:
                return {
                    "action": "open_inspection_work_order",
                    "status": "failed",
                    "target": work_order_link,
                    "http_status": response.status_code,
                    "error_message": f"打开巡检工单失败: {response.status_code}",
                    "retryable": response.status_code >= 500,
                }
            return {
                "action": "open_inspection_work_order",
                "status": "success",
                "target": work_order_link,
                "http_status": response.status_code,
            }
        except httpx.TimeoutException:
            return {
                "action": "open_inspection_work_order",
                "status": "failed",
                "target": work_order_link,
                "error_type": "timeout",
                "error_message": "打开巡检工单超时",
                "retryable": True,
            }

    async def _upload_reports(
        self,
        client: httpx.AsyncClient,
        context: ExecutorContext,
        report_match: ReportMatchResult,
        work_order_id: str,
    ) -> dict[str, Any]:
        endpoint = self.settings.inspection_real_upload_endpoint_template.format(work_order_id=work_order_id)
        files_payload: list[tuple[str, tuple[str, bytes, str]]] = []
        uploaded_files: list[str] = []
        try:
            for file_type in ("word", "pdf"):
                for file_path in report_match.matched_files.get(file_type, []):
                    path = Path(file_path)
                    files_payload.append(
                        (
                            "files",
                            (
                                path.name,
                                path.read_bytes(),
                                "application/octet-stream",
                            ),
                        )
                    )
                    uploaded_files.append(str(path))
        except OSError as exc:
            return {
                "action": "upload_report_files",
                "status": "failed",
                "error_type": "unknown_error",
                "error_message": f"读取巡检报告失败: {exc}",
                "retryable": False,
            }

        data = {
            "task_plan_id": context.task_plan_id,
            "work_order_id": work_order_id,
            "customer_name": context.normalized_data.get("customer_name"),
        }
        try:
            response = await client.post(endpoint, data=data, files=files_payload)
            if response.status_code >= 400:
                return {
                    "action": "upload_report_files",
                    "status": "failed",
                    "http_status": response.status_code,
                    "uploaded_files": uploaded_files,
                    "error_message": f"上传巡检报告失败: {response.status_code}",
                    "retryable": response.status_code >= 500,
                }
            return {
                "action": "upload_report_files",
                "status": "success",
                "http_status": response.status_code,
                "uploaded_files": uploaded_files,
            }
        except httpx.TimeoutException:
            return {
                "action": "upload_report_files",
                "status": "failed",
                "uploaded_files": uploaded_files,
                "error_type": "timeout",
                "error_message": "上传巡检报告超时",
                "retryable": True,
            }

    async def _assign_owner(
        self,
        client: httpx.AsyncClient,
        context: ExecutorContext,
        work_order_id: str,
    ) -> dict[str, Any]:
        endpoint = self.settings.inspection_real_assign_endpoint_template.format(work_order_id=work_order_id)
        owner = "舒磊"
        payload = {
            "task_plan_id": context.task_plan_id,
            "work_order_id": work_order_id,
            "owner": owner,
        }
        try:
            response = await client.post(endpoint, json=payload)
            if response.status_code == 403:
                return {
                    "action": "assign_owner",
                    "status": "manual_required",
                    "http_status": response.status_code,
                    "error_message": "巡检工单权限不足，需要人工处理",
                    "retryable": False,
                }
            if response.status_code == 409:
                error_code = None
                try:
                    error_code = response.json().get("error_code")
                except ValueError:
                    error_code = None
                if error_code == "member_missing":
                    return {
                        "action": "assign_owner",
                        "status": "member_missing",
                        "http_status": response.status_code,
                        "owner": owner,
                        "error_message": "负责人不在成员列表，尝试补充成员",
                        "retryable": False,
                    }
            if response.status_code >= 400:
                return {
                    "action": "assign_owner",
                    "status": "failed",
                    "http_status": response.status_code,
                    "error_message": f"指派巡检负责人失败: {response.status_code}",
                    "retryable": response.status_code >= 500,
                }
            return {
                "action": "assign_owner",
                "status": "success",
                "http_status": response.status_code,
                "owner": owner,
            }
        except httpx.TimeoutException:
            return {
                "action": "assign_owner",
                "status": "failed",
                "error_type": "timeout",
                "error_message": "指派巡检负责人超时",
                "retryable": True,
            }

    async def _add_member_if_missing(
        self,
        client: httpx.AsyncClient,
        context: ExecutorContext,
        work_order_id: str,
    ) -> dict[str, Any]:
        endpoint = self.settings.inspection_real_add_member_endpoint_template.format(work_order_id=work_order_id)
        member_name = "舒磊"
        payload = {
            "task_plan_id": context.task_plan_id,
            "work_order_id": work_order_id,
            "member_name": member_name,
        }
        try:
            response = await client.post(endpoint, json=payload)
            if response.status_code == 403:
                return {
                    "action": "add_member_if_missing",
                    "status": "manual_required",
                    "http_status": response.status_code,
                    "error_message": "巡检工单无权限添加成员，需要人工处理",
                    "retryable": False,
                }
            if response.status_code >= 400:
                return {
                    "action": "add_member_if_missing",
                    "status": "failed",
                    "http_status": response.status_code,
                    "error_message": f"添加巡检成员失败: {response.status_code}",
                    "retryable": response.status_code >= 500,
                }
            return {
                "action": "add_member_if_missing",
                "status": "success",
                "http_status": response.status_code,
                "member_name": member_name,
            }
        except httpx.TimeoutException:
            return {
                "action": "add_member_if_missing",
                "status": "failed",
                "error_type": "timeout",
                "error_message": "添加巡检成员超时",
                "retryable": True,
            }

    async def _complete_work_order(
        self,
        client: httpx.AsyncClient,
        context: ExecutorContext,
        work_order_id: str,
        work_order_link: str,
        uploaded_files: list[str],
    ) -> dict[str, Any]:
        endpoint = self.settings.inspection_real_complete_endpoint_template.format(work_order_id=work_order_id)
        payload = {
            "task_plan_id": context.task_plan_id,
            "work_order_id": work_order_id,
            "work_order_link": work_order_link,
            "uploaded_files": uploaded_files,
        }
        try:
            response = await client.post(endpoint, json=payload)
            if response.status_code >= 400:
                return {
                    "action": "complete_inspection",
                    "status": "failed",
                    "http_status": response.status_code,
                    "error_message": f"完成巡检工单处理失败: {response.status_code}",
                    "retryable": response.status_code >= 500,
                }
            final_link = work_order_link
            try:
                data = response.json()
                final_link = _read_path(data, self.settings.inspection_real_final_link_path) or work_order_link
            except ValueError:
                pass
            return {
                "action": "complete_inspection",
                "status": "success",
                "http_status": response.status_code,
                "final_link": final_link,
            }
        except httpx.TimeoutException:
            return {
                "action": "complete_inspection",
                "status": "failed",
                "error_type": "timeout",
                "error_message": "完成巡检工单处理超时",
                "retryable": True,
            }

    def _base_diagnostics(self) -> dict[str, Any]:
        return build_runner_diagnostics(
            module_code="inspection",
            runner="InspectionRealRunner",
            mode="real",
            base_url=self.settings.inspection_real_base_url,
            assign_endpoint_template=self.settings.inspection_real_assign_endpoint_template,
            add_member_endpoint_template=self.settings.inspection_real_add_member_endpoint_template,
            upload_endpoint_template=self.settings.inspection_real_upload_endpoint_template,
            complete_endpoint_template=self.settings.inspection_real_complete_endpoint_template,
            token_header=self.settings.inspection_real_token_header,
        )

    def _failure_outcome(
        self,
        *,
        diagnostics: dict[str, Any],
        action_results: list[dict[str, Any]],
        action_result: dict[str, Any],
        fallback_message: str,
    ) -> InspectionRealRunOutcome:
        action_results = refresh_runner_diagnostics(diagnostics, action_results)
        mark_runner_failure(diagnostics, action_result=action_result)
        return InspectionRealRunOutcome(
            run_status="failed",
            error_message=action_result.get("error_message") or fallback_message,
            retryable=bool(action_result.get("retryable", False)),
            action_results=action_results,
            runner_diagnostics=diagnostics,
        )

    def _manual_required_outcome(
        self,
        *,
        diagnostics: dict[str, Any],
        action_results: list[dict[str, Any]],
        action_result: dict[str, Any],
        fallback_message: str,
    ) -> InspectionRealRunOutcome:
        action_results = refresh_runner_diagnostics(diagnostics, action_results)
        mark_runner_failure(diagnostics, action_result=action_result)
        diagnostics["manual_required"] = True
        return InspectionRealRunOutcome(
            run_status="manual_required",
            error_message=action_result.get("error_message") or fallback_message,
            retryable=False,
            action_results=action_results,
            runner_diagnostics=diagnostics,
        )


def _resolve_work_order_id(context: ExecutorContext) -> str | None:
    work_order_id = context.normalized_data.get("work_order_id")
    if work_order_id:
        return str(work_order_id)
    work_order_link = context.normalized_data.get("work_order_link")
    if not work_order_link:
        return None
    parsed = urlparse(str(work_order_link))
    tail = parsed.path.rstrip("/").split("/")[-1]
    return tail or None


def _resolve_work_order_link(context: ExecutorContext, settings: Settings, work_order_id: str | None) -> str | None:
    work_order_link = context.normalized_data.get("work_order_link")
    if work_order_link:
        return str(work_order_link)
    if not settings.inspection_real_base_url or not work_order_id:
        return None
    return f"{settings.inspection_real_base_url.rstrip('/')}/inspection-work-orders/{work_order_id}"


def _read_path(data: dict[str, Any], path: str) -> Any:
    current: Any = data
    for key in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current
