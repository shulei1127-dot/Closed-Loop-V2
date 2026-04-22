from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any

import httpx

from core.config import Settings, get_settings
from services.executors.runner_contract import build_simulated_runner_diagnostics
from services.executors.schemas import ExecutionResult, ExecutorContext
from services.executors.visit_actions import VisitActionBuilder
from services.executors.visit_real_runner import VisitRealRunner, _PtsBrowserSession, _PtsRunnerError, _read_path


DELIVERY_ID_PATTERNS = [
    re.compile(r"交付\s*ID[:：]?\s*([0-9a-f]{24})", re.IGNORECASE),
    re.compile(r'"deliveryId"\s*:\s*"([0-9a-f]{24})"', re.IGNORECASE),
    re.compile(r'"delivery_id"\s*:\s*"([0-9a-f]{24})"', re.IGNORECASE),
]
PRODUCT_ID_PATTERN = re.compile(r"/project/product/([0-9a-f]{24})", re.IGNORECASE)
PROJECT_DELIVERY_ID_PATTERN = re.compile(r"/project/(?!product/)([0-9a-f]{24})(?:[/?#]|$)", re.IGNORECASE)


@dataclass(slots=True)
class _ResolvedDeliveryContext:
    delivery_id: str | None
    source: str
    raw: str | None = None
    product_id_hint: str | None = None
    product_name_hint: str | None = None


class ProactiveExecutor:
    module_code = "proactive"
    task_type = "proactive_visit_close"
    executor_version = "phase10-proactive-pts-bridge-v1"

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        visit_action_builder: VisitActionBuilder | None = None,
        real_runner: VisitRealRunner | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.visit_action_builder = visit_action_builder or VisitActionBuilder()
        self.real_runner = real_runner or VisitRealRunner(self.settings)

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
                    error_message or "proactive PTS 真实执行配置缺失",
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
            return ExecutionResult(
                run_status="manual_required",
                manual_required=True,
                error_message="proactive 尚未接入 PTS 真实执行链，已禁止模拟执行冒充闭环",
                executor_version=self.executor_version,
                result_payload=self._build_payload(
                    context,
                    actions=actions,
                    execution_mode="manual_required",
                    runner_diagnostics=self._simulated_runner_diagnostics(reason="real_execution_disabled"),
                    extra_payload={
                        "reason": "proactive_real_execution_disabled",
                        "manual_required_reason": "proactive 尚未接入 PTS 真实执行链，无法进行真实闭环",
                    },
                ),
            )

        valid, diagnostics, error_message = self.real_runner.validate()
        if not valid:
            return self._precheck_failed(
                error_message or "proactive PTS 真实执行配置缺失",
                context,
                actions=actions,
                runner_diagnostics=diagnostics,
            )

        bridge = await self._build_visit_bridge_context(context)
        bridge_result = bridge.pop("__bridge_error_result__", None) if isinstance(bridge, dict) else None
        if isinstance(bridge_result, ExecutionResult):
            return bridge_result

        visit_context = ExecutorContext(
            task_plan_id=context.task_plan_id,
            module_code="visit",
            task_type="visit_close",
            plan_status=context.plan_status,
            normalized_record_id=context.normalized_record_id,
            source_row_id=context.source_row_id,
            recognition_status=context.recognition_status,
            planned_payload={**context.planned_payload, **bridge},
            normalized_data={**context.normalized_data, **bridge},
            source_url=context.source_url,
            source_doc_key=context.source_doc_key,
            source_view_key=context.source_view_key,
            source_collector_type=context.source_collector_type,
            source_extra_config=context.source_extra_config,
        )
        visit_actions, manual_reason = self.visit_action_builder.build(visit_context)
        if manual_reason:
            return self._manual_required(context, visit_actions, manual_reason)

        outcome = await self.real_runner.run(visit_context, visit_actions)
        execution_mode = "real" if outcome.run_status == "success" else "real_attempted"
        postcheck_payload = self._extract_postcheck_payload(outcome.runner_diagnostics)
        if outcome.final_link:
            postcheck_payload["final_link"] = outcome.final_link
        extra_payload = {
            **postcheck_payload,
            "resolved_delivery_id": bridge.get("delivery_id"),
            "resolved_pts_link": bridge.get("pts_link"),
            "delivery_resolution_source": bridge.get("delivery_resolution_source"),
            "product_id_hint": bridge.get("product_id_hint"),
            "bridge_runner": "visit_real_runner",
        }
        if outcome.run_status == "success":
            return ExecutionResult(
                run_status="success",
                executor_version=self.executor_version,
                final_link=outcome.final_link,
                result_payload=self._build_payload(
                    context,
                    actions=visit_actions,
                    action_results=outcome.action_results,
                    execution_mode=execution_mode,
                    runner_diagnostics=outcome.runner_diagnostics,
                    extra_payload=extra_payload,
                ),
            )

        if outcome.run_status == "pending_confirmation":
            return ExecutionResult(
                run_status="pending_confirmation",
                executor_version=self.executor_version,
                final_link=outcome.final_link,
                error_message=outcome.error_message,
                retryable=True,
                result_payload=self._build_payload(
                    context,
                    actions=visit_actions,
                    action_results=outcome.action_results,
                    execution_mode=execution_mode,
                    runner_diagnostics=outcome.runner_diagnostics,
                    extra_payload=extra_payload,
                ),
            )

        return ExecutionResult(
            run_status="failed",
            executor_version=self.executor_version,
            final_link=outcome.final_link,
            error_message=outcome.error_message,
            retryable=outcome.retryable,
            result_payload=self._build_payload(
                context,
                actions=visit_actions,
                action_results=outcome.action_results,
                execution_mode=execution_mode,
                runner_diagnostics=outcome.runner_diagnostics,
                extra_payload=extra_payload,
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
            "proactive_real_execution_enabled": self._should_use_real_execution(),
            "visit_real_execution_enabled": self.settings.visit_real_execution_enabled,
            "real_runner_ready": valid,
            "real_runner_error": error_message,
            "runner_diagnostics": diagnostics,
            "bridge_runner": "visit_real_runner",
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
            "visit_owner": data.get("visit_owner"),
            "feedback_note": data.get("feedback_note"),
        }
        missing_fields = [key for key, value in required_fields.items() if not value]
        if not str(data.get("product_link") or "").strip() and not str(data.get("product_info_id") or "").strip():
            missing_fields.append("product_link")
        return missing_fields

    def _manual_reason(self, data: dict[str, Any]) -> str | None:
        return None

    def _should_use_real_execution(self) -> bool:
        return self.settings.enable_real_execution and self.settings.visit_real_execution_enabled

    def _build_actions(self, context: ExecutorContext) -> list[dict[str, Any]]:
        return [
            {
                "action": "create_proactive_work_order",
                "work_order_type": "customer_satisfaction",
            },
            {
                "action": "assign_owner",
                "owner": context.normalized_data.get("visit_owner"),
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
            "proactive_real_execution_enabled": self._should_use_real_execution(),
            "visit_real_execution_enabled": self.settings.visit_real_execution_enabled,
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
            proactive_real_execution_enabled=self._should_use_real_execution(),
            visit_real_execution_enabled=self.settings.visit_real_execution_enabled,
        )

    async def _build_visit_bridge_context(self, context: ExecutorContext) -> dict[str, Any]:
        data = context.normalized_data
        delivery_id = str(data.get("delivery_id") or "").strip()
        product_link = str(data.get("product_link") or "").strip()
        if not delivery_id:
            if not product_link:
                return {
                    "__bridge_error_result__": self._precheck_failed(
                        "缺少 product_link / delivery_id，无法解析 proactive 对应交付信息",
                        context,
                        payload={"missing_fields": ["product_link", "delivery_id"]},
                        runner_diagnostics=self._simulated_runner_diagnostics(reason="bridge_context_missing"),
                    )
                }
            resolved = await self._resolve_delivery_id_from_product_link(product_link)
            if not resolved.delivery_id:
                error_type = "session_expired" if resolved.source == "session_expired" else "response_invalid"
                diagnostics = self._simulated_runner_diagnostics(reason="delivery_resolution_failed")
                diagnostics["mode"] = "real"
                diagnostics["error_type"] = error_type
                diagnostics["last_error"] = (
                    "PTS 会话已失效，无法从产品页解析交付 ID"
                    if resolved.source == "session_expired"
                    else "无法从产品页解析交付 ID"
                )
                diagnostics["failed_action"] = "resolve_delivery_context"
                diagnostics["delivery_resolution"] = {
                    "source": resolved.source,
                    "raw": resolved.raw,
                    "product_link": product_link,
                }
                return {
                    "__bridge_error_result__": ExecutionResult(
                        run_status="failed",
                        error_message=diagnostics["last_error"],
                        retryable=resolved.source not in {"session_expired", "missing_product_link"},
                        executor_version=self.executor_version,
                        result_payload=self._build_payload(
                            context,
                            execution_mode="real_attempted",
                            runner_diagnostics=diagnostics,
                            extra_payload={"delivery_resolution": diagnostics["delivery_resolution"]},
                        ),
                    )
                }
            delivery_id = resolved.delivery_id
            resolve_source = resolved.source
            resolve_raw = resolved.raw
        else:
            resolve_source = "raw_field"
            resolve_raw = delivery_id
            resolved = _ResolvedDeliveryContext(delivery_id=delivery_id, source=resolve_source, raw=resolve_raw)

        pts_link = f"{self.settings.pts_base_url.rstrip('/')}/project/{delivery_id}#base"
        product_id_hint = (
            resolved.product_id_hint
            or self._extract_product_info_id(product_link)
            or str(data.get("product_info_id") or "").strip()
        )
        return {
            "delivery_id": delivery_id,
            "pts_link": pts_link,
            "visit_type": "客户满意度调研",
            "visit_status": "已回访",
            "satisfaction": str(data.get("satisfaction") or "满意").strip() or "满意",
            "feedback_note": str(data.get("feedback_note") or "").strip(),
            "visit_owner": str(data.get("visit_owner") or "").strip(),
            "product_id_hint": product_id_hint or None,
            "product_name_hint": resolved.product_name_hint or None,
            "delivery_resolution_source": resolve_source,
            "delivery_resolution_raw": resolve_raw,
        }

    async def _resolve_delivery_id_from_product_link(self, product_link: str) -> _ResolvedDeliveryContext:
        direct_delivery_id = self._extract_delivery_id_from_project_url(product_link)
        if direct_delivery_id:
            return _ResolvedDeliveryContext(
                delivery_id=direct_delivery_id,
                source="delivery_url",
                raw=product_link,
            )

        product_info_id = self._extract_product_info_id(product_link)
        if product_info_id:
            resolved = await self._resolve_delivery_context_from_product_info(product_info_id)
            if resolved.delivery_id:
                return resolved

        headers = {
            "Cookie": self.settings.pts_cookie_header,
            "User-Agent": "Mozilla/5.0",
            "Accept": "text/html,application/xhtml+xml,application/json,*/*",
        }
        try:
            async with httpx.AsyncClient(
                timeout=self.settings.visit_real_timeout_seconds,
                verify=self.settings.pts_verify_ssl,
                follow_redirects=True,
            ) as client:
                response = await client.get(product_link, headers=headers)
        except httpx.HTTPError as exc:
            return _ResolvedDeliveryContext(delivery_id=None, source="http_error", raw=str(exc))

        final_url = str(response.url)
        final_url_delivery_id = self._extract_delivery_id_from_project_url(final_url)
        if final_url_delivery_id:
            return _ResolvedDeliveryContext(
                delivery_id=final_url_delivery_id,
                source="product_page_redirect",
                raw=final_url,
            )
        if response.status_code in {401, 403} or "auth.chaitin.net/login" in final_url:
            return await self._resolve_delivery_id_from_browser_session(product_link, fallback_source="session_expired")
        if response.status_code >= 400:
            return _ResolvedDeliveryContext(delivery_id=None, source="http_status", raw=f"status={response.status_code}")

        body = response.text
        for pattern in DELIVERY_ID_PATTERNS:
            match = pattern.search(body)
            if match is not None:
                return _ResolvedDeliveryContext(
                    delivery_id=match.group(1),
                    source="product_page",
                    raw=match.group(0),
                )
        return await self._resolve_delivery_id_from_browser_session(product_link, fallback_source="not_found", raw_hint=body[:400])

    async def _resolve_delivery_id_from_browser_session(
        self,
        product_link: str,
        *,
        fallback_source: str,
        raw_hint: str | None = None,
    ) -> _ResolvedDeliveryContext:
        script = """
        (() => {
          const text = document.body ? document.body.innerText : '';
          const html = document.documentElement ? document.documentElement.outerHTML : '';
          return JSON.stringify({
            url: location.href,
            title: document.title || '',
            text: text.slice(0, 12000),
            html: html.slice(0, 12000)
          });
        })()
        """
        try:
            async with _PtsBrowserSession(self.settings) as browser:
                raw = await browser.execute_js_on_project_background(product_link, script)
        except Exception as exc:
            return _ResolvedDeliveryContext(delivery_id=None, source=fallback_source, raw=raw_hint or str(exc))

        if not isinstance(raw, dict):
            return _ResolvedDeliveryContext(delivery_id=None, source=fallback_source, raw=raw_hint or str(raw))

        combined = "\n".join(
            [
                str(raw.get("title") or ""),
                str(raw.get("text") or ""),
                str(raw.get("html") or ""),
            ]
        )
        for pattern in DELIVERY_ID_PATTERNS:
            match = pattern.search(combined)
            if match is not None:
                return _ResolvedDeliveryContext(
                    delivery_id=match.group(1),
                    source="browser_session_product_page",
                    raw=match.group(0),
                )
        return _ResolvedDeliveryContext(delivery_id=None, source=fallback_source, raw=raw_hint or combined[:400])

    async def _resolve_delivery_context_from_product_info(self, product_info_id: str) -> _ResolvedDeliveryContext:
        payload = {
            "operationName": "ProductInfoByID",
            "variables": {"id": product_info_id},
            "query": _build_product_info_by_id_query(),
        }
        last_error: str | None = None
        try:
            data = await self._query_pts_graphql_payload(payload)
            resolved = self._extract_delivery_context_from_product_info_payload(
                data,
                product_info_id=product_info_id,
                source="product_info_graphql",
            )
            if resolved.delivery_id:
                return resolved
            last_error = resolved.raw
        except _PtsRunnerError as exc:
            last_error = exc.error_message
            if exc.error_type not in {"session_expired", "http_error", "response_invalid"}:
                return _ResolvedDeliveryContext(delivery_id=None, source=exc.error_type, raw=exc.error_message)

        try:
            data = await self._query_pts_graphql_payload_via_browser(payload)
            resolved = self._extract_delivery_context_from_product_info_payload(
                data,
                product_info_id=product_info_id,
                source="product_info_browser_graphql",
            )
            if resolved.delivery_id:
                return resolved
            return resolved
        except _PtsRunnerError as exc:
            return _ResolvedDeliveryContext(delivery_id=None, source=exc.error_type, raw=last_error or exc.error_message)
        except Exception as exc:
            return _ResolvedDeliveryContext(delivery_id=None, source="browser_graphql_failed", raw=last_error or str(exc))

    async def _query_pts_graphql_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.settings.pts_cookie_header:
            raise _PtsRunnerError(
                error_message="缺少 PTS Cookie，无法直接查询产品信息",
                error_type="session_expired",
                retryable=False,
            )
        try:
            async with httpx.AsyncClient(
                base_url=self.settings.pts_base_url,
                timeout=self.settings.visit_real_timeout_seconds,
                verify=self.settings.pts_verify_ssl,
                follow_redirects=True,
                headers={
                    "Cookie": self.settings.pts_cookie_header,
                    "Content-Type": "application/json",
                    "Accept": "*/*",
                    "Origin": self.settings.pts_base_url,
                    "Referer": f"{self.settings.pts_base_url.rstrip('/')}/",
                    "User-Agent": "Mozilla/5.0",
                },
            ) as client:
                response = await client.post("/query", json=payload)
        except httpx.TimeoutException as exc:
            raise _PtsRunnerError(
                error_message="PTS 产品信息查询超时",
                error_type="timeout",
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise _PtsRunnerError(
                error_message=f"PTS 产品信息查询失败: {exc}",
                error_type="http_error",
                retryable=True,
            ) from exc

        if _is_pts_auth_response(response):
            raise _PtsRunnerError(
                error_message="PTS 会话已失效，请重新登录 PTS 或更新 Cookie",
                error_type="session_expired",
                retryable=False,
                http_status=response.status_code,
            )
        if response.status_code >= 400:
            raise _PtsRunnerError(
                error_message=f"PTS 产品信息查询失败: {response.status_code}",
                error_type="http_error" if response.status_code >= 500 else "business_rejected",
                retryable=response.status_code >= 500,
                http_status=response.status_code,
            )

        try:
            response_payload = response.json()
        except ValueError as exc:
            raise _PtsRunnerError(
                error_message="PTS 产品信息查询返回非法 JSON",
                error_type="response_invalid",
                retryable=False,
            ) from exc
        return _extract_graphql_data(response_payload)

    async def _query_pts_graphql_payload_via_browser(self, payload: dict[str, Any]) -> dict[str, Any]:
        anchor_url = f"{self.settings.pts_base_url.rstrip('/')}/project"
        async with _PtsBrowserSession(self.settings) as browser:
            await browser.execute_js_on_project_background(anchor_url, "JSON.stringify({ready:true,url:location.href})")
            return await browser.graphql_payload(payload)

    @staticmethod
    def _extract_delivery_context_from_product_info_payload(
        data: dict[str, Any],
        *,
        product_info_id: str,
        source: str,
    ) -> _ResolvedDeliveryContext:
        product_info = data.get("productInfoByID") or {}
        if not isinstance(product_info, dict):
            return _ResolvedDeliveryContext(
                delivery_id=None,
                source=source,
                raw=f"productInfoByID missing for {product_info_id}",
            )

        delivery_id = str(_read_path(product_info, "delivery.id") or "").strip()
        if not delivery_id:
            delivery_list = product_info.get("delivery_list") or []
            if isinstance(delivery_list, list):
                for item in delivery_list:
                    if not isinstance(item, dict):
                        continue
                    delivery_id = str(_read_path(item, "delivery.id") or "").strip()
                    if delivery_id:
                        break

        product_detail = product_info.get("product_detail")
        if not isinstance(product_detail, dict):
            product_detail = _read_path(product_info, "product_delivery_support.product_detail") or {}
        product_id_hint = str(_read_path(product_detail, "product.id") or "").strip() or None
        product_name_hint = str(_read_path(product_detail, "product.name") or "").strip() or None

        return _ResolvedDeliveryContext(
            delivery_id=delivery_id or None,
            source=source,
            raw=json.dumps(
                {
                    "product_info_id": product_info_id,
                    "delivery_id": delivery_id or None,
                    "product_id_hint": product_id_hint,
                    "product_name_hint": product_name_hint,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            product_id_hint=product_id_hint,
            product_name_hint=product_name_hint,
        )

    @staticmethod
    def _extract_product_info_id(product_link: str) -> str | None:
        match = PRODUCT_ID_PATTERN.search(str(product_link or "").strip())
        if match is None:
            return None
        return match.group(1)

    @staticmethod
    def _extract_delivery_id_from_project_url(value: str) -> str | None:
        match = PROJECT_DELIVERY_ID_PATTERN.search(str(value or "").strip())
        if match is None:
            return None
        return match.group(1)

    @staticmethod
    def _extract_postcheck_payload(runner_diagnostics: dict[str, Any] | None) -> dict[str, Any]:
        diagnostics = runner_diagnostics or {}
        postcheck = diagnostics.get("postcheck") or {}
        if not isinstance(postcheck, dict):
            return {}
        return {
            "postcheck_passed": postcheck.get("postcheck_passed"),
            "closure_confirmed": postcheck.get("closure_confirmed"),
            "delivery_bound_confirmed": postcheck.get("delivery_bound_confirmed"),
            "feedback_confirmed": postcheck.get("feedback_confirmed"),
            "postcheck_finished": postcheck.get("postcheck_finished"),
            "postcheck_delivery_ids_found": postcheck.get("postcheck_delivery_ids_found") or [],
            "postcheck_feedback_present": postcheck.get("postcheck_feedback_present"),
            "postcheck_checked_at": postcheck.get("postcheck_checked_at"),
            "postcheck_error_type": postcheck.get("error_type"),
            "postcheck_error_message": postcheck.get("error_message"),
            "final_link": diagnostics.get("final_link"),
        }


def _build_product_info_by_id_query() -> str:
    return """
    query ProductInfoByID($id: ID!) {
      productInfoByID(id: $id) {
        id
        type
        product_detail {
          product {
            id
            name
            group
          }
          form {
            id
            name
          }
        }
        delivery {
          id
          project {
            id
            name
            company {
              id
              name
            }
          }
        }
        delivery_list {
          related_at
          delivery {
            id
            project {
              id
              name
            }
          }
        }
        product_delivery_support {
          id
          product_detail {
            product {
              id
              name
              group
            }
            form {
              id
              name
            }
          }
          project {
            id
            name
            company {
              id
              name
            }
          }
        }
      }
    }
    """


def _extract_graphql_data(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise _PtsRunnerError(
            error_message="PTS GraphQL 返回不是对象",
            error_type="response_invalid",
            retryable=False,
        )
    errors = payload.get("errors") or []
    if errors:
        message = errors[0].get("message") if isinstance(errors[0], dict) else str(errors[0])
        raise _PtsRunnerError(
            error_message=str(message or "PTS GraphQL 返回错误"),
            error_type="business_rejected",
            retryable=False,
        )
    data = payload.get("data")
    if not isinstance(data, dict):
        raise _PtsRunnerError(
            error_message="PTS GraphQL 缺少 data 字段",
            error_type="response_invalid",
            retryable=False,
        )
    return data


def _is_pts_auth_response(response: httpx.Response) -> bool:
    final_url = str(response.url)
    if response.status_code in {401, 403} or "auth.chaitin.net/login" in final_url:
        return True
    content_type = response.headers.get("content-type", "")
    if "application/json" not in content_type.lower() and "auth.chaitin.net/login" in response.text[:1000]:
        return True
    return False
