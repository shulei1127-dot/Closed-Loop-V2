from __future__ import annotations

from typing import Any

from core.config import Settings, get_settings
from services.executors.runner_contract import build_simulated_runner_diagnostics
from services.executors.schemas import ExecutionResult, ExecutorContext
from services.executors.inspection_real_runner import InspectionRealRunner
from services.report_matching.matcher import InspectionReportMatcher
from services.report_matching.scanner import InspectionReportScanner


class InspectionExecutor:
    module_code = "inspection"
    task_type = "inspection_close"
    executor_version = "phase9-inspection-real-v1"

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        scanner: InspectionReportScanner | None = None,
        matcher: InspectionReportMatcher | None = None,
        real_runner: InspectionRealRunner | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.report_root = self.settings.inspection_report_root
        self.scanner = scanner or InspectionReportScanner(self.report_root)
        self.matcher = matcher or InspectionReportMatcher(required_file_types=("word",))
        self.real_runner = real_runner or InspectionRealRunner(self.settings)

    def precheck(self, context: ExecutorContext) -> ExecutionResult:
        mismatch = self._validate_context(context)
        if mismatch is not None:
            return mismatch

        missing_fields = self._missing_required_fields(context.normalized_data)
        if missing_fields:
            return self._precheck_failed(
                "关键字段缺失，无法执行",
                context,
                payload={"missing_fields": missing_fields},
            )
        if context.normalized_data.get("inspection_done") is not True:
            return self._precheck_failed("inspection_done != true，禁止执行", context)
        if context.normalized_data.get("executor_name") != "舒磊":
            return self._precheck_failed("executor_name != 舒磊，禁止执行", context)
        if context.normalized_data.get("work_order_closed") is True:
            return self._precheck_failed("work_order_closed == true，禁止执行", context)

        match_result = self._match_report(context)
        if match_result.manual_required:
            return self._manual_required(context, match_result)

        actions = self._build_actions(context, match_result)
        if self._should_use_real_execution():
            valid, diagnostics, error_message = self.real_runner.validate()
            if not valid:
                return self._precheck_failed(
                    error_message or "inspection 真实执行配置缺失",
                    context,
                    actions=actions,
                    match_result=match_result,
                    runner_diagnostics=diagnostics,
                )
            return ExecutionResult(
                run_status="precheck_passed",
                executor_version=self.executor_version,
                result_payload=self._build_payload(
                    context,
                    actions=actions,
                    match_result=match_result,
                    execution_mode="real_ready",
                    runner_diagnostics=diagnostics,
                    precheck_summary={"real_execution_ready": True},
                ),
            )

        return self._precheck_failed(
            "巡检真实执行未启用，当前不允许模拟闭环",
            context,
            actions=actions,
            match_result=match_result,
            runner_diagnostics=self._simulated_runner_diagnostics(reason="real_execution_disabled"),
            payload={"reason": "inspection_real_execution_required"},
        )

    async def dry_run(self, context: ExecutorContext) -> ExecutionResult:
        match_result = self._match_report(context)
        actions = self._build_actions(context, match_result)
        if match_result.manual_required:
            return self._manual_required(context, match_result, actions=actions)

        return ExecutionResult(
            run_status="dry_run_ready",
            executor_version=self.executor_version,
            result_payload=self._build_payload(
                context,
                actions=actions,
                match_result=match_result,
                execution_mode="dry_run",
                runner_diagnostics=self._simulated_runner_diagnostics(reason="dry_run"),
            ),
        )

    async def execute(self, context: ExecutorContext) -> ExecutionResult:
        match_result = self._match_report(context)
        actions = self._build_actions(context, match_result)
        if match_result.manual_required:
            return self._manual_required(context, match_result, actions=actions)

        if not self._should_use_real_execution():
            return self._precheck_failed(
                "巡检真实执行未启用，当前不允许模拟闭环",
                context,
                actions=actions,
                match_result=match_result,
                runner_diagnostics=self._simulated_runner_diagnostics(reason="real_execution_disabled"),
                payload={"reason": "inspection_real_execution_required"},
            )

        valid, diagnostics, error_message = self.real_runner.validate()
        if not valid:
            return self._precheck_failed(
                error_message or "inspection 真实执行配置缺失",
                context,
                actions=actions,
                match_result=match_result,
                runner_diagnostics=diagnostics,
            )

        outcome = await self.real_runner.run(context, actions, match_result)
        execution_mode = "real" if outcome.run_status == "success" else "real_attempted"
        if outcome.run_status == "success":
            return ExecutionResult(
                run_status="success",
                executor_version=self.executor_version,
                final_link=outcome.final_link,
                result_payload=self._build_payload(
                    context,
                    actions=actions,
                    action_results=outcome.action_results,
                    match_result=match_result,
                    execution_mode=execution_mode,
                    runner_diagnostics=outcome.runner_diagnostics,
                ),
            )
        if outcome.run_status == "manual_required":
            return ExecutionResult(
                run_status="manual_required",
                manual_required=True,
                executor_version=self.executor_version,
                error_message=outcome.error_message,
                result_payload=self._build_payload(
                    context,
                    actions=actions,
                    action_results=outcome.action_results,
                    match_result=match_result,
                    execution_mode=execution_mode,
                    runner_diagnostics=outcome.runner_diagnostics,
                ),
            )

        return ExecutionResult(
            run_status="failed",
            executor_version=self.executor_version,
            error_message=outcome.error_message,
            retryable=outcome.retryable,
            result_payload=self._build_payload(
                context,
                actions=actions,
                action_results=outcome.action_results,
                match_result=match_result,
                execution_mode=execution_mode,
                runner_diagnostics=outcome.runner_diagnostics,
            ),
        )

    def healthcheck(self) -> dict[str, object]:
        valid, diagnostics, error_message = self.real_runner.validate()
        return {
            "ok": True,
            "module_code": self.module_code,
            "task_type": self.task_type,
            "executor_version": self.executor_version,
            "report_root": self.report_root,
            "real_execution_enabled": self.settings.enable_real_execution,
            "inspection_real_execution_enabled": self.settings.inspection_real_execution_enabled,
            "real_runner_ready": valid,
            "real_runner_error": error_message,
            "runner_diagnostics": diagnostics,
        }

    def _validate_context(self, context: ExecutorContext) -> ExecutionResult | None:
        if context.module_code != self.module_code or context.task_type != self.task_type:
            return ExecutionResult(
                run_status="precheck_failed",
                error_message="executor 与 module_code / task_type 不匹配",
                executor_version=self.executor_version,
                result_payload=self._build_payload(
                    context,
                    execution_mode="simulated",
                    runner_diagnostics=self._simulated_runner_diagnostics(reason="executor_mismatch"),
                    extra_payload={
                        "expected_module_code": self.module_code,
                        "expected_task_type": self.task_type,
                    },
                ),
            )
        return None

    def _missing_required_fields(self, data: dict[str, Any]) -> list[str]:
        required_fields = {
            "customer_name": data.get("customer_name"),
            "inspection_done": data.get("inspection_done"),
            "executor_name": data.get("executor_name"),
        }
        missing = [key for key, value in required_fields.items() if value in (None, "", False)]
        if not data.get("work_order_link") and not data.get("work_order_id"):
            missing.append("work_order_link_or_id")
        return missing

    def _should_use_real_execution(self) -> bool:
        return self.settings.enable_real_execution and self.settings.inspection_real_execution_enabled

    def _match_report(self, context: ExecutorContext):
        lookup_customer = (
            context.planned_payload.get("report_lookup_customer")
            or context.normalized_data.get("customer_name")
            or context.planned_payload.get("customer_name")
            or ""
        )
        files = self.scanner.scan()
        return self.matcher.match(str(lookup_customer), files)

    def _build_actions(self, context: ExecutorContext, match_result) -> list[dict[str, Any]]:
        upload_files = match_result.matched_files
        return [
            {
                "action": "open_inspection_work_order",
                "target": context.normalized_data.get("work_order_link") or context.normalized_data.get("work_order_id"),
            },
            {
                "action": "assign_owner",
                "owner": "舒磊",
            },
            {
                "action": "add_member_if_missing",
                "member_name": "舒磊",
            },
            {
                "action": "upload_report_files",
                "word_files": upload_files.get("word", []),
            },
            {
                "action": "complete_inspection",
                "work_order_link": context.normalized_data.get("work_order_link"),
            },
            {
                "action": "archive_uploaded_reports",
                "archive_root": f"{self.report_root.rstrip('/')}/已上传的文档",
                "word_files": upload_files.get("word", []),
            },
        ]

    def _precheck_failed(
        self,
        error_message: str,
        context: ExecutorContext,
        *,
        actions: list[dict[str, Any]] | None = None,
        payload: dict[str, Any] | None = None,
        match_result=None,
        runner_diagnostics: dict[str, Any] | None = None,
    ) -> ExecutionResult:
        return ExecutionResult(
            run_status="precheck_failed",
            error_message=error_message,
            executor_version=self.executor_version,
            result_payload=self._build_payload(
                context,
                actions=actions,
                match_result=match_result,
                execution_mode="real_precheck",
                runner_diagnostics=runner_diagnostics or self._simulated_runner_diagnostics(reason="precheck_failed"),
                extra_payload=payload,
            ),
        )

    def _manual_required(
        self,
        context: ExecutorContext,
        match_result,
        *,
        actions: list[dict[str, Any]] | None = None,
    ) -> ExecutionResult:
        return ExecutionResult(
            run_status="manual_required",
            manual_required=True,
            error_message=match_result.error_message or "巡检报告未就绪，需要人工处理",
            executor_version=self.executor_version,
            result_payload=self._build_payload(
                context,
                actions=actions,
                match_result=match_result,
                execution_mode="manual_required",
                runner_diagnostics=self._simulated_runner_diagnostics(reason="manual_required"),
                extra_payload={"reason": match_result.error_message},
            ),
        )

    def _build_payload(
        self,
        context: ExecutorContext,
        *,
        actions: list[dict[str, Any]] | None = None,
        action_results: list[dict[str, Any]] | None = None,
        match_result=None,
        execution_mode: str,
        runner_diagnostics: dict[str, Any],
        precheck_summary: dict[str, Any] | None = None,
        extra_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        diagnostics = runner_diagnostics or {}
        postcheck = diagnostics.get("postcheck") or {}
        payload = {
            "execution_mode": execution_mode,
            "customer_name": context.normalized_data.get("customer_name"),
            "task_plan_id": context.task_plan_id,
            "action_trace": actions or [],
            "action_results": action_results or [],
            "precheck_summary": precheck_summary or {},
            "upload_candidates": (match_result.matched_files if match_result else {}),
            "report_match": (match_result.model_dump() if match_result else {}),
            "real_execution_enabled": self.settings.enable_real_execution,
            "inspection_real_execution_enabled": self.settings.inspection_real_execution_enabled,
            "runner_diagnostics": diagnostics,
            "postcheck_passed": postcheck.get("postcheck_passed", False),
            "closure_confirmed": postcheck.get("closure_confirmed", False),
            "report_attached_confirmed": postcheck.get("report_attached_confirmed", False),
            "postcheck_stage_after": postcheck.get("postcheck_stage_after"),
            "postcheck_uploaded_file_ids_expected": postcheck.get("postcheck_uploaded_file_ids_expected", []),
            "postcheck_uploaded_file_ids_found": postcheck.get("postcheck_uploaded_file_ids_found", []),
            "postcheck_uploaded_filenames_expected": postcheck.get("postcheck_uploaded_filenames_expected", []),
            "postcheck_uploaded_filenames_found": postcheck.get("postcheck_uploaded_filenames_found", []),
        }
        if extra_payload:
            payload.update(extra_payload)
        return payload

    def _simulated_runner_diagnostics(self, *, reason: str) -> dict[str, Any]:
        return build_simulated_runner_diagnostics(
            module_code=self.module_code,
            runner="InspectionSimulatedRunner",
            reason=reason,
            real_execution_enabled=self.settings.enable_real_execution,
            inspection_real_execution_enabled=self.settings.inspection_real_execution_enabled,
        )
