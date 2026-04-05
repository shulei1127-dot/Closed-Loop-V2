from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import subprocess
from typing import Any

import httpx
from pydantic import BaseModel, Field

from core.config import Settings
from services.dingtalk_visit_writeback import DingtalkVisitWritebackService
from services.recognizers.visit_delivery_backfill import (
    _find_local_chrome_user_data_dir,
    strip_url_fragment,
)
from services.executors.runner_contract import (
    apply_validation_result,
    build_runner_diagnostics,
    mark_runner_failure,
    mark_runner_success,
    normalize_action_result,
    refresh_runner_diagnostics,
)
from services.executors.schemas import ExecutorContext


PTS_VISIT_TYPE_MAP = {
    "客户满意度调研": "client",
    "交付满意度评价": "delivery",
    "交付回访": "delivery",
    "售后回访": "delivery",
}

PTS_SCORE_MAP = {
    "十分满意": "five",
    "非常满意": "five",
    "满意": "four",
    "一般": "three",
    "不满意": "two",
    "非常不满意": "one",
    "十分不满意": "one",
}


class VisitRealRunOutcome(BaseModel):
    run_status: str
    final_link: str | None = None
    error_message: str | None = None
    retryable: bool = False
    action_results: list[dict[str, Any]] = Field(default_factory=list)
    runner_diagnostics: dict[str, Any] = Field(default_factory=dict)


@dataclass
class _PtsVisitRuntime:
    visitor_id: str | None = None
    visitor_name: str | None = None
    company_id: str | None = None
    company_name: str | None = None
    contact_id: str | None = None
    contact_name: str | None = None
    product_id: str | None = None
    product_form_id: str | None = None
    visit_id: str | None = None
    content_id: str | None = None
    final_link: str | None = None
    visit_type: str | None = None


class _PtsRunnerError(Exception):
    def __init__(
        self,
        *,
        error_message: str,
        error_type: str,
        retryable: bool = False,
        http_status: int | None = None,
    ) -> None:
        super().__init__(error_message)
        self.error_message = error_message
        self.error_type = error_type
        self.retryable = retryable
        self.http_status = http_status


class _PtsBrowserSession:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._current_project_url: str | None = None

    async def __aenter__(self) -> "_PtsBrowserSession":
        if _find_local_chrome_user_data_dir() is None:
            raise _PtsRunnerError(
                error_message="未找到本机 Chrome 登录会话，请先登录 PTS",
                error_type="session_expired",
                retryable=False,
            )
        running = await self._run_applescript(
            """
            tell application "Google Chrome"
              return running
            end tell
            """
        )
        if str(running).strip().lower() != "true":
            raise _PtsRunnerError(
                error_message="请先打开 Google Chrome 并登录 PTS",
                error_type="session_expired",
                retryable=False,
            )
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def open_project(self, target: str) -> dict[str, Any]:
        normalized_target = strip_url_fragment(target)
        try:
            final_url = await self._run_applescript(
                f'''
                tell application "Google Chrome"
                  activate
                  set targetUrl to {json.dumps(normalized_target)}
                  if (count of windows) = 0 then make new window
                  set matchedWindowIndex to 0
                  set matchedTabIndex to 0
                  repeat with windowIndex from 1 to count of windows
                    tell window windowIndex
                      repeat with tabIndex from 1 to count of tabs
                        set currentUrl to URL of tab tabIndex
                        if currentUrl contains "pts.chaitin.net/project/" then
                          set matchedWindowIndex to windowIndex
                          set matchedTabIndex to tabIndex
                          exit repeat
                        end if
                      end repeat
                    end tell
                    if matchedWindowIndex is not 0 then exit repeat
                  end repeat
                  if matchedWindowIndex is 0 then
                    tell front window
                      make new tab with properties {{URL:targetUrl}}
                      set active tab index to (count of tabs)
                    end tell
                  else
                    set index of window matchedWindowIndex to 1
                    tell front window
                      set active tab index to matchedTabIndex
                      set URL of active tab to targetUrl
                    end tell
                  end if
                  delay 2
                  return URL of active tab of front window
                end tell
                '''
            )
        except _PtsRunnerError:
            raise
        except Exception:
            return {
                "action": "open_pts_delivery_link",
                "status": "failed",
                "target": target,
                "error_type": "timeout",
                "error_message": "打开 PTS 链接超时",
                "retryable": True,
            }
        if "auth.chaitin.net/login" in str(final_url):
            return {
                "action": "open_pts_delivery_link",
                "status": "failed",
                "target": target,
                "error_type": "session_expired",
                "error_message": "PTS 会话已失效，请重新登录 PTS 或更新 Cookie",
                "retryable": False,
            }
        return {
            "action": "open_pts_delivery_link",
            "status": "success",
            "target": target,
            "http_status": 200,
        }

    async def graphql(self, query: str) -> dict[str, Any]:
        js = (
            "var xhr=new XMLHttpRequest();"
            "xhr.open('POST','/query',false);"
            "xhr.withCredentials=true;"
            "xhr.setRequestHeader('Content-Type','application/json');"
            "xhr.setRequestHeader('Accept','*/*');"
            f"try{{xhr.send(JSON.stringify({{query:{json.dumps(query)}}}));"
            "JSON.stringify({status:xhr.status,responseURL:(xhr.responseURL||''),text:xhr.responseText,url:window.location.href});}"
            "catch(e){JSON.stringify({status:0,error:String(e),url:window.location.href});}"
        )
        raw = await self._run_applescript(
            f'''
            tell application "Google Chrome"
              return execute active tab of front window javascript {json.dumps(js)}
            end tell
            '''
        )
        try:
            result = json.loads(raw or "{}")
        except json.JSONDecodeError as exc:
            raise _PtsRunnerError(
                error_message="Chrome 会话执行返回非法结果",
                error_type="response_invalid",
                retryable=False,
            ) from exc
        status = int(result.get("status") or 0)
        url = str(result.get("url") or "")
        response_url = str(result.get("responseURL") or "")
        text = str(result.get("text") or "")
        if "auth.chaitin.net/login" in url or "auth.chaitin.net/login" in response_url or status in {401, 403}:
            raise _PtsRunnerError(
                error_message="PTS 会话已失效，请重新登录 PTS 或更新 Cookie",
                error_type="session_expired",
                retryable=False,
                http_status=status or None,
            )
        if status >= 400:
            raise _PtsRunnerError(
                error_message=f"PTS GraphQL 请求失败: {status}",
                error_type="http_error" if status >= 500 else "business_rejected",
                retryable=status >= 500,
                http_status=status,
            )
        try:
            payload = json.loads(text)
        except ValueError as exc:
            raise _PtsRunnerError(
                error_message="PTS GraphQL 返回非法 JSON",
                error_type="response_invalid",
                retryable=False,
            ) from exc
        errors = payload.get("errors") or []
        if errors:
            message = errors[0].get("message") or "PTS GraphQL 返回错误"
            raise _PtsRunnerError(
                error_message=str(message),
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

    async def read_page_text(self, *, limit: int = 4000) -> str:
        js = f"document.body ? document.body.innerText.slice(0,{int(limit)}) : ''"
        return await self._run_applescript(
            f'''
            tell application "Google Chrome"
              return execute active tab of front window javascript {json.dumps(js)}
            end tell
            '''
        )

    async def execute_js(self, script: str) -> Any:
        raw = await self._run_applescript(
            f'''
            tell application "Google Chrome"
              return execute active tab of front window javascript {json.dumps(script, ensure_ascii=False)}
            end tell
            '''
        )
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw

    async def send_key_code(self, key_code: int) -> None:
        await self._run_applescript(
            f'''
            tell application "Google Chrome" to activate
            tell application "System Events"
              key code {key_code}
            end tell
            '''
        )

    async def send_key_code_repeated(self, key_code: int, count: int) -> None:
        count = max(1, int(count))
        await self._run_applescript(
            f'''
            tell application "Google Chrome" to activate
            tell application "System Events"
              repeat {count} times
                key code {key_code}
                delay 0.03
              end repeat
            end tell
            '''
        )

    async def _run_applescript(self, script: str) -> str:
        def _invoke() -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                ["osascript", "-"],
                input=script,
                text=True,
                capture_output=True,
            )

        result = await asyncio.to_thread(_invoke)
        if result.returncode != 0:
            raise _PtsRunnerError(
                error_message="无法驱动本机 Chrome 会话，请检查浏览器自动化权限",
                error_type="unknown_error",
                retryable=False,
            )
        return result.stdout.strip()


class VisitRealRunner:
    def __init__(
        self,
        settings: Settings,
        *,
        writeback_service: DingtalkVisitWritebackService | None = None,
    ) -> None:
        self.settings = settings
        self.writeback_service = writeback_service or DingtalkVisitWritebackService(settings)

    def validate(self) -> tuple[bool, dict[str, Any], str | None]:
        diagnostics = self._base_diagnostics()
        missing_fields: list[str] = []
        if self._use_legacy_api_mode():
            if not self.settings.pts_cookie_header:
                missing_fields.append("pts_cookie_header")
            if not self.settings.visit_real_base_url:
                missing_fields.append("visit_real_base_url")
            if not self.settings.visit_real_create_endpoint:
                missing_fields.append("visit_real_create_endpoint")
            if not self.settings.visit_real_assign_endpoint_template:
                missing_fields.append("visit_real_assign_endpoint_template")
            if not self.settings.visit_real_mark_target_endpoint_template:
                missing_fields.append("visit_real_mark_target_endpoint_template")
            if not self.settings.visit_real_fill_feedback_endpoint_template:
                missing_fields.append("visit_real_fill_feedback_endpoint_template")
            if not self.settings.visit_real_complete_endpoint_template:
                missing_fields.append("visit_real_complete_endpoint_template")
            if not self.settings.visit_real_token:
                missing_fields.append("visit_real_token")
        else:
            if not self.settings.pts_base_url:
                missing_fields.append("pts_base_url")
            if not self._browser_session_available() and not self.settings.pts_cookie_header:
                missing_fields.append("pts_cookie_header")
        apply_validation_result(diagnostics, missing_fields)
        if missing_fields:
            return False, diagnostics, "visit 真实执行配置缺失"
        return True, diagnostics, None

    async def run(self, context: ExecutorContext, actions: list[dict[str, Any]]) -> VisitRealRunOutcome:
        valid, diagnostics, error_message = self.validate()
        if not valid:
            return VisitRealRunOutcome(
                run_status="failed",
                error_message=error_message,
                retryable=False,
                runner_diagnostics=diagnostics,
            )

        if self._use_legacy_api_mode():
            return await self._run_legacy_api_mode(context, actions, diagnostics)
        if self._browser_session_available():
            return await self._run_pts_browser_mode(context, actions, diagnostics)
        return await self._run_pts_direct_mode(context, actions, diagnostics)

    def _use_legacy_api_mode(self) -> bool:
        return bool(self.settings.visit_real_base_url and self.settings.visit_real_token)

    def _browser_session_available(self) -> bool:
        return _find_local_chrome_user_data_dir() is not None

    async def _run_pts_direct_mode(
        self,
        context: ExecutorContext,
        actions: list[dict[str, Any]],
        diagnostics: dict[str, Any],
    ) -> VisitRealRunOutcome:
        action_results: list[dict[str, Any]] = []
        runtime = _PtsVisitRuntime()

        try:
            async with httpx.AsyncClient(
                base_url=self.settings.pts_base_url,
                timeout=self.settings.visit_real_timeout_seconds,
                verify=self.settings.pts_verify_ssl,
                follow_redirects=True,
                headers=self._pts_headers(),
            ) as client:
                open_result = normalize_action_result(await self._open_pts_link(context, actions[0]))
                action_results.append(open_result)
                if open_result["status"] != "success":
                    return self._failure_outcome(
                        diagnostics=diagnostics,
                        action_results=action_results,
                        action_result=open_result,
                        fallback_message="打开 PTS 链接失败",
                    )

                create_result = normalize_action_result(
                    await self._create_visit_work_order_pts(
                        query_func=lambda query: self._pts_graphql(client, query),
                        context=context,
                        action=actions[1],
                        owner_action=actions[2],
                        runtime=runtime,
                    )
                )
                action_results.append(create_result)
                if create_result["status"] != "success":
                    return self._failure_outcome(
                        diagnostics=diagnostics,
                        action_results=action_results,
                        action_result=create_result,
                        fallback_message="创建回访工单失败",
                    )

                assign_result = normalize_action_result(await self._assign_owner_direct(actions[2], runtime))
                action_results.append(assign_result)
                if assign_result["status"] != "success":
                    return self._failure_outcome(
                        diagnostics=diagnostics,
                        action_results=action_results,
                        action_result=assign_result,
                        fallback_message="指派负责人失败",
                    )

                mark_target_result = normalize_action_result(
                    await self._mark_visit_target_direct(actions[3], runtime)
                )
                action_results.append(mark_target_result)
                if mark_target_result["status"] != "success":
                    return self._failure_outcome(
                        diagnostics=diagnostics,
                        action_results=action_results,
                        action_result=mark_target_result,
                        fallback_message="标记回访对象失败",
                    )

                fill_feedback_result = normalize_action_result(
                    await self._fill_feedback_pts(
                        query_func=lambda query: self._pts_graphql(client, query),
                        context=context,
                        action=actions[4],
                        runtime=runtime,
                    )
                )
                action_results.append(fill_feedback_result)
                if fill_feedback_result["status"] != "success":
                    return self._failure_outcome(
                        diagnostics=diagnostics,
                        action_results=action_results,
                        action_result=fill_feedback_result,
                        fallback_message="填写反馈失败",
                    )

                complete_result = normalize_action_result(
                    await self._complete_visit_pts(
                        query_func=lambda query: self._pts_graphql(client, query),
                        runtime=runtime,
                    )
                )
                action_results.append(complete_result)
                if complete_result["status"] != "success":
                    return self._failure_outcome(
                        diagnostics=diagnostics,
                        action_results=action_results,
                        action_result=complete_result,
                        fallback_message="完成回访失败",
                    )

                writeback_result = await self._run_writeback(context, runtime.final_link)
                if writeback_result is not None:
                    action_results.append(normalize_action_result(writeback_result))
                    if writeback_result["status"] != "success":
                        return self._failure_outcome(
                            diagnostics=diagnostics,
                            action_results=action_results,
                            action_result=writeback_result,
                            fallback_message="回写钉钉文档失败",
                            final_link=runtime.final_link,
                        )

                action_results = refresh_runner_diagnostics(diagnostics, action_results)
                mark_runner_success(diagnostics)
                return VisitRealRunOutcome(
                    run_status="success",
                    final_link=runtime.final_link,
                    retryable=False,
                    action_results=action_results,
                    runner_diagnostics=diagnostics,
                )
        except httpx.TimeoutException as exc:
            action_results = refresh_runner_diagnostics(diagnostics, action_results)
            mark_runner_failure(diagnostics, error_type="timeout", last_error=str(exc))
            return VisitRealRunOutcome(
                run_status="failed",
                error_message="visit real runner 请求超时",
                retryable=True,
                action_results=action_results,
                runner_diagnostics=diagnostics,
            )
        except httpx.HTTPError as exc:
            action_results = refresh_runner_diagnostics(diagnostics, action_results)
            mark_runner_failure(diagnostics, error_type="http_error", last_error=str(exc))
            return VisitRealRunOutcome(
                run_status="failed",
                error_message="visit real runner 请求失败",
                retryable=True,
                action_results=action_results,
                runner_diagnostics=diagnostics,
            )

    async def _run_pts_browser_mode(
        self,
        context: ExecutorContext,
        actions: list[dict[str, Any]],
        diagnostics: dict[str, Any],
    ) -> VisitRealRunOutcome:
        action_results: list[dict[str, Any]] = []
        runtime = _PtsVisitRuntime()
        diagnostics["transport_mode"] = "pts_browser_session"
        try:
            async with _PtsBrowserSession(self.settings) as browser:
                open_result = normalize_action_result(await browser.open_project(actions[0].get("target") or context.normalized_data.get("pts_link")))
                action_results.append(open_result)
                if open_result["status"] != "success":
                    return self._failure_outcome(
                        diagnostics=diagnostics,
                        action_results=action_results,
                        action_result=open_result,
                        fallback_message="打开 PTS 链接失败",
                    )

                create_result = normalize_action_result(
                    await self._create_visit_work_order_pts(
                        query_func=browser.graphql,
                        context=context,
                        action=actions[1],
                        owner_action=actions[2],
                        runtime=runtime,
                    )
                )
                action_results.append(create_result)
                if create_result["status"] != "success":
                    return self._failure_outcome(
                        diagnostics=diagnostics,
                        action_results=action_results,
                        action_result=create_result,
                        fallback_message="创建回访工单失败",
                    )

                assign_result = normalize_action_result(await self._assign_owner_direct(actions[2], runtime))
                action_results.append(assign_result)
                if assign_result["status"] != "success":
                    return self._failure_outcome(
                        diagnostics=diagnostics,
                        action_results=action_results,
                        action_result=assign_result,
                        fallback_message="指派负责人失败",
                    )

                mark_target_result = normalize_action_result(await self._mark_visit_target_direct(actions[3], runtime))
                action_results.append(mark_target_result)
                if mark_target_result["status"] != "success":
                    return self._failure_outcome(
                        diagnostics=diagnostics,
                        action_results=action_results,
                        action_result=mark_target_result,
                        fallback_message="标记回访对象失败",
                    )

                fill_feedback_result = normalize_action_result(
                    await self._fill_feedback_pts(
                        query_func=browser.graphql,
                        context=context,
                        action=actions[4],
                        runtime=runtime,
                    )
                )
                action_results.append(fill_feedback_result)
                if fill_feedback_result["status"] != "success":
                    return self._failure_outcome(
                        diagnostics=diagnostics,
                        action_results=action_results,
                        action_result=fill_feedback_result,
                        fallback_message="填写反馈失败",
                    )

                complete_result = normalize_action_result(
                    await self._complete_visit_pts(
                        query_func=browser.graphql,
                        runtime=runtime,
                    )
                )
                action_results.append(complete_result)
                if complete_result["status"] != "success":
                    return self._failure_outcome(
                        diagnostics=diagnostics,
                        action_results=action_results,
                        action_result=complete_result,
                        fallback_message="完成回访失败",
                    )

                writeback_result = await self._run_writeback(context, runtime.final_link)
                if writeback_result is not None:
                    action_results.append(normalize_action_result(writeback_result))
                    if writeback_result["status"] != "success":
                        return self._failure_outcome(
                            diagnostics=diagnostics,
                            action_results=action_results,
                            action_result=writeback_result,
                            fallback_message="回写钉钉文档失败",
                            final_link=runtime.final_link,
                        )

                action_results = refresh_runner_diagnostics(diagnostics, action_results)
                mark_runner_success(diagnostics)
                return VisitRealRunOutcome(
                    run_status="success",
                    final_link=runtime.final_link,
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
            return VisitRealRunOutcome(
                run_status="failed",
                error_message=exc.error_message,
                retryable=exc.retryable,
                action_results=action_results,
                runner_diagnostics=diagnostics,
            )

    async def _run_legacy_api_mode(
        self,
        context: ExecutorContext,
        actions: list[dict[str, Any]],
        diagnostics: dict[str, Any],
    ) -> VisitRealRunOutcome:
        headers = {self.settings.visit_real_token_header: self.settings.visit_real_token}
        action_results: list[dict[str, Any]] = []
        try:
            async with httpx.AsyncClient(
                base_url=self.settings.visit_real_base_url,
                timeout=self.settings.visit_real_timeout_seconds,
                verify=self.settings.visit_real_verify_ssl,
                headers=headers,
            ) as client:
                open_result = normalize_action_result(await self._open_pts_link(context, actions[0]))
                action_results.append(open_result)
                if open_result["status"] != "success":
                    return self._failure_outcome(
                        diagnostics=diagnostics,
                        action_results=action_results,
                        action_result=open_result,
                        fallback_message="打开 PTS 链接失败",
                    )

                create_result = normalize_action_result(await self._create_visit_work_order_legacy(client, context, actions[1]))
                action_results.append(create_result)
                if create_result["status"] != "success":
                    return self._failure_outcome(
                        diagnostics=diagnostics,
                        action_results=action_results,
                        action_result=create_result,
                        fallback_message="创建回访工单失败",
                    )

                final_link = create_result.get("final_link")
                assign_result = normalize_action_result(await self._assign_owner_legacy(client, context, actions[2]))
                action_results.append(assign_result)
                if assign_result["status"] != "success":
                    return self._failure_outcome(
                        diagnostics=diagnostics,
                        action_results=action_results,
                        action_result=assign_result,
                        fallback_message="指派负责人失败",
                    )

                mark_target_result = normalize_action_result(
                    await self._mark_visit_target_legacy(client, context, actions[3])
                )
                action_results.append(mark_target_result)
                if mark_target_result["status"] != "success":
                    return self._failure_outcome(
                        diagnostics=diagnostics,
                        action_results=action_results,
                        action_result=mark_target_result,
                        fallback_message="标记回访对象失败",
                    )

                fill_feedback_result = normalize_action_result(
                    await self._fill_feedback_legacy(client, context, actions[4], final_link)
                )
                action_results.append(fill_feedback_result)
                if fill_feedback_result["status"] != "success":
                    return self._failure_outcome(
                        diagnostics=diagnostics,
                        action_results=action_results,
                        action_result=fill_feedback_result,
                        fallback_message="填写反馈失败",
                    )

                complete_result = normalize_action_result(
                    await self._complete_visit_legacy(client, context, actions[5], final_link)
                )
                action_results.append(complete_result)
                if complete_result["status"] != "success":
                    return self._failure_outcome(
                        diagnostics=diagnostics,
                        action_results=action_results,
                        action_result=complete_result,
                        fallback_message="完成回访失败",
                    )

                writeback_result = await self._run_writeback(context, final_link)
                if writeback_result is not None:
                    action_results.append(normalize_action_result(writeback_result))
                    if writeback_result["status"] != "success":
                        return self._failure_outcome(
                            diagnostics=diagnostics,
                            action_results=action_results,
                            action_result=writeback_result,
                            fallback_message="回写钉钉文档失败",
                            final_link=final_link,
                        )

                action_results = refresh_runner_diagnostics(diagnostics, action_results)
                mark_runner_success(diagnostics)
                return VisitRealRunOutcome(
                    run_status="success",
                    final_link=final_link,
                    retryable=False,
                    action_results=action_results,
                    runner_diagnostics=diagnostics,
                )
        except httpx.TimeoutException as exc:
            action_results = refresh_runner_diagnostics(diagnostics, action_results)
            mark_runner_failure(diagnostics, error_type="timeout", last_error=str(exc))
            return VisitRealRunOutcome(
                run_status="failed",
                error_message="visit real runner 请求超时",
                retryable=True,
                action_results=action_results,
                runner_diagnostics=diagnostics,
            )
        except httpx.HTTPError as exc:
            action_results = refresh_runner_diagnostics(diagnostics, action_results)
            mark_runner_failure(diagnostics, error_type="http_error", last_error=str(exc))
            return VisitRealRunOutcome(
                run_status="failed",
                error_message="visit real runner 请求失败",
                retryable=True,
                action_results=action_results,
                runner_diagnostics=diagnostics,
            )

    async def _open_pts_link(
        self,
        context: ExecutorContext,
        action: dict[str, Any],
    ) -> dict[str, Any]:
        target = action.get("target") or context.normalized_data.get("pts_link")
        try:
            async with httpx.AsyncClient(
                timeout=self.settings.visit_real_timeout_seconds,
                verify=self.settings.pts_verify_ssl,
                headers={"Cookie": self.settings.pts_cookie_header, "User-Agent": "Mozilla/5.0"},
                follow_redirects=True,
            ) as pts_client:
                response = await pts_client.get(str(target))
            if _is_pts_session_expired(response):
                return {
                    "action": "open_pts_delivery_link",
                    "status": "failed",
                    "target": target,
                    "http_status": response.status_code,
                    "error_type": "session_expired",
                    "error_message": "PTS 会话已失效，请重新登录 PTS 或更新 Cookie",
                    "retryable": False,
                }
            if response.status_code >= 400:
                return {
                    "action": "open_pts_delivery_link",
                    "status": "failed",
                    "target": target,
                    "http_status": response.status_code,
                    "error_message": f"打开 PTS 链接失败: {response.status_code}",
                    "retryable": response.status_code >= 500,
                }
            return {
                "action": "open_pts_delivery_link",
                "status": "success",
                "target": target,
                "http_status": response.status_code,
            }
        except httpx.TimeoutException:
            return {
                "action": "open_pts_delivery_link",
                "status": "failed",
                "target": target,
                "error_type": "timeout",
                "error_message": "打开 PTS 链接超时",
                "retryable": True,
            }

    async def _create_visit_work_order_pts(
        self,
        *,
        query_func,
        context: ExecutorContext,
        action: dict[str, Any],
        owner_action: dict[str, Any],
        runtime: _PtsVisitRuntime,
    ) -> dict[str, Any]:
        try:
            desired_owner = owner_action.get("owner") or "舒磊"
            me = await query_func(_build_me_query())
            runtime.visitor_id = ((me.get("me") or {}).get("id"))
            runtime.visitor_name = ((me.get("me") or {}).get("name"))
            if not runtime.visitor_id:
                raise _PtsRunnerError(
                    error_message="PTS 当前会话缺少用户信息",
                    error_type="response_invalid",
                    retryable=False,
                )
            if desired_owner and runtime.visitor_name and runtime.visitor_name != desired_owner:
                raise _PtsRunnerError(
                    error_message=f"PTS 当前会话用户不是 {desired_owner}",
                    error_type="business_rejected",
                    retryable=False,
                )

            delivery_id = action.get("delivery_id") or context.normalized_data.get("delivery_id")
            delivery_meta = await query_func(_build_delivery_meta_query(delivery_id))
            runtime.company_id = _read_path(delivery_meta, "list_product_delivery.data.0.project.company.id")
            runtime.company_name = _read_path(delivery_meta, "list_product_delivery.data.0.project.company.name")
            contacts = _read_path(delivery_meta, "list_product_delivery.data.0.project.company.contact") or []
            contact = _select_contact(contacts, context.normalized_data.get("visit_contact"))
            if not contact:
                raise _PtsRunnerError(
                    error_message="PTS 项目缺少可用联系人",
                    error_type="business_rejected",
                    retryable=False,
                )
            runtime.contact_id = contact.get("id")
            runtime.contact_name = contact.get("name")
            runtime.product_id = _read_path(delivery_meta, "list_product_delivery.data.0.project.product_detail_list.0.product.id")
            runtime.product_form_id = _read_path(delivery_meta, "list_product_delivery.data.0.project.product_detail_list.0.form.id")
            if not runtime.company_id or not runtime.contact_id or not runtime.product_id or not runtime.product_form_id:
                raise _PtsRunnerError(
                    error_message="PTS 项目详情缺少创建回访所需字段",
                    error_type="response_invalid",
                    retryable=False,
                )

            runtime.visit_type = _map_pts_visit_type(context.normalized_data.get("visit_type"))
            if runtime.visit_type is None:
                raise _PtsRunnerError(
                    error_message=f"visit_type `{context.normalized_data.get('visit_type')}` 暂不支持 PTS 自动执行",
                    error_type="business_rejected",
                    retryable=False,
                )

            before_ids = await self._list_open_visit_ids(
                query_func,
                company_id=runtime.company_id,
                delivery_id=delivery_id,
                visitor_id=runtime.visitor_id,
            )

            await query_func(
                _build_create_visit_mutation(
                    company_id=runtime.company_id,
                    visitor_id=runtime.visitor_id,
                    visit_type=runtime.visit_type,
                    contact_id=runtime.contact_id,
                    product_id=runtime.product_id,
                    form_id=runtime.product_form_id,
                    delivery_id=delivery_id,
                ),
            )

            visit = await self._find_created_visit(
                query_func,
                company_id=runtime.company_id,
                delivery_id=delivery_id,
                visitor_id=runtime.visitor_id,
                before_ids=before_ids,
            )
            runtime.visit_id = visit.get("id")
            runtime.final_link = _build_visit_detail_link(self.settings.pts_base_url, runtime.visit_id)
            detail = await query_func(_build_visit_detail_query(runtime.visit_id))
            runtime.content_id = _read_path(detail, "visit_detail.content_list.0.id")
            if not runtime.content_id:
                raise _PtsRunnerError(
                    error_message="创建回访成功但缺少 content_id",
                    error_type="response_invalid",
                    retryable=False,
                )
            return {
                "action": "create_visit_work_order",
                "status": "success",
                "http_status": 200,
                "final_link": runtime.final_link,
                "visit_id": runtime.visit_id,
                "contact_name": runtime.contact_name,
            }
        except _PtsRunnerError as exc:
            return _failed_action(
                action="create_visit_work_order",
                error_message=exc.error_message,
                error_type=exc.error_type,
                retryable=exc.retryable,
                http_status=exc.http_status,
            )

    async def _assign_owner_direct(
        self,
        action: dict[str, Any],
        runtime: _PtsVisitRuntime,
    ) -> dict[str, Any]:
        owner = action.get("owner") or "舒磊"
        if runtime.visitor_name and owner and runtime.visitor_name != owner:
            return _failed_action(
                action="assign_owner",
                error_message=f"PTS 当前会话用户不是 {owner}",
                error_type="business_rejected",
                retryable=False,
            )
        return {
            "action": "assign_owner",
            "status": "success",
            "http_status": 200,
            "owner": owner,
            "owner_source": "pts_current_user",
        }

    async def _mark_visit_target_direct(
        self,
        action: dict[str, Any],
        runtime: _PtsVisitRuntime,
    ) -> dict[str, Any]:
        if not runtime.contact_id:
            return _failed_action(
                action="mark_visit_target",
                error_message="缺少可用联系人，无法标记回访对象",
                error_type="business_rejected",
                retryable=False,
            )
        return {
            "action": "mark_visit_target",
            "status": "success",
            "http_status": 200,
            "customer_name": action.get("customer_name"),
            "contact_name": runtime.contact_name,
            "visit_object": True,
        }

    async def _fill_feedback_pts(
        self,
        *,
        query_func,
        context: ExecutorContext,
        action: dict[str, Any],
        runtime: _PtsVisitRuntime,
    ) -> dict[str, Any]:
        try:
            if not runtime.visit_id or not runtime.content_id or not runtime.contact_id:
                raise _PtsRunnerError(
                    error_message="缺少 process_visit 所需上下文",
                    error_type="response_invalid",
                    retryable=False,
                )
            score = _map_visit_score(action.get("satisfaction") or context.normalized_data.get("satisfaction"))
            if score is None:
                raise _PtsRunnerError(
                    error_message="缺少可用满意度评分，无法自动提交回访内容",
                    error_type="business_rejected",
                    retryable=False,
                )
            feedback_note = str(action.get("feedback_note") or context.normalized_data.get("feedback_note") or "").strip()
            await query_func(
                _build_process_visit_mutation(
                    visit_id=runtime.visit_id,
                    contact_id=runtime.contact_id,
                    content_id=runtime.content_id,
                    score=score,
                    feedback_note=feedback_note,
                    visit_time=_utc_now_z(),
                ),
            )
            return {
                "action": "fill_feedback",
                "status": "success",
                "http_status": 200,
                "satisfaction": action.get("satisfaction") or context.normalized_data.get("satisfaction"),
            }
        except _PtsRunnerError as exc:
            return _failed_action(
                action="fill_feedback",
                error_message=exc.error_message,
                error_type=exc.error_type,
                retryable=exc.retryable,
                http_status=exc.http_status,
            )

    async def _complete_visit_pts(
        self,
        *,
        query_func,
        runtime: _PtsVisitRuntime,
    ) -> dict[str, Any]:
        try:
            if not runtime.visit_id:
                raise _PtsRunnerError(
                    error_message="缺少 visit_id，无法完成回访",
                    error_type="response_invalid",
                    retryable=False,
                )
            await query_func(_build_finish_visit_mutation(runtime.visit_id))
            return {
                "action": "complete_visit",
                "status": "success",
                "http_status": 200,
                "final_link": runtime.final_link,
            }
        except _PtsRunnerError as exc:
            return _failed_action(
                action="complete_visit",
                error_message=exc.error_message,
                error_type=exc.error_type,
                retryable=exc.retryable,
                http_status=exc.http_status,
            )

    async def _pts_graphql(self, client: httpx.AsyncClient, query: str) -> dict[str, Any]:
        try:
            response = await client.post("/query", json={"query": query})
        except httpx.TimeoutException as exc:
            raise _PtsRunnerError(
                error_message="PTS GraphQL 请求超时",
                error_type="timeout",
                retryable=True,
            ) from exc
        if _is_pts_session_expired(response):
            raise _PtsRunnerError(
                error_message="PTS 会话已失效，请重新登录 PTS 或更新 Cookie",
                error_type="session_expired",
                retryable=False,
                http_status=response.status_code,
            )
        if response.status_code >= 400:
            raise _PtsRunnerError(
                error_message=f"PTS GraphQL 请求失败: {response.status_code}",
                error_type="http_error" if response.status_code >= 500 else "business_rejected",
                retryable=response.status_code >= 500,
                http_status=response.status_code,
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise _PtsRunnerError(
                error_message="PTS GraphQL 返回非法 JSON",
                error_type="response_invalid",
                retryable=False,
            ) from exc
        errors = payload.get("errors") or []
        if errors:
            message = errors[0].get("message") or "PTS GraphQL 返回错误"
            raise _PtsRunnerError(
                error_message=str(message),
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

    async def _list_open_visit_ids(
        self,
        query_func,
        *,
        company_id: str,
        delivery_id: str,
        visitor_id: str,
    ) -> set[str]:
        data = await query_func(_build_list_visit_query(company_id, delivery_id, visitor_id))
        items = data.get("list_visit", {}).get("data") or []
        return {str(item.get("id")) for item in items if item.get("id")}

    async def _find_created_visit(
        self,
        query_func,
        *,
        company_id: str,
        delivery_id: str,
        visitor_id: str,
        before_ids: set[str],
    ) -> dict[str, Any]:
        data = await query_func(_build_list_visit_query(company_id, delivery_id, visitor_id))
        items = data.get("list_visit", {}).get("data") or []
        if not items:
            raise _PtsRunnerError(
                error_message="创建回访后未找到对应 visit 记录",
                error_type="response_invalid",
                retryable=False,
            )
        candidates = [item for item in items if str(item.get("id")) not in before_ids]
        selected = _select_latest_visit(candidates or items)
        if not selected or not selected.get("id"):
            raise _PtsRunnerError(
                error_message="创建回访后未找到有效 visit_id",
                error_type="response_invalid",
                retryable=False,
            )
        return selected

    async def _create_visit_work_order_legacy(
        self,
        client: httpx.AsyncClient,
        context: ExecutorContext,
        action: dict[str, Any],
    ) -> dict[str, Any]:
        payload = {
            "task_plan_id": context.task_plan_id,
            "customer_name": context.normalized_data.get("customer_name"),
            "delivery_id": action.get("delivery_id") or context.normalized_data.get("delivery_id"),
            "work_order_type": action.get("work_order_type"),
            "visit_contact": context.normalized_data.get("visit_contact"),
            "satisfaction": context.normalized_data.get("satisfaction"),
            "feedback_note": context.normalized_data.get("feedback_note"),
        }
        try:
            response = await client.post(self.settings.visit_real_create_endpoint, json=payload)
            if response.status_code >= 400:
                return {
                    "action": "create_visit_work_order",
                    "status": "failed",
                    "http_status": response.status_code,
                    "error_message": f"创建回访工单失败: {response.status_code}",
                    "retryable": response.status_code >= 500,
                }
            data = response.json()
            final_link = _read_path(data, self.settings.visit_real_final_link_path)
            if not final_link:
                return _failed_action(
                    action="create_visit_work_order",
                    error_message="创建回访工单成功但缺少 final_link",
                    error_type="response_invalid",
                    retryable=False,
                    http_status=response.status_code,
                )
            return {
                "action": "create_visit_work_order",
                "status": "success",
                "http_status": response.status_code,
                "final_link": final_link,
            }
        except httpx.TimeoutException:
            return _failed_action(
                action="create_visit_work_order",
                error_message="创建回访工单超时",
                error_type="timeout",
                retryable=True,
            )
        except ValueError:
            return _failed_action(
                action="create_visit_work_order",
                error_message="创建回访工单返回非法 JSON",
                error_type="response_invalid",
                retryable=False,
            )

    async def _assign_owner_legacy(
        self,
        client: httpx.AsyncClient,
        context: ExecutorContext,
        action: dict[str, Any],
    ) -> dict[str, Any]:
        delivery_id = context.normalized_data.get("delivery_id")
        endpoint = self.settings.visit_real_assign_endpoint_template.format(delivery_id=delivery_id)
        payload = {
            "task_plan_id": context.task_plan_id,
            "delivery_id": delivery_id,
            "owner": action.get("owner") or "舒磊",
        }
        try:
            response = await client.post(endpoint, json=payload)
            if response.status_code >= 400:
                return _failed_action(
                    action="assign_owner",
                    error_message=f"指派负责人失败: {response.status_code}",
                    error_type=None,
                    retryable=response.status_code >= 500,
                    http_status=response.status_code,
                )
            return {
                "action": "assign_owner",
                "status": "success",
                "http_status": response.status_code,
                "owner": payload["owner"],
            }
        except httpx.TimeoutException:
            return _failed_action(
                action="assign_owner",
                error_message="指派负责人超时",
                error_type="timeout",
                retryable=True,
            )

    async def _mark_visit_target_legacy(
        self,
        client: httpx.AsyncClient,
        context: ExecutorContext,
        action: dict[str, Any],
    ) -> dict[str, Any]:
        delivery_id = context.normalized_data.get("delivery_id")
        endpoint = self.settings.visit_real_mark_target_endpoint_template.format(delivery_id=delivery_id)
        payload = {
            "task_plan_id": context.task_plan_id,
            "delivery_id": delivery_id,
            "customer_name": action.get("customer_name") or context.normalized_data.get("customer_name"),
        }
        try:
            response = await client.post(endpoint, json=payload)
            if response.status_code >= 400:
                return _failed_action(
                    action="mark_visit_target",
                    error_message=f"标记回访对象失败: {response.status_code}",
                    error_type=None,
                    retryable=response.status_code >= 500,
                    http_status=response.status_code,
                )
            return {
                "action": "mark_visit_target",
                "status": "success",
                "http_status": response.status_code,
                "customer_name": payload["customer_name"],
            }
        except httpx.TimeoutException:
            return _failed_action(
                action="mark_visit_target",
                error_message="标记回访对象超时",
                error_type="timeout",
                retryable=True,
            )

    async def _fill_feedback_legacy(
        self,
        client: httpx.AsyncClient,
        context: ExecutorContext,
        action: dict[str, Any],
        final_link: str | None,
    ) -> dict[str, Any]:
        delivery_id = context.normalized_data.get("delivery_id")
        endpoint = self.settings.visit_real_fill_feedback_endpoint_template.format(delivery_id=delivery_id)
        payload = {
            "task_plan_id": context.task_plan_id,
            "delivery_id": delivery_id,
            "final_link": final_link,
            "satisfaction": action.get("satisfaction") or context.normalized_data.get("satisfaction"),
            "feedback_note": action.get("feedback_note") or context.normalized_data.get("feedback_note"),
        }
        try:
            response = await client.post(endpoint, json=payload)
            if response.status_code >= 400:
                return _failed_action(
                    action="fill_feedback",
                    error_message=f"填写反馈失败: {response.status_code}",
                    error_type=None,
                    retryable=response.status_code >= 500,
                    http_status=response.status_code,
                )
            return {
                "action": "fill_feedback",
                "status": "success",
                "http_status": response.status_code,
                "satisfaction": payload["satisfaction"],
            }
        except httpx.TimeoutException:
            return _failed_action(
                action="fill_feedback",
                error_message="填写反馈超时",
                error_type="timeout",
                retryable=True,
            )

    async def _complete_visit_legacy(
        self,
        client: httpx.AsyncClient,
        context: ExecutorContext,
        action: dict[str, Any],
        final_link: str | None,
    ) -> dict[str, Any]:
        delivery_id = context.normalized_data.get("delivery_id")
        endpoint = self.settings.visit_real_complete_endpoint_template.format(delivery_id=delivery_id)
        payload = {
            "task_plan_id": context.task_plan_id,
            "delivery_id": delivery_id,
            "final_link": final_link,
            "visit_contact": action.get("visit_contact") or context.normalized_data.get("visit_contact"),
            "satisfaction": context.normalized_data.get("satisfaction"),
            "feedback_note": context.normalized_data.get("feedback_note"),
        }
        try:
            response = await client.post(endpoint, json=payload)
            if response.status_code >= 400:
                return _failed_action(
                    action="complete_visit",
                    error_message=f"完成回访失败: {response.status_code}",
                    error_type=None,
                    retryable=response.status_code >= 500,
                    http_status=response.status_code,
                )
            return {
                "action": "complete_visit",
                "status": "success",
                "http_status": response.status_code,
                "final_link": final_link,
            }
        except httpx.TimeoutException:
            return _failed_action(
                action="complete_visit",
                error_message="完成回访超时",
                error_type="timeout",
                retryable=True,
            )

    def _pts_headers(self) -> dict[str, str]:
        return {
            "Cookie": self.settings.pts_cookie_header,
            "Content-Type": "application/json",
            "Accept": "*/*",
            "Origin": self.settings.pts_base_url,
            "Referer": f"{self.settings.pts_base_url.rstrip('/')}/",
            "User-Agent": "Mozilla/5.0",
        }

    def _base_diagnostics(self) -> dict[str, Any]:
        if self._use_legacy_api_mode():
            transport_mode = "legacy_api"
            pts_auth_header = "Cookie"
        elif self._browser_session_available():
            transport_mode = "pts_browser_session"
            pts_auth_header = "ChromeProfile"
        else:
            transport_mode = "pts_direct"
            pts_auth_header = "Cookie"
        return build_runner_diagnostics(
            module_code="visit",
            runner="VisitRealRunner",
            mode="real",
            transport_mode=transport_mode,
            pts_base_url=self.settings.pts_base_url,
            pts_verify_ssl=self.settings.pts_verify_ssl,
            pts_auth_header=pts_auth_header,
            base_url=self.settings.visit_real_base_url or self.settings.pts_base_url,
            create_endpoint=self.settings.visit_real_create_endpoint if self._use_legacy_api_mode() else "/query:create_visit",
            assign_endpoint_template=self.settings.visit_real_assign_endpoint_template if self._use_legacy_api_mode() else "create_visit.visitor_id",
            mark_target_endpoint_template=self.settings.visit_real_mark_target_endpoint_template if self._use_legacy_api_mode() else "create_visit.contact_list",
            fill_feedback_endpoint_template=self.settings.visit_real_fill_feedback_endpoint_template if self._use_legacy_api_mode() else "/query:process_visit",
            complete_endpoint_template=self.settings.visit_real_complete_endpoint_template if self._use_legacy_api_mode() else "/query:finish_visit",
            token_header=self.settings.visit_real_token_header if self._use_legacy_api_mode() else None,
        )

    async def _run_writeback(
        self,
        context: ExecutorContext,
        final_link: str | None,
    ) -> dict[str, Any] | None:
        if not final_link:
            return None
        if context.source_collector_type not in {"dingtalk", "real"}:
            return None
        return await self.writeback_service.write_visit_link(context=context, final_link=final_link)

    def _failure_outcome(
        self,
        *,
        diagnostics: dict[str, Any],
        action_results: list[dict[str, Any]],
        action_result: dict[str, Any],
        fallback_message: str,
        final_link: str | None = None,
    ) -> VisitRealRunOutcome:
        action_results = refresh_runner_diagnostics(diagnostics, action_results)
        mark_runner_failure(diagnostics, action_result=action_result)
        return VisitRealRunOutcome(
            run_status="failed",
            final_link=final_link,
            error_message=action_result.get("error_message") or fallback_message,
            retryable=bool(action_result.get("retryable", False)),
            action_results=action_results,
            runner_diagnostics=diagnostics,
        )


def _read_path(data: dict[str, Any], path: str) -> Any:
    current: Any = data
    for key in path.split("."):
        if isinstance(current, list):
            if not key.isdigit():
                return None
            index = int(key)
            if index >= len(current):
                return None
            current = current[index]
            continue
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _is_pts_session_expired(response: httpx.Response) -> bool:
    final_url = str(response.url)
    location = response.headers.get("Location", "")
    if "auth.chaitin.net/login" in final_url:
        return True
    if "auth.chaitin.net/login" in location:
        return True
    if response.status_code in {401, 403}:
        return True
    content_type = response.headers.get("Content-Type", "")
    if "text/html" in content_type:
        body = response.text
        markers = (
            "auth.chaitin.net/login",
            "统一身份认证",
            "登录",
            "login-container",
        )
        return any(marker in body for marker in markers)
    return False


def _failed_action(
    *,
    action: str,
    error_message: str,
    error_type: str | None,
    retryable: bool,
    http_status: int | None = None,
) -> dict[str, Any]:
    return {
        "action": action,
        "status": "failed",
        "http_status": http_status,
        "error_message": error_message,
        "error_type": error_type,
        "retryable": retryable,
    }


def _map_pts_visit_type(value: Any) -> str | None:
    if value is None:
        return None
    return PTS_VISIT_TYPE_MAP.get(str(value).strip())


def _map_visit_score(value: Any) -> str | None:
    if value is None:
        return None
    return PTS_SCORE_MAP.get(str(value).strip())


def _select_contact(contacts: list[dict[str, Any]], desired_name: Any) -> dict[str, Any] | None:
    if not contacts:
        return None
    name = str(desired_name).strip() if desired_name else ""
    if name:
        for contact in contacts:
            contact_name = str(contact.get("name") or "").strip()
            if contact_name == name:
                return contact
        for contact in contacts:
            contact_name = str(contact.get("name") or "").strip()
            if name and name in contact_name:
                return contact
    return contacts[0]


def _build_visit_detail_link(base_url: str, visit_id: str | None) -> str | None:
    if not visit_id:
        return None
    return f"{base_url.rstrip('/')}/return-visit/detail/{visit_id}"


def _select_latest_visit(items: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not items:
        return None
    return sorted(items, key=lambda item: str(item.get("created_at") or ""), reverse=True)[0]


def _utc_now_z() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _build_me_query() -> str:
    return """
    query {
      me {
        id
        name
      }
    }
    """


def _build_delivery_meta_query(delivery_id: str) -> str:
    return f"""
    query {{
      list_product_delivery(
        search:{{id:{json.dumps(delivery_id)}}},
        pagination:{{skip:0,limit:10}},
        SortBy:{{by:"updated_at",sort:-1}}
      ) {{
        total
        data {{
          id
          project {{
            id
            name
            company {{
              id
              name
              contact {{
                id
                name
                area_code
                phone
                email
                duty
                meta
              }}
            }}
            product_detail_list {{
              product {{
                id
                name
              }}
              form {{
                id
                name
              }}
            }}
          }}
          visit_data {{
            visit_finished
          }}
        }}
      }}
    }}
    """


def _build_create_visit_mutation(
    *,
    company_id: str,
    visitor_id: str,
    visit_type: str,
    contact_id: str,
    product_id: str,
    form_id: str,
    delivery_id: str,
) -> str:
    return f"""
    mutation {{
      create_visit(input:{{
        company_id:{json.dumps(company_id)},
        visitor_id:{json.dumps(visitor_id)},
        type:{visit_type},
        contact_list:[{{contact_id:{json.dumps(contact_id)}, visit_object:true, note:""}}],
        content_list:[{{
          product_detail:{{product:{json.dumps(product_id)}, form:{json.dumps(form_id)}}},
          delivery_list:[{{delivery_id:{json.dumps(delivery_id)}, delivery_type:product_delivery}}]
        }}]
      }})
    }}
    """


def _build_list_visit_query(company_id: str, delivery_id: str, visitor_id: str) -> str:
    return f"""
    query {{
      list_visit(
        search:{{
          company_id:{json.dumps(company_id)},
          delivery_id:{json.dumps(delivery_id)},
          visitor_ids:[{json.dumps(visitor_id)}],
          finished:false
        }},
        pagination:{{skip:0,limit:20}}
      ) {{
        total
        data {{
          id
          type
          finished
          created_at
          company {{
            id
            name
          }}
          visitor {{
            id
            name
          }}
        }}
      }}
    }}
    """


def _build_visit_detail_query(visit_id: str) -> str:
    return f"""
    query {{
      visit_detail(id:{json.dumps(visit_id)}) {{
        id
        type
        finished
        company {{
          id
          name
        }}
        visitor {{
          id
          name
        }}
        contact_list {{
          contact {{
            id
            name
            area_code
            phone
            email
            duty
            meta
          }}
          visit_object
          note
        }}
        content_list {{
          id
          score
          feedback_note
          product_detail {{
            product {{
              id
              name
            }}
            form {{
              id
              name
            }}
          }}
          delivery_list {{
            delivery_id
            delivery_type
            project {{
              id
              name
              company {{
                id
                name
              }}
            }}
          }}
        }}
      }}
    }}
    """


def _build_process_visit_mutation(
    *,
    visit_id: str,
    contact_id: str,
    content_id: str,
    score: str,
    feedback_note: str,
    visit_time: str,
) -> str:
    return f"""
    mutation {{
      process_visit(
        id:{json.dumps(visit_id)},
        contact_list:[{{contact_id:{json.dumps(contact_id)}, visit_object:true, note:""}}],
        way:phone,
        visit_time:{json.dumps(visit_time)},
        content_list:[{{id:{json.dumps(content_id)}, score:{score}, feedback_note:{json.dumps(feedback_note)}}}]
      )
    }}
    """


def _build_finish_visit_mutation(visit_id: str) -> str:
    return f"""
    mutation {{
      finish_visit(id:{json.dumps(visit_id)})
    }}
    """
