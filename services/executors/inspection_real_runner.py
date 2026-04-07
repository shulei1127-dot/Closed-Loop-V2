from __future__ import annotations

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
                            "error_type": "business_rejected",
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
                    expected_uploaded_files = self._resolve_report_match_word_files(report_match)
                    already_closed = normalize_action_result(
                        {
                        "action": "complete_inspection",
                        "status": "success",
                        "final_link": work_order_link,
                        "stage_before": runtime["current_stage_name"],
                        "stage_after": runtime["current_stage_name"],
                        "already_closed": True,
                        "closure_transition_confirmed": True,
                        }
                    )
                    action_results.append(already_closed)
                    postcheck_result = normalize_action_result(
                        await self._pts_postcheck_work_order(
                            browser,
                            runtime=runtime,
                            uploaded_file_ids=[],
                            uploaded_files=[],
                            uploaded_remote_files=[],
                            expected_uploaded_filenames=expected_uploaded_files,
                        )
                    )
                    action_results.append(postcheck_result)
                    diagnostics["postcheck"] = self._build_postcheck_diagnostics(postcheck_result)
                    if postcheck_result["status"] != "success":
                        return self._failure_outcome(
                            diagnostics=diagnostics,
                            action_results=action_results,
                            action_result=postcheck_result,
                            fallback_message="巡检工单已处于闭环阶段，但最终校验未通过",
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
                    await self._pts_upload_reports(browser, report_match)
                )
                action_results.append(upload_result)
                if upload_result["status"] != "success":
                    return self._failure_outcome(
                        diagnostics=diagnostics,
                        action_results=action_results,
                        action_result=upload_result,
                        fallback_message="上传巡检报告失败",
                    )

                add_info_result = normalize_action_result(
                    await self._pts_add_work_order_info(
                        browser,
                        work_order_id=work_order_id,
                        customer_name=str(context.normalized_data.get("customer_name") or ""),
                        uploaded_file_ids=upload_result.get("uploaded_file_ids", []),
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
        me = await browser.graphql_payload(
            {
                "operationName": "Me",
                "query": "query Me { me { id name } }",
            }
        )
        work_order = await browser.graphql_payload(
            {
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
            }
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
                    "error_type": "permission_denied",
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
                    "error_type": "permission_denied",
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
    ) -> dict[str, Any]:
        uploaded_files: list[str] = []
        uploaded_file_ids: list[str] = []
        uploaded_remote_files: list[dict[str, Any]] = []
        word_files = [str(path) for path in report_match.matched_files.get("word", [])]
        if not word_files:
            return {
                "action": "upload_report_files",
                "status": "failed",
                "error_message": "未找到可上传的 Word 报告",
                "retryable": False,
            }
        try:
            for file_path in word_files:
                path = Path(file_path)
                uploaded_files.append(str(path))
                content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
                payload = await self._upload_file_via_browser(
                    browser,
                    path=path,
                    content_type=content_type,
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
        except OSError as exc:
            return {
                "action": "upload_report_files",
                "status": "failed",
                "uploaded_files": uploaded_files,
                "error_type": "unknown_error",
                "error_message": f"读取巡检报告失败: {exc}",
                "retryable": False,
            }
        except _PtsRunnerError as exc:
            status = "manual_required" if exc.error_type == "permission_denied" else "failed"
            return {
                "action": "upload_report_files",
                "status": status,
                "uploaded_files": uploaded_files,
                "error_type": exc.error_type,
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

    async def _upload_file_via_browser(
        self,
        browser: _PtsBrowserSession,
        *,
        path: Path,
        content_type: str,
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
            formData.append("cat", "default");
            formData.append("file", blob, {path.name!r});
            const xhr = new XMLHttpRequest();
            xhr.open("POST", "/api/upload", false);
            xhr.withCredentials = true;
            xhr.send(formData);
            return JSON.stringify({{
              status: xhr.status,
              url: xhr.responseURL || "",
              text: xhr.responseText || "",
            }});
          }} catch (error) {{
            return JSON.stringify({{
              status: 0,
              error: String(error),
              url: window.location.href || "",
            }});
          }}
        }})()
        """
        result = await browser.execute_js(script)
        if not isinstance(result, dict):
            raise _PtsRunnerError(
                error_message="上传巡检报告返回非法结果",
                error_type="response_invalid",
                retryable=False,
            )
        status = int(result.get("status") or 0)
        url = str(result.get("url") or "")
        text = str(result.get("text") or "")
        if "auth.chaitin.net/login" in url or status in {401, 403}:
            error_type = "permission_denied" if status == 403 else "session_expired"
            error_message = (
                "当前 PTS 账号无权上传巡检报告，请人工处理"
                if status == 403
                else "PTS 会话已失效，请重新登录 PTS 或更新 Cookie"
            )
            raise _PtsRunnerError(
                error_message=error_message,
                error_type=error_type,
                retryable=False,
                http_status=status or None,
            )
        if status >= 400:
            raise _PtsRunnerError(
                error_message=f"上传巡检报告失败: {status}",
                error_type="http_error" if status >= 500 else "business_rejected",
                retryable=status >= 500,
                http_status=status,
            )
        try:
            payload = json.loads(text)
        except ValueError as exc:
            raise _PtsRunnerError(
                error_message="上传巡检报告返回非法 JSON",
                error_type="response_invalid",
                retryable=False,
            ) from exc
        return payload

    async def _pts_add_work_order_info(
        self,
        browser: _PtsBrowserSession,
        *,
        work_order_id: str,
        customer_name: str,
        uploaded_file_ids: list[str],
    ) -> dict[str, Any]:
        del customer_name
        # Chrome AppleScript execution is unstable with non-ASCII note payloads here.
        note = "inspection report uploaded automatically"
        try:
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
            if not add_result:
                return {
                    "action": "add_work_order_info",
                    "status": "failed",
                    "uploaded_file_ids": uploaded_file_ids,
                    "error_type": "business_rejected",
                    "error_message": "工单处理记录写入未生效",
                    "retryable": False,
                }
            return {
                "action": "add_work_order_info",
                "status": "success",
                "uploaded_file_ids": uploaded_file_ids,
                "note": note,
            }
        except _PtsRunnerError as exc:
            return {
                "action": "add_work_order_info",
                "status": "failed",
                "uploaded_file_ids": uploaded_file_ids,
                "error_message": exc.error_message,
                "error_type": exc.error_type,
                "http_status": exc.http_status,
                "retryable": exc.retryable,
            }

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
            }
        return {
            "action": "precheck_uploaded_attachments",
            "status": "failed",
            "error_type": "upload_failed",
            "error_message": "巡检报告未出现在工单附件中",
            "retryable": False,
            "uploaded_file_ids_expected": [str(item).strip() for item in uploaded_file_ids if str(item).strip()],
            "uploaded_file_ids_found": attachment_check["uploaded_file_ids_found"],
            "uploaded_filenames_expected": attachment_check["uploaded_filenames_expected"],
            "uploaded_filenames_found": attachment_check["uploaded_filenames_found"],
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
            }
        found_ids = [item for item in expected_ids if item in attached_ids]
        found_filenames = [item for item in expected_filenames if item in attached_filenames]
        if expected_ids:
            report_attached_confirmed = set(found_ids) == set(expected_ids)
        else:
            report_attached_confirmed = bool(
                expected_filenames and set(found_filenames) == set(expected_filenames)
            )
        return {
            "report_attached_confirmed": report_attached_confirmed,
            "uploaded_file_ids_found": found_ids,
            "uploaded_filenames_expected": expected_filenames,
            "uploaded_filenames_found": found_filenames,
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
