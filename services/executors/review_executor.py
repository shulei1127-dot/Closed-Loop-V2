from __future__ import annotations

import logging
from typing import Any

from core.config import Settings, get_settings
from services.executors.runner_contract import build_simulated_runner_diagnostics
from services.executors.schemas import ExecutionResult, ExecutorContext
from services.pts_session_service import PtsSessionService

logger = logging.getLogger(__name__)


class ReviewExecutor:
    """Executor for review module — review_audit task type.

    Pipeline: fetch full project details from PTS -> run audit engine
    (services.audit.engine.run_audit) -> optionally write result to DingTalk
    (services.integration.review_dingtalk).
    """

    module_code = "review"
    task_type = "review_audit"
    executor_version = "v1-review-audit"

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        pts_session_service: PtsSessionService | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.pts_session_service = pts_session_service or PtsSessionService()

    def precheck(self, context: ExecutorContext) -> ExecutionResult:
        """Validate project_id exists and PTS session is available."""
        mismatch = self._validate_context(context)
        if mismatch is not None:
            return mismatch

        data = context.normalized_data
        project_id = str(data.get("project_id") or "").strip()
        if not project_id:
            return self._precheck_failed(
                "project_id 缺失，无法执行审核",
                context,
                payload={"missing_fields": ["project_id"]},
            )

        # Check PTS session availability
        pts_status = self.pts_session_service.get_status()
        if not pts_status.get("configured"):
            return self._precheck_failed(
                "PTS 会话未配置，无法获取项目详情",
                context,
                payload={"pts_status": pts_status},
            )

        actions = self._build_actions(context)

        if self._should_use_real_execution():
            return ExecutionResult(
                run_status="precheck_passed",
                executor_version=self.executor_version,
                result_payload=self._build_payload(
                    context,
                    actions=actions,
                    execution_mode="real_ready",
                    runner_diagnostics=self._real_runner_diagnostics(),
                    precheck_summary={"real_execution_ready": True, "pts_status": pts_status},
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
                precheck_summary={"real_execution_ready": False, "pts_status": pts_status},
            ),
        )

    async def dry_run(self, context: ExecutorContext) -> ExecutionResult:
        """Return dry_run_ready with planned audit actions."""
        actions = self._build_actions(context)
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
        """Fetch full project details from PTS, run audit engine, optionally write to DingTalk."""
        actions = self._build_actions(context)
        data = context.normalized_data
        project_id = str(data.get("project_id") or "").strip()

        if not project_id:
            return self._precheck_failed(
                "project_id 缺失，无法执行审核",
                context,
                actions=actions,
                payload={"missing_fields": ["project_id"]},
            )

        if not self._should_use_real_execution():
            return ExecutionResult(
                run_status="manual_required",
                manual_required=True,
                error_message="review 真实执行未启用，需要手动审核",
                executor_version=self.executor_version,
                result_payload=self._build_payload(
                    context,
                    actions=actions,
                    execution_mode="manual_required",
                    runner_diagnostics=self._simulated_runner_diagnostics(reason="real_execution_disabled"),
                    extra_payload={
                        "reason": "review_real_execution_disabled",
                        "manual_required_reason": "review 真实执行链未启用，需要手动完成审核闭环",
                    },
                ),
            )

        # Step 1: Build PTS client and fetch full project details
        try:
            from services.pts_graphql_client import PtsGraphQLClient
            from services.extractors.review_main_page import extract_project_data
            from services.extractors.review_product_detail import extract_product_detail
            from services.extractors.review_approval_time import extract_approval_time
            from services.audit.schemas import AuditInput

            client = PtsGraphQLClient(
                api_base_url=self.settings.pts_api_base_url,
                api_token=self.settings.pts_review_api_token or self.settings.pts_api_token,
            )

            project_data = await extract_project_data(client, project_id)

            # Fetch product details
            product_details = []
            for product in project_data.products:
                detail = await extract_product_detail(client, product.product_id)
                if detail:
                    product_details.append(detail)

            # Fetch approval time
            approval_time = await extract_approval_time(client, project_id)

            # Build audit input
            audit_input = AuditInput(
                project_id=project_data.project_id,
                project_name=project_data.project_name,
                customer_name=project_data.customer_name,
                delivery_stage=project_data.delivery_stage,
                stage_status=project_data.stage_status,
                after_sales_leader=project_data.after_sales_leader,
                assigner_username=project_data.assigner_username,
                assigner_name=project_data.assigner_name,
                person_in_charge_username=project_data.person_in_charge_username,
                person_in_charge_name=project_data.person_in_charge_name,
                delivery_items=project_data.delivery_items,
                contacts=project_data.contacts,
                products=project_data.products,
                product_details=product_details,
                approval_time=approval_time,
                partner_delivery_type=project_data.partner_delivery_type,
            )
        except Exception as exc:
            logger.exception("ReviewExecutor: failed to fetch project details for %s", project_id)
            return ExecutionResult(
                run_status="failed",
                error_message=f"获取项目详情失败: {exc}",
                retryable=True,
                executor_version=self.executor_version,
                result_payload=self._build_payload(
                    context,
                    actions=actions,
                    execution_mode="real_attempted",
                    runner_diagnostics=self._real_runner_diagnostics(),
                    extra_payload={"fetch_error": str(exc)},
                ),
            )

        # Step 2: Run audit engine (synchronous)
        try:
            from services.audit.engine import run_audit

            audit_result = run_audit(audit_input)
        except Exception as exc:
            logger.exception("ReviewExecutor: audit engine failed for %s", project_id)
            return ExecutionResult(
                run_status="failed",
                error_message=f"审核引擎执行失败: {exc}",
                retryable=True,
                executor_version=self.executor_version,
                result_payload=self._build_payload(
                    context,
                    actions=actions,
                    execution_mode="real_attempted",
                    runner_diagnostics=self._real_runner_diagnostics(),
                    extra_payload={"audit_error": str(exc)},
                ),
            )

        # Compute region / delivery type / project type
        try:
            from services.integration.review_dingtalk import (
                compute_region, compute_delivery_type, compute_project_type,
            )
            audit_result.region = compute_region(audit_result.assigner_name)
            audit_result.delivery_type = compute_delivery_type(
                audit_input.delivery_items, product_details, audit_input.partner_delivery_type,
            )
            audit_result.project_type = compute_project_type(audit_input.delivery_items)
        except Exception as exc:
            logger.warning("ReviewExecutor: region/type computation failed for %s: %s", project_id, exc)

        # Step 3: Optionally write result to DingTalk
        dingtalk_result: dict[str, Any] | None = None
        if self.settings.review_writeback_enabled:
            try:
                from services.integration.review_dingtalk import write_audit_to_dingtalk

                dingtalk_result = await write_audit_to_dingtalk(audit_result)
            except Exception as exc:
                logger.warning("ReviewExecutor: DingTalk writeback failed for %s: %s", project_id, exc)
                dingtalk_result = {"enabled": True, "error": str(exc)}
        else:
            dingtalk_result = {"enabled": False}

        # Build final result
        from schemas.review import ReviewAuditResultPayload

        audit_passed = audit_result.conclusion == "通过"
        result_payload = ReviewAuditResultPayload(
            project_id=project_id,
            audit_passed=audit_passed,
            audit_details=audit_result.model_dump(exclude={"dingtalk"}),
            audit_errors=[r.message for r in audit_result.rules if r.result in ("不通过", "无法判定")],
            dingtalk_writeback=dingtalk_result,
        )

        run_status = "success" if audit_passed else "failed"
        extra_payload = {
            "audit_result": result_payload.model_dump(),
            "dingtalk_writeback": dingtalk_result,
        }

        return ExecutionResult(
            run_status=run_status,
            executor_version=self.executor_version,
            result_payload=self._build_payload(
                context,
                actions=actions,
                execution_mode="real",
                runner_diagnostics=self._real_runner_diagnostics(),
                extra_payload=extra_payload,
            ),
        )

    def healthcheck(self) -> dict[str, Any]:
        """Report executor readiness including PTS session and real execution status."""
        pts_status = self.pts_session_service.get_status()
        return {
            "ok": True,
            "module_code": self.module_code,
            "task_type": self.task_type,
            "executor_version": self.executor_version,
            "real_execution_enabled": self.settings.enable_real_execution,
            "review_real_execution_enabled": self._should_use_real_execution(),
            "pts_configured": pts_status.get("configured", False),
            "pts_auth_source": pts_status.get("source", "unconfigured"),
            "writeback_enabled": self.settings.review_writeback_enabled,
        }

    # --- Internal helpers ---------------------------------------------------

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

    def _should_use_real_execution(self) -> bool:
        return self.settings.enable_real_execution and self.settings.review_real_execution_enabled

    def _build_actions(self, context: ExecutorContext) -> list[dict[str, Any]]:
        return [
            {
                "action": "fetch_project_details",
                "project_id": context.normalized_data.get("project_id"),
            },
            {
                "action": "run_audit",
                "project_id": context.normalized_data.get("project_id"),
            },
            {
                "action": "writeback_dingtalk",
                "project_id": context.normalized_data.get("project_id"),
                "customer_name": context.normalized_data.get("customer_name"),
            },
        ]

    def _precheck_failed(
        self,
        error_message: str,
        context: ExecutorContext,
        *,
        actions: list[dict[str, Any]] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> ExecutionResult:
        return ExecutionResult(
            run_status="precheck_failed",
            error_message=error_message,
            executor_version=self.executor_version,
            result_payload=self._build_payload(
                context,
                actions=actions,
                execution_mode="real_precheck" if self._should_use_real_execution() else "simulated",
                runner_diagnostics=self._simulated_runner_diagnostics(reason="precheck_failed"),
                extra_payload=payload,
            ),
        )

    def _build_payload(
        self,
        context: ExecutorContext,
        *,
        actions: list[dict[str, Any]] | None = None,
        execution_mode: str,
        runner_diagnostics: dict[str, Any],
        precheck_summary: dict[str, Any] | None = None,
        extra_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "execution_mode": execution_mode,
            "project_id": context.normalized_data.get("project_id"),
            "customer_name": context.normalized_data.get("customer_name"),
            "task_plan_id": context.task_plan_id,
            "action_trace": actions or [],
            "precheck_summary": precheck_summary or {},
            "real_execution_enabled": self.settings.enable_real_execution,
            "review_real_execution_enabled": self._should_use_real_execution(),
            "runner_diagnostics": runner_diagnostics,
        }
        if extra_payload:
            payload.update(extra_payload)
        return payload

    def _simulated_runner_diagnostics(self, *, reason: str) -> dict[str, Any]:
        return build_simulated_runner_diagnostics(
            module_code=self.module_code,
            runner="ReviewSimulatedRunner",
            reason=reason,
            real_execution_enabled=self.settings.enable_real_execution,
        )

    def _real_runner_diagnostics(self) -> dict[str, Any]:
        pts_status = self.pts_session_service.get_status()
        return {
            "mode": "real",
            "runner": "ReviewRealRunner",
            "pts_configured": pts_status.get("configured", False),
            "pts_auth_source": pts_status.get("source", "unconfigured"),
        }