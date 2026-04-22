from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
from pathlib import Path
import shutil
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
from services.executors.visit_real_runner import _PtsBrowserSession, _PtsRunnerError
from services.recognizers.visit_delivery_backfill import _find_local_chrome_user_data_dir
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
        missing_fields: list[str] = []
        if self._prefer_pts_browser_mode():
            if not self.settings.pts_base_url:
                missing_fields.append("pts_base_url")
            if not self._browser_session_available():
                missing_fields.append("pts_browser_session")
            if not self.settings.pts_cookie_header:
                missing_fields.append("pts_cookie_header")
        elif self._use_legacy_api_mode():
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
        else:
            missing_fields.extend(
                [
                    "pts_base_url",
                    "pts_browser_session",
                    "pts_cookie_header",
                    "inspection_real_base_url",
                    "inspection_real_token",
                ]
            )
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
        if self._prefer_pts_browser_mode():
            return await self._run_pts_browser_mode(context, report_match, diagnostics)
        if self._use_legacy_api_mode():
            return await self._run_legacy_api_mode(context, report_match, diagnostics)
        return InspectionRealRunOutcome(
            run_status="failed",
            error_message="inspection 真实执行配置缺失",
            retryable=False,
            runner_diagnostics=diagnostics,
        )

    def _use_legacy_api_mode(self) -> bool:
        return bool(self.settings.inspection_real_base_url and self.settings.inspection_real_token)

    def _prefer_pts_browser_mode(self) -> bool:
        return bool(
            self.settings.pts_base_url
            and self.settings.pts_cookie_header
            and self._browser_session_available()
        )

    def _browser_session_available(self) -> bool:
        return _find_local_chrome_user_data_dir() is not None

    async def _run_legacy_api_mode(
        self,
        context: ExecutorContext,
        report_match: ReportMatchResult,
        diagnostics: dict[str, Any],
    ) -> InspectionRealRunOutcome:
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
                open_result = normalize_action_result(await self._legacy_open_work_order(client, work_order_link))
                action_results.append(open_result)
                if open_result["status"] != "success":
                    return self._failure_outcome(
                        diagnostics=diagnostics,
                        action_results=action_results,
                        action_result=open_result,
                        fallback_message="打开巡检工单失败",
                    )

                assign_result = normalize_action_result(
                    await self._legacy_assign_owner(client, context, work_order_id)
                )
                action_results.append(assign_result)
                if assign_result["status"] == "member_missing":
                    add_member_result = normalize_action_result(
                        await self._legacy_add_member_if_missing(client, context, work_order_id)
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
                        await self._legacy_assign_owner(client, context, work_order_id)
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
                    await self._legacy_upload_reports(client, context, report_match, work_order_id)
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
                    await self._legacy_complete_work_order(
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

                archive_result = normalize_action_result(
                    self._archive_uploaded_reports(upload_result.get("uploaded_files", []))
                )
                action_results.append(archive_result)
                if archive_result["status"] != "success":
                    return self._failure_outcome(
                        diagnostics=diagnostics,
                        action_results=action_results,
                        action_result=archive_result,
                        fallback_message="归档巡检报告失败",
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

    async def _run_pts_browser_mode(
        self,
        context: ExecutorContext,
        report_match: ReportMatchResult,
        diagnostics: dict[str, Any],
    ) -> InspectionRealRunOutcome:
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

        diagnostics["transport_mode"] = "pts_browser_session"
        try:
            async with _PtsBrowserSession(self.settings) as browser:
                open_result = normalize_action_result(await browser.open_project(work_order_link))
                action_results.append(open_result)
                if open_result["status"] != "success":
                    return self._failure_outcome(
                        diagnostics=diagnostics,
                        action_results=action_results,
                        action_result=open_result,
                        fallback_message="打开巡检工单失败",
                    )

                runtime = await self._load_pts_runtime(browser, work_order_id, work_order_link)
                if str(runtime.get("me_name") or "").strip() != "舒磊":
                    account_result = normalize_action_result(
                        {
                            "action": "validate_pts_account",
                            "status": "manual_required",
                            "error_type": "manual_required_owner",
                            "error_message": "当前 PTS 登录账号不是舒磊，无法自动指定工单负责人",
                            "retryable": False,
                        }
                    )
                    action_results.append(account_result)
                    return self._manual_required_outcome(
                        diagnostics=diagnostics,
                        action_results=action_results,
                        action_result=account_result,
                        fallback_message="当前 PTS 登录账号不是舒磊，无法自动执行巡检闭环",
                    )
                diagnostics.setdefault("postcheck", self._empty_postcheck_payload())
                if runtime["is_finished"] or runtime["current_stage_name"] in {"审核工单", "完成"}:
                    already_closed_result = normalize_action_result(
                        {
                            "action": "validate_execution_preconditions",
                            "status": "manual_required",
                            "error_type": "already_closed_before_execution",
                            "error_message": "工单当前已处于审核/完成阶段，本次不执行上传闭环",
                            "retryable": False,
                            "stage_after": runtime["current_stage_name"],
                            "is_finished": bool(runtime["is_finished"]),
                        }
                    )
                    action_results.append(already_closed_result)
                    return self._manual_required_outcome(
                        diagnostics=diagnostics,
                        action_results=action_results,
                        action_result=already_closed_result,
                        fallback_message="工单已闭环，当前执行不再触发上传链",
                    )

                add_member_result = normalize_action_result(
                    await self._pts_add_member_if_missing(browser, runtime)
                )
                action_results.append(add_member_result)
                if add_member_result["status"] == "manual_required":
                    return self._manual_required_outcome(
                        diagnostics=diagnostics,
                        action_results=action_results,
                        action_result=add_member_result,
                        fallback_message="无权限添加项目成员，需要人工处理",
                    )
                if add_member_result["status"] != "success":
                    return self._failure_outcome(
                        diagnostics=diagnostics,
                        action_results=action_results,
                        action_result=add_member_result,
                        fallback_message="补充项目成员失败",
                    )

                assign_result = normalize_action_result(
                    await self._pts_assign_owner(browser, runtime)
                )
                action_results.append(assign_result)
                if assign_result["status"] == "manual_required":
                    return self._manual_required_outcome(
                        diagnostics=diagnostics,
                        action_results=action_results,
                        action_result=assign_result,
                        fallback_message="无权限指定工单负责人，需要人工处理",
                    )
                if assign_result["status"] != "success":
                    return self._failure_outcome(
                        diagnostics=diagnostics,
                        action_results=action_results,
                        action_result=assign_result,
                        fallback_message="指定工单负责人失败",
                    )

                upload_result = normalize_action_result(
                    await self._pts_upload_reports(
                        browser,
                        report_match,
                        work_order_link=work_order_link,
                        runtime=runtime,
                    )
                )
                action_results.append(upload_result)
                if upload_result["status"] != "success":
                    return self._failure_outcome(
                        diagnostics=diagnostics,
                        action_results=action_results,
                        action_result=upload_result,
                        fallback_message="上传巡检报告失败",
                    )

                if self._should_add_work_order_info_after_upload():
                    add_info_result = normalize_action_result(
                        await self._pts_add_work_order_info(
                            browser,
                            work_order_id=work_order_id,
                            work_order_link=work_order_link,
                            customer_name=str(context.normalized_data.get("customer_name") or ""),
                            uploaded_file_ids=upload_result.get("uploaded_file_ids", []),
                            uploaded_remote_files=upload_result.get("uploaded_remote_files", []),
                        )
                    )
                    action_results.append(add_info_result)
                    if add_info_result["status"] != "success":
                        return self._failure_outcome(
                            diagnostics=diagnostics,
                            action_results=action_results,
                            action_result=add_info_result,
                            fallback_message="写入工单处理记录失败",
                        )
                else:
                    action_results.append(
                        normalize_action_result(
                            {
                                "action": "add_work_order_info",
                                "status": "skipped",
                                "error_type": None,
                                "error_message": "已启用 PTS 前端上传模式，跳过自动备注写入",
                                "retryable": False,
                            }
                        )
                    )

                attachment_precheck_result = normalize_action_result(
                    await self._pts_precheck_uploaded_attachments(
                        browser,
                        runtime=runtime,
                        uploaded_file_ids=upload_result.get("uploaded_file_ids", []),
                        uploaded_files=upload_result.get("uploaded_files", []),
                        uploaded_remote_files=upload_result.get("uploaded_remote_files", []),
                    )
                )
                action_results.append(attachment_precheck_result)
                if attachment_precheck_result["status"] != "success":
                    return self._failure_outcome(
                        diagnostics=diagnostics,
                        action_results=action_results,
                        action_result=attachment_precheck_result,
                        fallback_message="巡检报告未成功挂载到工单附件，禁止闭环",
                    )

                complete_result = normalize_action_result(
                    await self._pts_complete_work_order(browser, runtime)
                )
                action_results.append(complete_result)
                if complete_result["status"] != "success":
                    return self._failure_outcome(
                        diagnostics=diagnostics,
                        action_results=action_results,
                        action_result=complete_result,
                        fallback_message="完成巡检工单处理失败",
                    )

                postcheck_result = normalize_action_result(
                    await self._pts_postcheck_work_order(
                        browser,
                        runtime=runtime,
                        uploaded_file_ids=upload_result.get("uploaded_file_ids", []),
                        uploaded_files=upload_result.get("uploaded_files", []),
                        uploaded_remote_files=upload_result.get("uploaded_remote_files", []),
                    )
                )
                action_results.append(postcheck_result)
                diagnostics["postcheck"] = self._build_postcheck_diagnostics(postcheck_result)
                if postcheck_result["status"] != "success":
                    return self._failure_outcome(
                        diagnostics=diagnostics,
                        action_results=action_results,
                        action_result=postcheck_result,
                        fallback_message="巡检工单闭环后校验未通过",
                    )

                archive_result = normalize_action_result(
                    self._archive_uploaded_reports(upload_result.get("uploaded_files", []))
                )
                action_results.append(archive_result)
                if archive_result["status"] != "success":
                    return self._failure_outcome(
                        diagnostics=diagnostics,
                        action_results=action_results,
                        action_result=archive_result,
                        fallback_message="归档巡检报告失败",
                    )

                action_results = refresh_runner_diagnostics(diagnostics, action_results)
                mark_runner_success(diagnostics)
                return InspectionRealRunOutcome(
                    run_status="success",
                    final_link=work_order_link,
                    retryable=False,
                    action_results=action_results,
                    runner_diagnostics=diagnostics,
                )
        except _PtsRunnerError as exc:
            action_results = refresh_runner_diagnostics(diagnostics, action_results)
            mark_runner_failure(
                diagnostics,
                error_type=exc.error_type,
                last_error=exc.error_message,
            )
            return InspectionRealRunOutcome(
                run_status="failed",
                error_message=exc.error_message,
                retryable=exc.retryable,
                action_results=action_results,
                runner_diagnostics=diagnostics,
            )

    @staticmethod
    def _empty_postcheck_payload() -> dict[str, Any]:
        return {
            "postcheck_passed": False,
            "closure_confirmed": False,
            "report_attached_confirmed": False,
            "postcheck_stage_after": None,
            "postcheck_uploaded_file_ids_expected": [],
            "postcheck_uploaded_file_ids_found": [],
            "postcheck_uploaded_filenames_expected": [],
            "postcheck_uploaded_filenames_found": [],
            "postcheck_attachment_match_mode": "none",
            "postcheck_source": "pts_browser_session",
        }

    def _build_postcheck_diagnostics(self, postcheck_result: dict[str, Any]) -> dict[str, Any]:
        payload = self._empty_postcheck_payload()
        payload.update(
            {
                "postcheck_passed": postcheck_result.get("postcheck_passed", False),
                "closure_confirmed": postcheck_result.get("closure_confirmed", False),
                "report_attached_confirmed": postcheck_result.get("report_attached_confirmed", False),
                "postcheck_stage_after": postcheck_result.get("stage_after"),
                "postcheck_uploaded_file_ids_expected": postcheck_result.get("uploaded_file_ids_expected", []),
                "postcheck_uploaded_file_ids_found": postcheck_result.get("uploaded_file_ids_found", []),
                "postcheck_uploaded_filenames_expected": postcheck_result.get("uploaded_filenames_expected", []),
                "postcheck_uploaded_filenames_found": postcheck_result.get("uploaded_filenames_found", []),
                "postcheck_attachment_match_mode": postcheck_result.get("attachment_match_mode", "none"),
                "postcheck_source": postcheck_result.get("postcheck_source", "pts_browser_session"),
            }
        )
        return payload

    async def _load_pts_runtime(
        self,
        browser: _PtsBrowserSession,
        work_order_id: str,
        work_order_link: str,
    ) -> dict[str, Any]:
        me = await self._query_runtime_graphql(
            browser,
            work_order_link=work_order_link,
            payload={
                "operationName": "Me",
                "query": "query Me { me { id name } }",
            },
        )
        work_order = await self._query_runtime_graphql(
            browser,
            work_order_link=work_order_link,
            payload={
                "operationName": "WorkOrderByID",
                "variables": {"id": work_order_id},
                "query": (
                    "query WorkOrderByID($id: ID!) { "
                    "workOrderByID(id: $id) { "
                    "id "
                    "is_finished "
                    "current_stage { name sequence } "
                    "claim_by { id name } "
                    "customer_affect_owner { id name } "
                    "technical_owner { id name } "
                    "product_delivery_support { id user_list { id name } } "
                    "delivery { id user_list { id name } } "
                    "info { id note stage file { id filename size } } "
                    "} "
                    "}"
                ),
            },
        )
        work_order_data = (work_order or {}).get("workOrderByID") or {}
        member_container = work_order_data.get("product_delivery_support") or work_order_data.get("delivery") or {}
        info_list = work_order_data.get("info") or []
        if isinstance(info_list, dict):
            info_list = [info_list]
        all_attached_files: list[dict[str, Any]] = []
        for info in info_list:
            for file_item in info.get("file") or []:
                all_attached_files.append(
                    {
                        "id": str(file_item.get("id") or "").strip(),
                        "filename": str(file_item.get("filename") or "").strip(),
                        "size": file_item.get("size"),
                    }
                )
        me_data = (me or {}).get("me") or {}
        return {
            "me_id": str(me_data.get("id") or "").strip(),
            "me_name": str(me_data.get("name") or "").strip(),
            "work_order_id": work_order_id,
            "work_order_link": work_order_link,
            "is_finished": bool(work_order_data.get("is_finished")),
            "current_stage_name": str(((work_order_data.get("current_stage") or {}).get("name") or "")).strip(),
            "claim_by_id": str(((work_order_data.get("claim_by") or {}).get("id") or "")).strip(),
            "delivery_support_id": str(member_container.get("id") or "").strip(),
            "member_ids": [
                str(item.get("id") or "").strip()
                for item in member_container.get("user_list") or []
                if str(item.get("id") or "").strip()
            ],
            "info_list": info_list,
            "all_attached_files": all_attached_files,
        }

    async def _query_runtime_graphql(
        self,
        browser: _PtsBrowserSession,
        *,
        work_order_link: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        operation_name = str(payload.get("operationName") or "AnonymousOperation")
        endpoint = "/query"
        max_attempts = 3
        last_error: _PtsRunnerError | None = None

        for attempt in range(1, max_attempts + 1):
            try:
                raw_result = await browser.execute_js_on_project(
                    work_order_link,
                    self._build_runtime_graphql_probe_script(payload, endpoint=endpoint),
                )
                parsed_result = (
                    raw_result if isinstance(raw_result, dict) else {"status": 0, "raw": str(raw_result or "")}
                )
                status = self._safe_int(parsed_result.get("status"))
                response_url = str(parsed_result.get("responseURL") or "")
                page_url = str(parsed_result.get("url") or "")
                content_type = str(parsed_result.get("contentType") or "")
                response_text = str(parsed_result.get("text") or "")
                client_error = str(parsed_result.get("error") or "")

                if (
                    status in {401, 403}
                    or "auth.chaitin.net/login" in response_url
                    or "auth.chaitin.net/login" in page_url
                ):
                    raise _PtsRunnerError(
                        error_message=self._build_runtime_error_message(
                            reason="PTS 会话已失效，请重新登录 PTS 或更新 Cookie",
                            operation_name=operation_name,
                            endpoint=endpoint,
                            attempt=attempt,
                            max_attempts=max_attempts,
                            status=status,
                            content_type=content_type,
                            response_url=response_url,
                            page_url=page_url,
                            response_text=response_text,
                            raw_error=client_error,
                        ),
                        error_type="session_expired",
                        retryable=False,
                        http_status=status or None,
                    )

                if status >= 400:
                    retryable = status >= 500
                    raise _PtsRunnerError(
                        error_message=self._build_runtime_error_message(
                            reason=f"PTS GraphQL 请求失败: {status}",
                            operation_name=operation_name,
                            endpoint=endpoint,
                            attempt=attempt,
                            max_attempts=max_attempts,
                            status=status,
                            content_type=content_type,
                            response_url=response_url,
                            page_url=page_url,
                            response_text=response_text,
                            raw_error=client_error,
                        ),
                        error_type="http_error" if status >= 500 else "business_rejected",
                        retryable=retryable,
                        http_status=status,
                    )

                if not response_text.strip():
                    raise _PtsRunnerError(
                        error_message=self._build_runtime_error_message(
                            reason="PTS GraphQL 返回空响应",
                            operation_name=operation_name,
                            endpoint=endpoint,
                            attempt=attempt,
                            max_attempts=max_attempts,
                            status=status,
                            content_type=content_type,
                            response_url=response_url,
                            page_url=page_url,
                            response_text=response_text,
                            raw_error=client_error,
                        ),
                        error_type="empty_response",
                        retryable=True,
                        http_status=status or None,
                    )

                response_text_lower = response_text.strip().lower()
                content_type_lower = content_type.lower()
                if (
                    "text/html" in content_type_lower
                    or response_text_lower.startswith("<!doctype html")
                    or response_text_lower.startswith("<html")
                ):
                    err_type = (
                        "session_expired"
                        if "auth.chaitin.net/login" in response_text_lower
                        else "upstream_html_response"
                    )
                    raise _PtsRunnerError(
                        error_message=self._build_runtime_error_message(
                            reason=(
                                "PTS 会话已失效，请重新登录 PTS 或更新 Cookie"
                                if err_type == "session_expired"
                                else "PTS GraphQL 返回 HTML 响应"
                            ),
                            operation_name=operation_name,
                            endpoint=endpoint,
                            attempt=attempt,
                            max_attempts=max_attempts,
                            status=status,
                            content_type=content_type,
                            response_url=response_url,
                            page_url=page_url,
                            response_text=response_text,
                            raw_error=client_error,
                        ),
                        error_type=err_type,
                        retryable=err_type != "session_expired",
                        http_status=status or None,
                    )

                try:
                    response_payload = json.loads(response_text)
                except ValueError as exc:
                    raise _PtsRunnerError(
                        error_message=self._build_runtime_error_message(
                            reason="PTS GraphQL 返回非法 JSON",
                            operation_name=operation_name,
                            endpoint=endpoint,
                            attempt=attempt,
                            max_attempts=max_attempts,
                            status=status,
                            content_type=content_type,
                            response_url=response_url,
                            page_url=page_url,
                            response_text=response_text,
                            raw_error=client_error,
                        ),
                        error_type="response_invalid",
                        retryable=True,
                        http_status=status or None,
                    ) from exc

                errors = response_payload.get("errors") or []
                if errors:
                    message = str(errors[0].get("message") or "PTS GraphQL 返回错误").strip()
                    raise _PtsRunnerError(
                        error_message=self._build_runtime_error_message(
                            reason=message,
                            operation_name=operation_name,
                            endpoint=endpoint,
                            attempt=attempt,
                            max_attempts=max_attempts,
                            status=status,
                            content_type=content_type,
                            response_url=response_url,
                            page_url=page_url,
                            response_text=response_text,
                            raw_error=client_error,
                        ),
                        error_type="business_rejected",
                        retryable=False,
                        http_status=status or None,
                    )

                data = response_payload.get("data")
                if not isinstance(data, dict):
                    raise _PtsRunnerError(
                        error_message=self._build_runtime_error_message(
                            reason="PTS GraphQL 缺少 data 字段",
                            operation_name=operation_name,
                            endpoint=endpoint,
                            attempt=attempt,
                            max_attempts=max_attempts,
                            status=status,
                            content_type=content_type,
                            response_url=response_url,
                            page_url=page_url,
                            response_text=response_text,
                            raw_error=client_error,
                        ),
                        error_type="response_invalid",
                        retryable=True,
                        http_status=status or None,
                    )
                return data
            except _PtsRunnerError as exc:
                last_error = exc
                if exc.error_type in {"empty_response", "upstream_html_response", "response_invalid"} and attempt < max_attempts:
                    await asyncio.sleep(0.25 * attempt)
                    continue
                raise

        if last_error is not None:
            raise last_error
        raise _PtsRunnerError(
            error_message=f"PTS runtime 查询失败 action={operation_name} endpoint={endpoint}",
            error_type="response_invalid",
            retryable=False,
        )

    @staticmethod
    def _build_runtime_graphql_probe_script(payload: dict[str, Any], *, endpoint: str) -> str:
        encoded_payload = json.dumps(payload, ensure_ascii=False)
        return (
            "var xhr=new XMLHttpRequest();"
            f"xhr.open('POST',{json.dumps(endpoint)},false);"
            "xhr.withCredentials=true;"
            "xhr.setRequestHeader('Content-Type','application/json');"
            "xhr.setRequestHeader('Accept','*/*');"
            f"try{{xhr.send({json.dumps(encoded_payload, ensure_ascii=False)});"
            "JSON.stringify({"
            "status:xhr.status,"
            "responseURL:(xhr.responseURL||''),"
            "contentType:(xhr.getResponseHeader('content-type')||''),"
            "text:(xhr.responseText||''),"
            "url:(window.location.href||'')"
            "});}"
            "catch(e){JSON.stringify({"
            "status:0,"
            "error:String(e),"
            "responseURL:'',"
            "contentType:'',"
            "text:'',"
            "url:(window.location.href||'')"
            "});}"
        )

    def _build_runtime_error_message(
        self,
        *,
        reason: str,
        operation_name: str,
        endpoint: str,
        attempt: int,
        max_attempts: int,
        status: int,
        content_type: str,
        response_url: str,
        page_url: str,
        response_text: str,
        raw_error: str,
    ) -> str:
        return (
            f"{reason}; action={operation_name}; endpoint={endpoint}; "
            f"attempt={attempt}/{max_attempts}; status={status}; "
            f"content_type={self._truncate_runtime_text(content_type, 80)}; "
            f"response_url={self._truncate_runtime_text(response_url, 180)}; "
            f"page_url={self._truncate_runtime_text(page_url, 180)}; "
            f"response_preview={self._truncate_runtime_text(response_text, 220)}; "
            f"raw_error={self._truncate_runtime_text(raw_error, 120)}"
        )

    @staticmethod
    def _truncate_runtime_text(value: Any, limit: int) -> str:
        text = str(value or "").replace("\n", "\\n").replace("\r", "")
        if len(text) <= limit:
            return text
        return text[:limit] + "..."

    @staticmethod
    def _safe_int(value: Any) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    async def _pts_add_member_if_missing(
        self,
        browser: _PtsBrowserSession,
        runtime: dict[str, Any],
    ) -> dict[str, Any]:
        member_name = runtime.get("me_name") or "舒磊"
        member_id = str(runtime.get("me_id") or "").strip()
        existing_ids = [str(item).strip() for item in runtime.get("member_ids") or [] if str(item).strip()]
        if member_id and member_id in existing_ids:
            return {
                "action": "add_member_if_missing",
                "status": "success",
                "member_name": member_name,
                "member_added": False,
            }
        support_id = str(runtime.get("delivery_support_id") or "").strip()
        if not support_id or not member_id:
            return {
                "action": "add_member_if_missing",
                "status": "manual_required",
                "member_name": member_name,
                "error_message": "无法解析项目成员列表，需人工添加舒磊到项目成员",
                "error_type": "manual_required_owner",
                "retryable": False,
            }
        try:
            await browser.graphql_payload(
                {
                    "operationName": "UpdateProductDeliverySupportUserList",
                    "variables": {
                        "product_delivery_support_id": support_id,
                        "user_id_list": list(dict.fromkeys(existing_ids + [member_id])),
                    },
                    "query": (
                        "mutation UpdateProductDeliverySupportUserList($product_delivery_support_id: ID!, $user_id_list: [String!]) { "
                        "update_product_delivery_support_user_list(product_delivery_support_id: $product_delivery_support_id, user_id_list: $user_id_list) "
                        "}"
                    ),
                }
            )
            runtime["member_ids"] = list(dict.fromkeys(existing_ids + [member_id]))
            return {
                "action": "add_member_if_missing",
                "status": "success",
                "member_name": member_name,
                "member_added": True,
            }
        except _PtsRunnerError as exc:
            if _looks_like_permission_error(exc.error_message):
                return {
                    "action": "add_member_if_missing",
                    "status": "manual_required",
                    "member_name": member_name,
                    "error_message": "当前 PTS 账号无权添加项目成员，请人工处理",
                    "error_type": "manual_required_owner",
                    "source_error_type": "permission_denied",
                    "retryable": False,
                }
            return {
                "action": "add_member_if_missing",
                "status": "failed",
                "member_name": member_name,
                "error_message": exc.error_message,
                "error_type": exc.error_type,
                "http_status": exc.http_status,
                "retryable": exc.retryable,
            }

    async def _pts_assign_owner(
        self,
        browser: _PtsBrowserSession,
        runtime: dict[str, Any],
    ) -> dict[str, Any]:
        owner_name = runtime.get("me_name") or "舒磊"
        owner_id = str(runtime.get("me_id") or "").strip()
        if owner_id and owner_id == str(runtime.get("claim_by_id") or "").strip():
            return {
                "action": "assign_owner",
                "status": "success",
                "owner": owner_name,
                "already_assigned": True,
            }
        try:
            await browser.graphql_payload(
                {
                    "operationName": "UpdateWorkOrderClaimBy",
                    "variables": {
                        "id": runtime["work_order_id"],
                        "claim_by": owner_id,
                        "customer_affect_owner": [owner_id],
                        "technical_owner": [owner_id],
                    },
                    "query": (
                        "mutation UpdateWorkOrderClaimBy($id: ID!, $claim_by: ID!, $customer_affect_owner: [ID!], $technical_owner: [ID!]) { "
                        "update_work_order_claim_by(id: $id, claim_by: $claim_by, customer_affect_owner: $customer_affect_owner, technical_owner: $technical_owner) "
                        "}"
                    ),
                }
            )
            runtime["claim_by_id"] = owner_id
            return {
                "action": "assign_owner",
                "status": "success",
                "owner": owner_name,
            }
        except _PtsRunnerError as exc:
            if _looks_like_permission_error(exc.error_message):
                return {
                    "action": "assign_owner",
                    "status": "manual_required",
                    "owner": owner_name,
                    "error_message": "当前 PTS 账号无权指定工单负责人，请人工处理",
                    "error_type": "manual_required_owner",
                    "source_error_type": "permission_denied",
                    "retryable": False,
                }
            return {
                "action": "assign_owner",
                "status": "failed",
                "owner": owner_name,
                "error_message": exc.error_message,
                "error_type": exc.error_type,
                "http_status": exc.http_status,
                "retryable": exc.retryable,
            }

    async def _pts_upload_reports(
        self,
        browser: _PtsBrowserSession,
        report_match: ReportMatchResult,
        *,
        work_order_link: str,
        runtime: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        uploaded_files: list[str] = []
        uploaded_file_ids: list[str] = []
        uploaded_remote_files: list[dict[str, Any]] = []
        word_files = [str(path) for path in report_match.matched_files.get("word", [])]
        if not word_files:
            return {
                "action": "upload_report_files",
                "status": "failed",
                "error_type": "upload_failed",
                "error_message": "未找到可上传的 Word 报告",
                "retryable": False,
            }
        try:
            runtime_for_upload = runtime or {}
            for file_path in word_files:
                path = Path(file_path)
                uploaded_files.append(str(path))
                if self._use_frontend_upload_mode():
                    uploaded = await self._upload_file_via_frontend_ui(
                        browser=browser,
                        path=path,
                        work_order_link=work_order_link,
                        runtime=runtime_for_upload,
                    )
                    uploaded_file_id = str(uploaded.get("id") or "").strip()
                    uploaded_filename = str(uploaded.get("filename") or path.name).strip()
                    if uploaded_file_id:
                        uploaded_file_ids.append(uploaded_file_id)
                    uploaded_remote_files.append(
                        {
                            "id": uploaded_file_id,
                            "filename": uploaded_filename,
                        }
                    )
                else:
                    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
                    payload = await self._upload_file_via_browser(
                        browser,
                        path=path,
                        content_type=content_type,
                        work_order_link=work_order_link,
                    )
                    upload_err = payload.get("err")
                    if upload_err not in (None, 0, "0", ""):
                        raise _PtsRunnerError(
                            error_message=f"上传巡检报告返回错误 err={upload_err}",
                            error_type="response_invalid",
                            retryable=False,
                        )
                    uploaded_file_id = str(payload.get("id") or "").strip()
                    if not uploaded_file_id:
                        raise _PtsRunnerError(
                            error_message="上传巡检报告成功但未返回文件 ID",
                            error_type="response_invalid",
                            retryable=False,
                        )
                    uploaded_file_ids.append(uploaded_file_id)
                    uploaded_remote_files.append(
                        {
                            "id": uploaded_file_id,
                            "filename": str(payload.get("filename") or path.name),
                        }
                    )
            validation_error = self._validate_uploaded_report_results(
                local_files=word_files,
                uploaded_files=uploaded_files,
                uploaded_file_ids=uploaded_file_ids,
                uploaded_remote_files=uploaded_remote_files,
            )
            if validation_error is not None:
                return {
                    "action": "upload_report_files",
                    "status": "failed",
                    "error_type": "upload_failed",
                    "error_message": validation_error,
                    "retryable": False,
                    "uploaded_files": uploaded_files,
                    "uploaded_word_files": uploaded_files,
                    "uploaded_file_ids": [item for item in uploaded_file_ids if item],
                    "uploaded_remote_files": uploaded_remote_files,
                }
        except OSError as exc:
            return {
                "action": "upload_report_files",
                "status": "failed",
                "uploaded_files": uploaded_files,
                "error_type": "upload_failed",
                "error_message": f"读取巡检报告失败: {exc}",
                "retryable": False,
            }
        except _PtsRunnerError as exc:
            status = "manual_required" if exc.error_type == "permission_denied" else "failed"
            error_type = exc.error_type if status == "manual_required" else "upload_failed"
            return {
                "action": "upload_report_files",
                "status": status,
                "uploaded_files": uploaded_files,
                "error_type": error_type,
                "source_error_type": exc.error_type,
                "http_status": exc.http_status,
                "error_message": exc.error_message,
                "retryable": exc.retryable,
            }
        return {
            "action": "upload_report_files",
            "status": "success",
            "uploaded_files": uploaded_files,
            "uploaded_word_files": uploaded_files,
            "uploaded_file_ids": [item for item in uploaded_file_ids if item],
            "uploaded_remote_files": uploaded_remote_files,
        }

    def _use_frontend_upload_mode(self) -> bool:
        return bool(getattr(self.settings, "inspection_upload_via_frontend_enabled", True))

    def _should_add_work_order_info_after_upload(self) -> bool:
        return bool(getattr(self.settings, "inspection_add_work_order_info_enabled", False))

    async def _upload_file_via_frontend_ui(
        self,
        *,
        browser: _PtsBrowserSession,
        path: Path,
        work_order_link: str,
        runtime: dict[str, Any],
    ) -> dict[str, Any]:
        work_order_id = str(runtime.get("work_order_id") or "").strip()
        if not work_order_id:
            work_order_id = _resolve_work_order_id_from_link(work_order_link) or ""
        if not work_order_id:
            raise _PtsRunnerError(
                error_message="无法解析工单 ID，无法执行前端上传",
                error_type="response_invalid",
                retryable=False,
            )

        before_runtime = await self._load_pts_runtime(browser, work_order_id, work_order_link)
        before_ids = {
            str(item.get("id") or "").strip()
            for item in before_runtime.get("all_attached_files") or []
            if str(item.get("id") or "").strip()
        }

        trigger_result = await browser.trigger_frontend_file_upload_dialog(work_order_link)
        if trigger_result.get("status") != "success":
            raise _PtsRunnerError(
                error_message=str(trigger_result.get("error_message") or "触发上传窗口失败"),
                error_type=str(trigger_result.get("error_type") or "response_invalid"),
                retryable=False,
            )
        inject_result = await self._inject_file_into_frontend_upload_input(
            browser=browser,
            work_order_link=work_order_link,
            path=path,
        )
        if inject_result.get("status") != "success":
            # Fallback: when frontend input injection fails, try native file dialog.
            # This path depends on macOS automation permissions (System Events).
            choose_result = await browser.choose_file_in_dialog(str(path))
            if choose_result.get("status") != "success":
                inject_error = str(inject_result.get("error_message") or "前端输入框注入失败")
                choose_error = str(choose_result.get("error_message") or "选择本地文件失败")
                raise _PtsRunnerError(
                    error_message=(
                        f"PTS 前端上传失败: inject={inject_error}; choose={choose_error}; "
                        "如为权限问题，请在 macOS 隐私与安全性中允许终端/应用控制“系统事件”与“Google Chrome”。"
                    ),
                    error_type=str(choose_result.get("error_type") or "unknown_error"),
                    retryable=bool(choose_result.get("retryable", False)),
                )

        uploaded = await self._wait_attachment_after_ui_upload(
            browser=browser,
            work_order_id=work_order_id,
            work_order_link=work_order_link,
            expected_filename=path.name,
            before_ids=before_ids,
        )
        runtime.update(uploaded.get("runtime") or {})
        return {
            "id": uploaded.get("id"),
            "filename": uploaded.get("filename") or path.name,
        }

    async def _inject_file_into_frontend_upload_input(
        self,
        *,
        browser: _PtsBrowserSession,
        work_order_link: str,
        path: Path,
    ) -> dict[str, Any]:
        try:
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        except OSError as exc:
            return {
                "action": "inject_frontend_file_input",
                "status": "failed",
                "error_type": "upload_failed",
                "error_message": f"读取本地文件失败: {exc}",
                "retryable": False,
            }
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        script = f"""
        (() => {{
          try {{
            const inputCandidates = Array.from(document.querySelectorAll('input[type="file"]'))
              .filter((el) => !el.disabled);
            if (!inputCandidates.length) {{
              return JSON.stringify({{
                status: "failed",
                error: "未找到可用的 input[type=file]"
              }});
            }}
            const input = inputCandidates[inputCandidates.length - 1];
            const base64 = {json.dumps(encoded)};
            const binary = atob(base64);
            const bytes = new Uint8Array(binary.length);
            for (let i = 0; i < binary.length; i += 1) {{
              bytes[i] = binary.charCodeAt(i);
            }}
            const file = new File([bytes], {json.dumps(path.name)}, {{ type: {json.dumps(content_type)} }});
            const dt = new DataTransfer();
            dt.items.add(file);
            try {{
              input.files = dt.files;
            }} catch (e) {{
              Object.defineProperty(input, "files", {{
                value: dt.files,
                configurable: true,
              }});
            }}
            input.dispatchEvent(new Event("input", {{ bubbles: true }}));
            input.dispatchEvent(new Event("change", {{ bubbles: true }}));
            return JSON.stringify({{
              status: "success",
              filename: file.name,
              input_count: inputCandidates.length
            }});
          }} catch (error) {{
            return JSON.stringify({{
              status: "failed",
              error: String(error || "")
            }});
          }}
        }})()
        """
        raw_result = await browser.execute_js_on_project(work_order_link, script)
        result = raw_result if isinstance(raw_result, dict) else {}
        if str(result.get("status") or "").strip() == "success":
            return {
                "action": "inject_frontend_file_input",
                "status": "success",
                "filename": str(result.get("filename") or path.name),
            }
        return {
            "action": "inject_frontend_file_input",
            "status": "failed",
            "error_type": "response_invalid",
            "error_message": (
                f"注入前端上传 input 失败: {result.get('error') or 'unknown_error'}"
            ),
            "retryable": False,
        }

    async def _wait_attachment_after_ui_upload(
        self,
        *,
        browser: _PtsBrowserSession,
        work_order_id: str,
        work_order_link: str,
        expected_filename: str,
        before_ids: set[str],
    ) -> dict[str, Any]:
        expected_filename = str(expected_filename or "").strip()
        last_runtime: dict[str, Any] | None = None
        for attempt in range(1, 16):
            runtime = await self._load_pts_runtime(browser, work_order_id, work_order_link)
            last_runtime = runtime
            attachments = runtime.get("all_attached_files") or []
            for item in attachments:
                attachment_id = str(item.get("id") or "").strip()
                filename = str(item.get("filename") or "").strip()
                if attachment_id and attachment_id not in before_ids:
                    if expected_filename and filename and filename != expected_filename:
                        continue
                    return {"id": attachment_id, "filename": filename, "runtime": runtime}
            for item in attachments:
                attachment_id = str(item.get("id") or "").strip()
                filename = str(item.get("filename") or "").strip()
                if filename and expected_filename and filename == expected_filename:
                    return {"id": attachment_id, "filename": filename, "runtime": runtime}
            await asyncio.sleep(0.35)

        preview = ""
        if last_runtime is not None:
            preview = self._truncate_runtime_text(
                json.dumps(last_runtime.get("all_attached_files") or [], ensure_ascii=False),
                280,
            )
        raise _PtsRunnerError(
            error_message=(
                f"PTS 前端上传后未观察到附件变化: filename={expected_filename}; "
                f"attempts=15; attachment_preview={preview}"
            ),
            error_type="upload_failed",
            retryable=False,
        )

    @staticmethod
    def _validate_uploaded_report_results(
        *,
        local_files: list[str],
        uploaded_files: list[str],
        uploaded_file_ids: list[str],
        uploaded_remote_files: list[dict[str, Any]],
    ) -> str | None:
        local_count = len(local_files)
        uploaded_count = len(uploaded_files)
        remote_count = len(uploaded_remote_files)
        file_id_count = len([item for item in uploaded_file_ids if str(item).strip()])
        local_filenames = [Path(item).name.strip() for item in local_files if Path(item).name.strip()]
        remote_filenames = [
            str(item.get("filename") or "").strip()
            for item in uploaded_remote_files
            if str(item.get("filename") or "").strip()
        ]
        if local_count <= 0:
            return "未找到本次上传文件"
        if uploaded_count != local_count:
            return f"上传结果数量异常: local={local_count}, uploaded={uploaded_count}"
        if remote_count != local_count:
            return f"远端文件数量异常: local={local_count}, remote={remote_count}"
        if file_id_count != local_count:
            return f"远端文件 ID 数量异常: local={local_count}, file_ids={file_id_count}"
        if len(local_filenames) != local_count:
            return "本地上传文件名解析异常，无法确认上传结果"
        if len(remote_filenames) != remote_count:
            return "远端返回文件名解析异常，无法确认上传结果"
        if len(set(uploaded_file_ids)) != file_id_count:
            return "远端文件 ID 存在重复，无法确认一一对应关系"
        if len(set(local_filenames)) == local_count and len(set(remote_filenames)) == remote_count:
            if set(local_filenames) != set(remote_filenames):
                return (
                    "上传文件名不一致，无法确认一一对应关系: "
                    f"local={local_filenames}, remote={remote_filenames}"
                )
        for index, remote_item in enumerate(uploaded_remote_files):
            remote_id = str(remote_item.get("id") or "").strip()
            remote_name = str(remote_item.get("filename") or "").strip()
            if not remote_id:
                return f"远端文件缺少 ID(index={index})"
            if not remote_name:
                return f"远端文件缺少文件名(index={index})"
        return None

    async def _upload_file_via_browser(
        self,
        browser: _PtsBrowserSession,
        *,
        path: Path,
        content_type: str,
        work_order_link: str,
    ) -> dict[str, Any]:
        raw_bytes = path.read_bytes()
        encoded = base64.b64encode(raw_bytes).decode("ascii")
        script = f"""
        (() => {{
          try {{
            const binary = atob({encoded!r});
            const bytes = new Uint8Array(binary.length);
            for (let i = 0; i < binary.length; i += 1) {{
              bytes[i] = binary.charCodeAt(i);
            }}
            const blob = new Blob([bytes], {{ type: {content_type!r} }});
            const formData = new FormData();
            formData.append("file", blob, {path.name!r});
            formData.append("cat", "default");
            const xhr = new XMLHttpRequest();
            xhr.open("POST", "/api/upload", false);
            xhr.withCredentials = true;
            xhr.setRequestHeader("Accept", "application/json, text/plain, */*");
            xhr.send(formData);
            return JSON.stringify({{
              status: xhr.status,
              url: xhr.responseURL || "",
              contentType: xhr.getResponseHeader("content-type") || "",
              text: xhr.responseText || "",
            }});
          }} catch (error) {{
            return JSON.stringify({{
              status: 0,
              error: String(error),
              url: window.location.href || "",
              contentType: "",
              text: "",
            }});
          }}
        }})()
        """
        max_attempts = 3
        last_error: _PtsRunnerError | None = None
        for attempt in range(1, max_attempts + 1):
            result = await browser.execute_js_on_project(work_order_link, script)
            if not isinstance(result, dict):
                err = _PtsRunnerError(
                    error_message=f"上传巡检报告返回非法结果 attempt={attempt}/{max_attempts}",
                    error_type="response_invalid",
                    retryable=attempt < max_attempts,
                )
                if attempt < max_attempts:
                    await asyncio.sleep(0.25 * attempt)
                    last_error = err
                    continue
                raise err

            status = int(result.get("status") or 0)
            url = str(result.get("url") or "")
            content_type_header = str(result.get("contentType") or "")
            text = str(result.get("text") or "")
            raw_error = str(result.get("error") or "")
            if "pts.chaitin.net" not in url:
                err = _PtsRunnerError(
                    error_message=(
                        "上传巡检报告时浏览器上下文异常: "
                        f"url={self._truncate_runtime_text(url, 180)}; "
                        f"attempt={attempt}/{max_attempts}"
                    ),
                    error_type="response_invalid",
                    retryable=attempt < max_attempts,
                )
                if attempt < max_attempts:
                    await asyncio.sleep(0.25 * attempt)
                    last_error = err
                    continue
                raise err
            preview = self._truncate_runtime_text(text, 220)
            detail = (
                f"status={status}; attempt={attempt}/{max_attempts}; "
                f"url={self._truncate_runtime_text(url, 180)}; "
                f"content_type={self._truncate_runtime_text(content_type_header, 80)}; "
                f"preview={preview}; raw_error={self._truncate_runtime_text(raw_error, 120)}"
            )

            if "auth.chaitin.net/login" in url or status in {401, 403}:
                error_type = "permission_denied" if status == 403 else "session_expired"
                error_message = (
                    "当前 PTS 账号无权上传巡检报告，请人工处理"
                    if status == 403
                    else "PTS 会话已失效，请重新登录 PTS 或更新 Cookie"
                )
                raise _PtsRunnerError(
                    error_message=f"{error_message}; {detail}",
                    error_type=error_type,
                    retryable=False,
                    http_status=status or None,
                )

            if status >= 400:
                retryable = status in {0, 404, 429} or status >= 500
                err = _PtsRunnerError(
                    error_message=f"上传巡检报告失败: {status}; {detail}",
                    error_type="http_error" if status >= 500 else "business_rejected",
                    retryable=retryable and attempt < max_attempts,
                    http_status=status,
                )
                if retryable and attempt < max_attempts:
                    await asyncio.sleep(0.25 * attempt)
                    last_error = err
                    continue
                raise err

            if not text.strip():
                err = _PtsRunnerError(
                    error_message=f"上传巡检报告返回空响应; {detail}",
                    error_type="empty_response",
                    retryable=attempt < max_attempts,
                    http_status=status or None,
                )
                if attempt < max_attempts:
                    await asyncio.sleep(0.25 * attempt)
                    last_error = err
                    continue
                raise err

            try:
                payload = json.loads(text)
                return payload
            except ValueError as exc:
                err = _PtsRunnerError(
                    error_message=f"上传巡检报告返回非法 JSON; {detail}",
                    error_type="response_invalid",
                    retryable=attempt < max_attempts,
                    http_status=status or None,
                )
                if attempt < max_attempts:
                    await asyncio.sleep(0.25 * attempt)
                    last_error = err
                    continue
                raise err from exc

        if last_error is not None:
            raise last_error
        raise _PtsRunnerError(
            error_message="上传巡检报告失败: 未获取到有效响应",
            error_type="response_invalid",
            retryable=False,
        )

    async def _pts_add_work_order_info(
        self,
        browser: _PtsBrowserSession,
        *,
        work_order_id: str,
        work_order_link: str,
        customer_name: str,
        uploaded_file_ids: list[str],
        uploaded_remote_files: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        del customer_name
        if not uploaded_file_ids:
            return {
                "action": "add_work_order_info",
                "status": "failed",
                "uploaded_file_ids": [],
                "error_type": "attachment_bind_failed",
                "error_message": "缺少可绑定的上传文件 ID，无法写入工单附件绑定信息",
                "retryable": False,
            }
        note = self._build_uploaded_files_note(
            uploaded_file_ids=uploaded_file_ids,
            uploaded_remote_files=uploaded_remote_files or [],
        )
        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            try:
                open_result = await browser.open_project(work_order_link)
                if str(open_result.get("status") or "") != "success":
                    return {
                        "action": "add_work_order_info",
                        "status": "failed",
                        "uploaded_file_ids": uploaded_file_ids,
                        "error_type": "attachment_bind_failed",
                        "error_message": f"写入工单处理记录前切换页面失败: {open_result.get('error_message') or 'unknown_error'}",
                            "retryable": False,
                    }
                add_result = await self._submit_work_order_info_update(
                    browser=browser,
                    work_order_id=work_order_id,
                    work_order_link=work_order_link,
                    note=note,
                    uploaded_file_ids=uploaded_file_ids,
                )
                if not add_result:
                    return {
                        "action": "add_work_order_info",
                        "status": "failed",
                        "uploaded_file_ids": uploaded_file_ids,
                        "error_type": "attachment_bind_failed",
                        "error_message": "工单处理记录写入未生效",
                        "retryable": False,
                    }
                return {
                    "action": "add_work_order_info",
                    "status": "success",
                    "uploaded_file_ids": uploaded_file_ids,
                }
            except _PtsRunnerError as exc:
                should_retry = (
                    exc.error_type in {"business_rejected", "response_invalid", "http_error"}
                    and attempt < max_attempts
                )
                if should_retry:
                    # Recover to the expected PTS work-order page before retrying graphql mutation.
                    await browser.open_project(work_order_link)
                    await asyncio.sleep(0.25 * attempt)
                    continue
                mapped_error_type = (
                    exc.error_type
                    if exc.error_type in {"session_expired", "permission_denied"}
                    else "attachment_bind_failed"
                )
                return {
                    "action": "add_work_order_info",
                    "status": "failed",
                    "uploaded_file_ids": uploaded_file_ids,
                    "error_message": (
                        f"{exc.error_message}; attempt={attempt}/{max_attempts}; "
                        f"source_error_type={exc.error_type}; http_status={exc.http_status}"
                    ),
                    "error_type": mapped_error_type,
                    "source_error_type": exc.error_type,
                    "http_status": exc.http_status,
                    "retryable": False,
                }

    async def _submit_work_order_info_update(
        self,
        *,
        browser: _PtsBrowserSession,
        work_order_id: str,
        work_order_link: str,
        note: str,
        uploaded_file_ids: list[str],
    ) -> bool:
        # Primary path: bind attachments directly by work-order ID.
        # This path works with executable runs and keeps attachment binding explicit.
        payload = await browser.graphql_payload(
            {
                "operationName": "AddWorkOrderInfo",
                "variables": {
                    "id": work_order_id,
                    "note": note,
                    "file": uploaded_file_ids,
                },
                "query": (
                    "mutation AddWorkOrderInfo($id: ID!, $note: String, $file: [ID!]) { "
                    "add_work_order_info(id: $id, note: $note, file: $file) "
                    "}"
                ),
            }
        )
        add_result = payload.get("add_work_order_info")
        if isinstance(add_result, bool):
            return add_result
        if add_result:
            return True

        # Fallback path: some pages edit an existing info row via UpdateWorkOrderInfo.
        # For this API, `id` must be info_id (not work_order_id), so we resolve latest info_id first.
        runtime = await self._load_pts_runtime(
            browser,
            work_order_id=work_order_id,
            work_order_link=work_order_link,
        )
        info_list = runtime.get("info_list") or []
        info_id = ""
        for info in reversed(info_list):
            info_id = str((info or {}).get("id") or "").strip()
            if info_id:
                break
        if not info_id:
            return False

        fallback_payload = await browser.graphql_payload(
            {
                "operationName": "UpdateWorkOrderInfo",
                "variables": {
                    "id": info_id,
                    "note": note,
                    "file": uploaded_file_ids,
                },
                "query": (
                    "mutation UpdateWorkOrderInfo($id: ID!, $note: String, $file: [ID!]) { "
                    "update_work_order_info(id: $id, note: $note, file: $file) "
                    "}"
                ),
            }
        )
        update_result = fallback_payload.get("update_work_order_info")
        if isinstance(update_result, bool):
            return update_result
        return bool(update_result)

    async def _pts_complete_work_order(
        self,
        browser: _PtsBrowserSession,
        runtime: dict[str, Any],
    ) -> dict[str, Any]:
        stage_path = [str(runtime.get("current_stage_name") or "").strip()]
        max_steps = 4
        for _ in range(max_steps):
            stage_name = str(runtime.get("current_stage_name") or "").strip()
            if stage_name in {"审核工单", "完成"} or runtime.get("is_finished"):
                return {
                    "action": "complete_inspection",
                    "status": "success",
                    "final_link": runtime["work_order_link"],
                    "stage_before": stage_path[0] if stage_path else None,
                    "stage_after": stage_name,
                    "stage_path": [item for item in stage_path if item],
                    "closure_transition_confirmed": True,
                }
            try:
                await browser.graphql_payload(
                    {
                        "operationName": "ConfirmWorkOrderStage",
                        "variables": {
                            "id": runtime["work_order_id"],
                            "claim_by": runtime["me_id"],
                            "customer_affect_owner": [runtime["me_id"]],
                            "technical_owner": [runtime["me_id"]],
                        },
                        "query": (
                            "mutation ConfirmWorkOrderStage($id: ID!, $claim_by: ID, $customer_affect_owner: [ID!], $technical_owner: [ID!], $contact: VisitContactParam, $renew_input: InputRenewProcessWorkorder) { "
                            "confirm_work_order_stage(id: $id, claim_by: $claim_by, customer_affect_owner: $customer_affect_owner, technical_owner: $technical_owner, contact: $contact, renew_input: $renew_input) "
                            "}"
                        ),
                    }
                )
            except _PtsRunnerError as exc:
                if _looks_like_permission_error(exc.error_message):
                    return {
                        "action": "complete_inspection",
                        "status": "manual_required",
                        "error_message": "当前 PTS 账号无权推进巡检工单阶段，请人工处理",
                        "error_type": "permission_denied",
                        "retryable": False,
                        "stage_path": [item for item in stage_path if item],
                    }
                return {
                    "action": "complete_inspection",
                    "status": "failed",
                    "error_message": exc.error_message,
                    "error_type": exc.error_type,
                    "http_status": exc.http_status,
                    "retryable": exc.retryable,
                    "stage_path": [item for item in stage_path if item],
                }
            refreshed = await self._load_pts_runtime(
                browser,
                runtime["work_order_id"],
                runtime["work_order_link"],
            )
            runtime.update(refreshed)
            current = str(runtime.get("current_stage_name") or "").strip()
            if current and current not in stage_path:
                stage_path.append(current)
        return {
            "action": "complete_inspection",
            "status": "failed",
            "error_type": "business_rejected",
            "error_message": "巡检工单未能推进到审核工单阶段",
            "retryable": False,
            "stage_before": stage_path[0] if stage_path else None,
            "stage_after": str(runtime.get("current_stage_name") or "").strip(),
            "stage_path": [item for item in stage_path if item],
            "closure_transition_confirmed": False,
        }

    async def _pts_postcheck_work_order(
        self,
        browser: _PtsBrowserSession,
        *,
        runtime: dict[str, Any],
        uploaded_file_ids: list[str],
        uploaded_files: list[str],
        uploaded_remote_files: list[dict[str, Any]] | None = None,
        expected_uploaded_filenames: list[str] | None = None,
    ) -> dict[str, Any]:
        refreshed = await self._load_pts_runtime(
            browser,
            runtime["work_order_id"],
            runtime["work_order_link"],
        )
        runtime.update(refreshed)
        closure_confirmed = self._runtime_is_closed_stage(runtime)
        attachment_check = self._runtime_contains_uploaded_reports(
            runtime,
            uploaded_file_ids=uploaded_file_ids,
            uploaded_files=uploaded_files,
            uploaded_remote_files=uploaded_remote_files or [],
            expected_uploaded_filenames=expected_uploaded_filenames or [],
        )
        report_attached_confirmed = attachment_check["report_attached_confirmed"]
        postcheck_passed = closure_confirmed and report_attached_confirmed
        if postcheck_passed:
            return {
                "action": "postcheck_inspection_closure",
                "status": "success",
                "closure_confirmed": True,
                "report_attached_confirmed": True,
                "postcheck_passed": True,
                "postcheck_source": "pts_browser_session",
                "stage_after": str(runtime.get("current_stage_name") or "").strip(),
                "is_finished": bool(runtime.get("is_finished")),
                "uploaded_file_ids_expected": [str(item).strip() for item in uploaded_file_ids if str(item).strip()],
                "uploaded_file_ids_found": attachment_check["uploaded_file_ids_found"],
                "uploaded_filenames_expected": attachment_check["uploaded_filenames_expected"],
                "uploaded_filenames_found": attachment_check["uploaded_filenames_found"],
                "attachment_match_mode": attachment_check["attachment_match_mode"],
            }
        return {
            "action": "postcheck_inspection_closure",
            "status": "failed",
            "closure_confirmed": closure_confirmed,
            "report_attached_confirmed": report_attached_confirmed,
            "postcheck_passed": False,
            "postcheck_source": "pts_browser_session",
            "stage_after": str(runtime.get("current_stage_name") or "").strip(),
            "is_finished": bool(runtime.get("is_finished")),
            "uploaded_file_ids_expected": [str(item).strip() for item in uploaded_file_ids if str(item).strip()],
            "uploaded_file_ids_found": attachment_check["uploaded_file_ids_found"],
            "uploaded_filenames_expected": attachment_check["uploaded_filenames_expected"],
            "uploaded_filenames_found": attachment_check["uploaded_filenames_found"],
            "attachment_match_mode": attachment_check["attachment_match_mode"],
            "error_type": "postcheck_failed",
            "error_message": "巡检工单动作已执行，但最终校验未通过",
            "retryable": False,
        }

    async def _pts_precheck_uploaded_attachments(
        self,
        browser: _PtsBrowserSession,
        *,
        runtime: dict[str, Any],
        uploaded_file_ids: list[str],
        uploaded_files: list[str],
        uploaded_remote_files: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        refreshed = await self._load_pts_runtime(
            browser,
            runtime["work_order_id"],
            runtime["work_order_link"],
        )
        runtime.update(refreshed)
        attachment_check = self._runtime_contains_uploaded_reports(
            runtime,
            uploaded_file_ids=uploaded_file_ids,
            uploaded_files=uploaded_files,
            uploaded_remote_files=uploaded_remote_files or [],
            expected_uploaded_filenames=[],
        )
        if attachment_check["report_attached_confirmed"]:
            return {
                "action": "precheck_uploaded_attachments",
                "status": "success",
                "uploaded_file_ids_expected": [str(item).strip() for item in uploaded_file_ids if str(item).strip()],
                "uploaded_file_ids_found": attachment_check["uploaded_file_ids_found"],
                "uploaded_filenames_expected": attachment_check["uploaded_filenames_expected"],
                "uploaded_filenames_found": attachment_check["uploaded_filenames_found"],
                "attachment_match_mode": attachment_check["attachment_match_mode"],
            }
        return {
            "action": "precheck_uploaded_attachments",
            "status": "failed",
            "error_type": "attachment_bind_failed",
            "error_message": "巡检报告未出现在工单附件中",
            "retryable": False,
            "uploaded_file_ids_expected": [str(item).strip() for item in uploaded_file_ids if str(item).strip()],
            "uploaded_file_ids_found": attachment_check["uploaded_file_ids_found"],
            "uploaded_filenames_expected": attachment_check["uploaded_filenames_expected"],
            "uploaded_filenames_found": attachment_check["uploaded_filenames_found"],
            "attachment_match_mode": attachment_check["attachment_match_mode"],
        }

    @staticmethod
    def _runtime_is_closed_stage(runtime: dict[str, Any]) -> bool:
        return bool(runtime.get("is_finished")) or str(runtime.get("current_stage_name") or "").strip() in {
            "审核工单",
            "完成",
        }

    def _runtime_contains_uploaded_reports(
        self,
        runtime: dict[str, Any],
        *,
        uploaded_file_ids: list[str],
        uploaded_files: list[str],
        uploaded_remote_files: list[dict[str, Any]],
        expected_uploaded_filenames: list[str],
    ) -> dict[str, Any]:
        attached_files = runtime.get("all_attached_files") or []
        attached_ids = {
            str(item.get("id") or "").strip()
            for item in attached_files
            if str(item.get("id") or "").strip()
        }
        attached_filenames = {
            str(item.get("filename") or "").strip()
            for item in attached_files
            if str(item.get("filename") or "").strip()
        }
        expected_ids = [str(item).strip() for item in uploaded_file_ids if str(item).strip()]
        expected_filenames = self._extract_uploaded_filenames(uploaded_files, uploaded_remote_files)
        if not expected_filenames and expected_uploaded_filenames:
            expected_filenames = self._normalize_filename_list(expected_uploaded_filenames)
        if not expected_ids and not expected_filenames:
            return {
                "report_attached_confirmed": False,
                "uploaded_file_ids_found": [],
                "uploaded_filenames_expected": [],
                "uploaded_filenames_found": [],
                "attachment_match_mode": "none",
            }
        found_ids = [item for item in expected_ids if item in attached_ids]
        found_filenames = [item for item in expected_filenames if item in attached_filenames]
        if expected_ids:
            report_attached_confirmed = set(found_ids) == set(expected_ids)
            match_mode = "file_id"
        else:
            report_attached_confirmed = bool(
                expected_filenames and set(found_filenames) == set(expected_filenames)
            )
            match_mode = "filename_fallback"
        return {
            "report_attached_confirmed": report_attached_confirmed,
            "uploaded_file_ids_found": found_ids,
            "uploaded_filenames_expected": expected_filenames,
            "uploaded_filenames_found": found_filenames,
            "attachment_match_mode": match_mode,
        }

    @staticmethod
    def _extract_uploaded_filenames(
        uploaded_files: list[str],
        uploaded_remote_files: list[dict[str, Any]],
    ) -> list[str]:
        filenames: list[str] = []
        for item in uploaded_remote_files:
            filename = str(item.get("filename") or "").strip()
            if filename:
                filenames.append(filename)
        for file_path in uploaded_files:
            filename = Path(file_path).name.strip()
            if filename:
                filenames.append(filename)
        return list(dict.fromkeys(filenames))

    @staticmethod
    def _build_uploaded_files_note(
        *,
        uploaded_file_ids: list[str],
        uploaded_remote_files: list[dict[str, Any]],
    ) -> str:
        # Keep note empty so PTS timeline focuses on real attached Word files.
        # Attachment binding is done by `file` IDs in mutation payload.
        del uploaded_file_ids, uploaded_remote_files
        return ""

    @staticmethod
    def _normalize_filename_list(items: list[str]) -> list[str]:
        filenames: list[str] = []
        for item in items:
            filename = Path(str(item)).name.strip()
            if filename:
                filenames.append(filename)
        return list(dict.fromkeys(filenames))

    @staticmethod
    def _resolve_report_match_word_files(report_match: ReportMatchResult) -> list[str]:
        matched_files = report_match.matched_files or {}
        word_files = matched_files.get("word")
        if not isinstance(word_files, list):
            return []
        return [str(item) for item in word_files if str(item).strip()]

    async def _legacy_open_work_order(self, client: httpx.AsyncClient, work_order_link: str) -> dict[str, Any]:
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

    async def _legacy_upload_reports(
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
            for file_type in ("word",):
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
                "uploaded_word_files": uploaded_files,
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

    async def _legacy_assign_owner(
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

    async def _legacy_add_member_if_missing(
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

    async def _legacy_complete_work_order(
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

    def _archive_uploaded_reports(self, uploaded_files: list[str]) -> dict[str, Any]:
        archive_root = Path(self.settings.inspection_report_root) / "已上传的文档"
        archive_root.mkdir(parents=True, exist_ok=True)
        archived_files: list[str] = []
        try:
            for file_path in uploaded_files:
                source = Path(file_path)
                if not source.exists():
                    continue
                target = archive_root / source.name
                if target.exists():
                    target = archive_root / f"{source.stem}-{self._timestamp_suffix(source)}{source.suffix}"
                shutil.move(str(source), str(target))
                archived_files.append(str(target))
        except OSError as exc:
            return {
                "action": "archive_uploaded_reports",
                "status": "failed",
                "error_type": "unknown_error",
                "error_message": f"归档巡检报告失败: {exc}",
                "retryable": False,
                "archived_files": archived_files,
            }
        return {
            "action": "archive_uploaded_reports",
            "status": "success",
            "archived_files": archived_files,
            "archive_root": str(archive_root),
        }

    def _pts_http_headers(self, *, referer: str) -> dict[str, str]:
        return {
            "Cookie": self.settings.pts_cookie_header,
            "Origin": self.settings.pts_base_url.rstrip("/"),
            "Referer": referer,
            "Accept": "*/*",
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
            ),
        }

    def _base_diagnostics(self) -> dict[str, Any]:
        base_url = self.settings.inspection_real_base_url or self.settings.pts_base_url
        return build_runner_diagnostics(
            module_code="inspection",
            runner="InspectionRealRunner",
            mode="real",
            base_url=base_url,
            assign_endpoint_template=self.settings.inspection_real_assign_endpoint_template,
            add_member_endpoint_template=self.settings.inspection_real_add_member_endpoint_template,
            upload_endpoint_template=self.settings.inspection_real_upload_endpoint_template,
            complete_endpoint_template=self.settings.inspection_real_complete_endpoint_template,
            token_header=self.settings.inspection_real_token_header,
            pts_base_url=self.settings.pts_base_url,
            pts_verify_ssl=self.settings.pts_verify_ssl,
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

    @staticmethod
    def _timestamp_suffix(path: Path) -> str:
        stat = path.stat()
        return str(int(stat.st_mtime))

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


def _resolve_work_order_id_from_link(work_order_link: str | None) -> str | None:
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


def _looks_like_permission_error(message: str | None) -> bool:
    text = str(message or "").lower()
    return any(token in text for token in ["permission", "forbidden", "无权", "权限", "not authorized"])
