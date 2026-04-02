from __future__ import annotations

from typing import Any

from core.config import Settings, get_settings
from services.executors.proactive_real_runner import ProactiveRealRunner
from services.executors.runner_contract import build_simulated_runner_diagnostics
from services.executors.schemas import ExecutionResult, ExecutorContext


class ProactiveExecutor:
    module_code = "proactive"
    task_type = "proactive_visit_close"
    executor_version = "phase9-proactive-real-v1"

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        real_runner: ProactiveRealRunner | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.real_runner = real_runner or ProactiveRealRunner(self.settings)

    def precheck(self, context: ExecutorContext) -> ExecutionResult:
        mismatch = self._validate_context(context)
        if mismatch is not None:
            return mismatch

        data = context.normalized_data
        missing_fields = self._missing_required_fields(data)
        if missing_fields:
            return self._precheck_failed(
                "关键字段缺失，无法执行",
                context,
                payload={"missing_fields": missing_fields},
            )

        if data.get("liaison_status") != "已建联":
            return self._precheck_failed("liaison_status 不是已建联，禁止执行", context)
        if data.get("visit_link"):
            return self._precheck_failed("visit_link 已存在，禁止重复执行", context)

        actions = self._build_actions(context)
        manual_reason = self._manual_reason(data)
        if manual_reason:
            return self._manual_required(context, actions, manual_reason)

        if self._should_use_real_execution():
            valid, diagnostics, error_message = self.real_runner.validate()
            if not valid:
                return self._precheck_failed(
                    error_message or "proactive 真实执行配置缺失",
                    context,
                    actions=actions,
                    runner_diagnostics=diagnostics,
                )
            return ExecutionResult(
                run_status="precheck_passed",
                executor_version=self.executor_version,
                result_payload=self._build_payload(
                    context,
                    actions=actions,
                    execution_mode="real_ready",
                    runner_diagnostics=diagnostics,
                    precheck_summary={"real_execution_ready": True},
                ),
            )

        return ExecutionResult(
            run_status="precheck_passed",
            executor_version=self.executor_version,
            result_payload=self._build_payload(
                context,
                actions=actions,
                execution_mode="simulated",
                runner_diagnostics=self._simulated_runner_diagnostics(reason="real_execution_disabled"),
                precheck_summary={"real_execution_ready": False},
            ),
        )

    async def dry_run(self, context: ExecutorContext) -> ExecutionResult:
        actions = self._build_actions(context)
        manual_reason = self._manual_reason(context.normalized_data)
        if manual_reason:
            return self._manual_required(context, actions, manual_reason)

        return ExecutionResult(
            run_status="dry_run_ready",
            executor_version=self.executor_version,
            result_payload=self._build_payload(
                context,
                actions=actions,
                execution_mode="dry_run",
                runner_diagnostics=self._simulated_runner_diagnostics(reason="dry_run"),
            ),
        )

    async def execute(self, context: ExecutorContext) -> ExecutionResult:
        actions = self._build_actions(context)
        manual_reason = self._manual_reason(context.normalized_data)
        if manual_reason:
            return self._manual_required(context, actions, manual_reason)

        if not self._should_use_real_execution():
            final_link = f"https://pts.example.com/simulated/proactive/{context.task_plan_id}"
            return ExecutionResult(
                run_status="simulated_success",
                executor_version=self.executor_version,
                final_link=final_link,
                result_payload=self._build_payload(
                    context,
                    actions=actions,
                    execution_mode="simulated",
                    runner_diagnostics=self._simulated_runner_diagnostics(reason="real_execution_disabled"),
                ),
            )

        valid, diagnostics, error_message = self.real_runner.validate()
        if not valid:
            return self._precheck_failed(
                error_message or "proactive 真实执行配置缺失",
                context,
                actions=actions,
                runner_diagnostics=diagnostics,
            )

        outcome = await self.real_runner.run(context, actions)
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
            "real_execution_enabled": self.settings.enable_real_execution,
            "proactive_real_execution_enabled": self.settings.proactive_real_execution_enabled,
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
            "liaison_status": data.get("liaison_status"),
            "feedback_note": data.get("feedback_note"),
        }
        return [key for key, value in required_fields.items() if not value]

    def _manual_reason(self, data: dict[str, Any]) -> str | None:
        if not data.get("contact_name") and not data.get("contact_phone"):
            return "缺少联系人信息，需要人工处理"
        return None

    def _should_use_real_execution(self) -> bool:
        return self.settings.enable_real_execution and self.settings.proactive_real_execution_enabled

    def _build_actions(self, context: ExecutorContext) -> list[dict[str, Any]]:
        return [
            {
                "action": "create_proactive_work_order",
                "work_order_type": "customer_satisfaction_survey",
            },
            {
                "action": "assign_owner",
                "owner": "舒磊",
            },
            {
                "action": "fill_feedback",
                "feedback_note": context.normalized_data.get("feedback_note"),
            },
        ]

    def _precheck_failed(
        self,
        error_message: str,
        context: ExecutorContext,
        *,
        actions: list[dict[str, Any]] | None = None,
        payload: dict[str, Any] | None = None,
        runner_diagnostics: dict[str, Any] | None = None,
    ) -> ExecutionResult:
        return ExecutionResult(
            run_status="precheck_failed",
            error_message=error_message,
            executor_version=self.executor_version,
            result_payload=self._build_payload(
                context,
                actions=actions,
                execution_mode="real_precheck" if self._should_use_real_execution() else "simulated",
                runner_diagnostics=runner_diagnostics or self._simulated_runner_diagnostics(reason="precheck_failed"),
                extra_payload=payload,
            ),
        )

    def _manual_required(
        self,
        context: ExecutorContext,
        actions: list[dict[str, Any]],
        manual_reason: str,
    ) -> ExecutionResult:
        return ExecutionResult(
            run_status="manual_required",
            manual_required=True,
            error_message=manual_reason,
            executor_version=self.executor_version,
            result_payload=self._build_payload(
                context,
                actions=actions,
                execution_mode="manual_required",
                runner_diagnostics=self._simulated_runner_diagnostics(reason="manual_required"),
                extra_payload={"reason": manual_reason},
            ),
        )

    def _build_payload(
        self,
        context: ExecutorContext,
        *,
        actions: list[dict[str, Any]] | None = None,
        action_results: list[dict[str, Any]] | None = None,
        execution_mode: str,
        runner_diagnostics: dict[str, Any],
        precheck_summary: dict[str, Any] | None = None,
        extra_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "execution_mode": execution_mode,
            "customer_name": context.normalized_data.get("customer_name"),
            "task_plan_id": context.task_plan_id,
            "action_trace": actions or [],
            "action_results": action_results or [],
            "precheck_summary": precheck_summary or {},
            "real_execution_enabled": self.settings.enable_real_execution,
            "proactive_real_execution_enabled": self.settings.proactive_real_execution_enabled,
            "runner_diagnostics": runner_diagnostics,
        }
        if extra_payload:
            payload.update(extra_payload)
        return payload

    def _simulated_runner_diagnostics(self, *, reason: str) -> dict[str, Any]:
        return build_simulated_runner_diagnostics(
            module_code=self.module_code,
            runner="ProactiveSimulatedRunner",
            reason=reason,
            real_execution_enabled=self.settings.enable_real_execution,
            proactive_real_execution_enabled=self.settings.proactive_real_execution_enabled,
        )
