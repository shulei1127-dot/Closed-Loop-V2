from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from core.config import Settings


PROJECT_ROOT = Path(__file__).resolve().parents[1]
AUTH_LOGIN_MARKER = "auth.chaitin.net/login"


class PtsBrowserProfileError(Exception):
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


@dataclass
class _ProfileState:
    playwright: Any
    context: Any
    page: Any
    profile_dir: Path
    launch_key: tuple[Any, ...]


_LOCKS: dict[int, asyncio.Lock] = {}
_STATES: dict[int, _ProfileState] = {}


def pts_browser_profile_enabled(settings: Settings) -> bool:
    transport = str(getattr(settings, "pts_execution_transport", "auto") or "auto").strip().lower()
    return bool(getattr(settings, "pts_browser_profile_enabled", True)) and transport in {"auto", "browser_profile"}


def pts_browser_profile_configured(settings: Settings) -> bool:
    if not pts_browser_profile_enabled(settings):
        return False
    profile_dir = resolve_pts_profile_dir(settings)
    if not profile_dir.exists():
        return False
    markers = (
        profile_dir / "Default" / "Network" / "Cookies",
        profile_dir / "Default" / "Cookies",
        profile_dir / "Default" / "Preferences",
        profile_dir / "Local State",
    )
    return any(marker.exists() for marker in markers)


def pts_browser_profile_required(settings: Settings) -> bool:
    transport = str(getattr(settings, "pts_execution_transport", "auto") or "auto").strip().lower()
    return bool(getattr(settings, "pts_browser_profile_enabled", True)) and transport == "browser_profile"


def pts_direct_http_enabled(settings: Settings) -> bool:
    transport = str(getattr(settings, "pts_execution_transport", "auto") or "auto").strip().lower()
    if transport == "browser_profile":
        return False
    if transport == "cookie_direct":
        return True
    return bool(getattr(settings, "pts_direct_http_enabled", True))


def resolve_pts_profile_dir(settings: Settings) -> Path:
    configured = str(getattr(settings, "pts_browser_profile_dir", "") or ".pts-browser-profile/chrome-profile").strip()
    path = Path(configured).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def _current_loop_key() -> int:
    return id(asyncio.get_running_loop())


def _lock_for_current_loop() -> asyncio.Lock:
    loop_key = _current_loop_key()
    lock = _LOCKS.get(loop_key)
    if lock is None:
        lock = asyncio.Lock()
        _LOCKS[loop_key] = lock
    return lock


class PtsBrowserProfileSession:
    """PTS same-origin browser session backed by a stable Playwright profile."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.profile_dir = resolve_pts_profile_dir(settings)
        self._lock: asyncio.Lock | None = None
        self._loop_key: int | None = None
        self._state: _ProfileState | None = None

    async def __aenter__(self) -> "PtsBrowserProfileSession":
        if not pts_browser_profile_enabled(self.settings):
            raise PtsBrowserProfileError(
                error_message="PTS 浏览器 profile 传输未启用",
                error_type="config_missing",
                retryable=False,
            )
        self._loop_key = _current_loop_key()
        self._lock = _lock_for_current_loop()
        await self._lock.acquire()
        try:
            self._state = await self._ensure_state()
        except Exception:
            self._lock.release()
            self._lock = None
            raise
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._state and not bool(getattr(self.settings, "pts_browser_context_reuse_enabled", True)):
            await self._close_state(self._loop_key)
        self._state = None
        if self._lock is not None:
            self._lock.release()
            self._lock = None

    async def open_project(self, target: str | None) -> dict[str, Any]:
        page = await self._page()
        target_url = str(target or "").strip() or f"{self.settings.pts_base_url.rstrip('/')}/project"
        try:
            response = await page.goto(target_url, wait_until="domcontentloaded", timeout=self._timeout_ms())
        except Exception as exc:
            return {
                "action": "open_pts_delivery_link",
                "status": "failed",
                "target": target_url,
                "error_type": "timeout",
                "error_message": f"打开 PTS 链接失败: {exc}",
                "retryable": True,
            }
        current_url = str(page.url or "")
        http_status = response.status if response is not None else 200
        if AUTH_LOGIN_MARKER in current_url or http_status in {401, 403}:
            return {
                "action": "open_pts_delivery_link",
                "status": "failed",
                "target": target_url,
                "http_status": http_status,
                "error_type": "session_expired",
                "error_message": "PTS 浏览器 profile 未登录或会话已失效，请先运行登录脚本",
                "retryable": False,
            }
        if http_status and http_status >= 400:
            return {
                "action": "open_pts_delivery_link",
                "status": "failed",
                "target": target_url,
                "http_status": http_status,
                "error_type": "http_error" if http_status >= 500 else "business_rejected",
                "error_message": f"打开 PTS 链接失败: {http_status}",
                "retryable": http_status >= 500,
            }
        return {
            "action": "open_pts_delivery_link",
            "status": "success",
            "target": target_url,
            "http_status": http_status,
            "transport_mode": "pts_browser_profile",
        }

    async def graphql(self, query: str) -> dict[str, Any]:
        return await self.graphql_payload({"query": query})

    async def graphql_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        page = await self._page()
        await self._ensure_pts_origin(page)
        try:
            result = await page.evaluate(
                """
                async ({ payload }) => {
                  try {
                    const response = await fetch('/query', {
                      method: 'POST',
                      credentials: 'include',
                      headers: {
                        'Content-Type': 'application/json',
                        'Accept': '*/*'
                      },
                      body: JSON.stringify(payload)
                    });
                    return {
                      status: response.status,
                      responseURL: response.url || '',
                      text: await response.text(),
                      url: window.location.href
                    };
                  } catch (error) {
                    return {
                      status: 0,
                      error: String(error),
                      responseURL: '',
                      text: '',
                      url: window.location.href
                    };
                  }
                }
                """,
                {"payload": payload},
            )
        except Exception as exc:
            raise PtsBrowserProfileError(
                error_message=f"PTS 浏览器 profile 执行 GraphQL 失败: {exc}",
                error_type="unknown_error",
                retryable=True,
            ) from exc

        if not isinstance(result, dict):
            raise PtsBrowserProfileError(
                error_message="PTS 浏览器 profile 返回非法结果",
                error_type="response_invalid",
                retryable=False,
            )
        status = int(result.get("status") or 0)
        url = str(result.get("url") or "")
        response_url = str(result.get("responseURL") or "")
        text = str(result.get("text") or "")
        if AUTH_LOGIN_MARKER in url or AUTH_LOGIN_MARKER in response_url or status in {401, 403}:
            raise PtsBrowserProfileError(
                error_message="PTS 浏览器 profile 未登录或会话已失效，请先运行登录脚本",
                error_type="session_expired",
                retryable=False,
                http_status=status or None,
            )
        if status == 0:
            raise PtsBrowserProfileError(
                error_message=str(result.get("error") or "PTS GraphQL 请求未发出"),
                error_type="timeout",
                retryable=True,
            )
        if status >= 400:
            raise PtsBrowserProfileError(
                error_message=f"PTS GraphQL 请求失败: {status}",
                error_type="http_error" if status >= 500 else "business_rejected",
                retryable=status >= 500,
                http_status=status,
            )
        try:
            response_payload = json.loads(text)
        except ValueError as exc:
            raise PtsBrowserProfileError(
                error_message="PTS GraphQL 返回非法 JSON",
                error_type="response_invalid",
                retryable=False,
            ) from exc
        return _extract_graphql_data(response_payload)

    async def execute_js_on_project_background(self, target: str, script: str) -> Any:
        page = await self._page()
        await self.open_project(target)
        try:
            result = await page.evaluate(script)
        except Exception as exc:
            raise PtsBrowserProfileError(
                error_message=f"PTS 页面脚本执行失败: {exc}",
                error_type="unknown_error",
                retryable=True,
            ) from exc
        return _decode_possible_json(result)

    async def read_page_text(self, *, limit: int = 4000) -> str:
        page = await self._page()
        result = await page.evaluate(
            "(limit) => document.body ? document.body.innerText.slice(0, limit) : ''",
            int(limit),
        )
        return str(result or "")

    async def _ensure_state(self) -> _ProfileState:
        loop_key = _current_loop_key()
        launch_key = self._launch_key()
        state = _STATES.get(loop_key)
        if state is not None and state.launch_key == launch_key:
            try:
                if not state.page.is_closed():
                    return state
            except Exception:
                await self._close_state(loop_key)
            await self._close_state(loop_key)
        elif state is not None:
            await self._close_state(loop_key)

        self.profile_dir.mkdir(parents=True, exist_ok=True)
        playwright = None
        try:
            from playwright.async_api import async_playwright

            playwright = await async_playwright().start()
            launch_kwargs: dict[str, Any] = {
                "headless": bool(getattr(self.settings, "pts_browser_headless", True)),
                "ignore_https_errors": not bool(getattr(self.settings, "pts_verify_ssl", True)),
                "viewport": {"width": 1440, "height": 1000},
            }
            channel = str(getattr(self.settings, "pts_browser_channel", "") or "").strip()
            if channel:
                launch_kwargs["channel"] = channel
            context = await self._launch_persistent_context(playwright, launch_kwargs)
        except Exception as exc:
            if playwright is not None:
                try:
                    await playwright.stop()
                except Exception:
                    pass
            raise PtsBrowserProfileError(
                error_message=f"启动 PTS 浏览器 profile 失败: {exc}",
                error_type="browser_unavailable",
                retryable=False,
            ) from exc

        page = context.pages[0] if context.pages else await context.new_page()
        state = _ProfileState(
            playwright=playwright,
            context=context,
            page=page,
            profile_dir=self.profile_dir,
            launch_key=self._launch_key(),
        )
        _STATES[loop_key] = state
        return state

    async def _launch_persistent_context(self, playwright: Any, launch_kwargs: dict[str, Any]) -> Any:
        try:
            return await playwright.chromium.launch_persistent_context(str(self.profile_dir), **launch_kwargs)
        except Exception as exc:
            if launch_kwargs.get("channel") or "Executable doesn't exist" not in str(exc):
                raise
            chrome_kwargs = {**launch_kwargs, "channel": "chrome"}
            try:
                return await playwright.chromium.launch_persistent_context(str(self.profile_dir), **chrome_kwargs)
            except Exception:
                raise exc

    async def _ensure_pts_origin(self, page: Any) -> None:
        current_url = str(page.url or "")
        base_origin = self.settings.pts_base_url.rstrip("/")
        if current_url.startswith(base_origin) and AUTH_LOGIN_MARKER not in current_url:
            return
        open_result = await self.open_project(f"{base_origin}/project")
        if open_result.get("status") != "success":
            raise PtsBrowserProfileError(
                error_message=str(open_result.get("error_message") or "PTS 浏览器 profile 未登录"),
                error_type=str(open_result.get("error_type") or "session_expired"),
                retryable=bool(open_result.get("retryable")),
                http_status=open_result.get("http_status"),
            )

    async def _page(self) -> Any:
        if self._state is None:
            self._state = await self._ensure_state()
        if self._state.page.is_closed():
            self._state.page = await self._state.context.new_page()
        return self._state.page

    async def _close_state(self, loop_key: int | None) -> None:
        if loop_key is None:
            return
        state = _STATES.pop(loop_key, None)
        if state is None:
            return
        try:
            await state.context.close()
        finally:
            await state.playwright.stop()

    def _launch_key(self) -> tuple[Any, ...]:
        return (
            str(self.profile_dir),
            bool(getattr(self.settings, "pts_browser_headless", True)),
            str(getattr(self.settings, "pts_browser_channel", "") or "").strip(),
            bool(getattr(self.settings, "pts_verify_ssl", True)),
        )

    def _timeout_ms(self) -> int:
        seconds = float(getattr(self.settings, "visit_real_timeout_seconds", 15.0) or 15.0)
        return max(1000, int(seconds * 1000))


def _decode_possible_json(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _extract_graphql_data(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise PtsBrowserProfileError(
            error_message="PTS GraphQL 返回不是对象",
            error_type="response_invalid",
            retryable=False,
        )
    errors = payload.get("errors") or []
    if errors:
        message = errors[0].get("message") if isinstance(errors[0], dict) else str(errors[0])
        raise PtsBrowserProfileError(
            error_message=str(message or "PTS GraphQL 返回错误"),
            error_type="business_rejected",
            retryable=False,
        )
    data = payload.get("data")
    if not isinstance(data, dict):
        raise PtsBrowserProfileError(
            error_message="PTS GraphQL 缺少 data 字段",
            error_type="response_invalid",
            retryable=False,
        )
    return data
