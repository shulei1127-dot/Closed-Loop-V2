from __future__ import annotations

import asyncio
import json
import subprocess
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import parse_qs, urlparse

from core.config import Settings, get_settings
from services.collectors.dingtalk_parallelv2_decoder import (
    DingtalkDocumentStructure,
    parse_document_data_structure,
)
from services.collectors.fetchers import DingtalkPayloadFetcher
from services.collectors.source_config import ModuleSourceConfig
from services.executors.schemas import ExecutorContext


class DingtalkVisitWritebackError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        error_type: str = "unknown_error",
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_type = error_type
        self.retryable = retryable


class DingtalkVisitWritebackService:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        fetcher: DingtalkPayloadFetcher | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.fetcher = fetcher or DingtalkPayloadFetcher()

    async def write_visit_link(self, *, context: ExecutorContext, final_link: str) -> dict[str, Any]:
        source_config = self._build_source_config(context)
        if source_config is None:
            return {
                "action": "writeback_visit_link_to_dingtalk",
                "status": "skipped",
                "http_status": None,
                "retryable": False,
                "error_message": None,
                "error_type": None,
                "writeback_mode": "disabled",
            }

        try:
            structure = await self._load_structure(source_config)
            target_row_id = context.source_row_id
            if not target_row_id:
                raise DingtalkVisitWritebackError(
                    "缺少 source_row_id，无法精确回写钉钉文档",
                    error_type="response_invalid",
                    retryable=False,
                )
            target_field_id = self._find_field_id(structure, "回访链接")
            customer_field_id = self._find_field_id(structure, "客户名称")
            target_row_index = self._find_row_index(structure, target_row_id)
            document_url = self._resolve_document_url(source_config)
            async with _shared_chrome_dingtalk_session() as chrome:
                await chrome.open_url(document_url)
                await chrome.wait_until_ready()
                await chrome.focus_grid(customer_field_id)
                await chrome.navigate_to_row(
                    target_row_id=target_row_id,
                    target_row_index=target_row_index,
                    record_ids=structure.record_ids,
                    customer_field_id=customer_field_id,
                )
                await chrome.select_cell(target_row_id=target_row_id, field_id=customer_field_id)
                await chrome.move_selection_to_field(target_field_id=target_field_id)
                await chrome.open_link_editor()
                await chrome.fill_link_editor(title=final_link, url=final_link)
                await chrome.save_link_editor()
                await chrome.assert_cell_contains(
                    target_row_id=target_row_id,
                    target_field_id=target_field_id,
                    expected=final_link,
                )
                await chrome.close_link_editor_if_visible()
            return {
                "action": "writeback_visit_link_to_dingtalk",
                "status": "success",
                "http_status": 200,
                "retryable": False,
                "error_message": None,
                "error_type": None,
                "source_row_id": target_row_id,
                "field_name": "回访链接",
                "field_id": target_field_id,
                "visit_link": final_link,
                "writeback_mode": "chrome_browser_session",
                "document_url": document_url,
                "row_index": target_row_index,
            }
        except DingtalkVisitWritebackError as exc:
            return {
                "action": "writeback_visit_link_to_dingtalk",
                "status": "failed",
                "http_status": None,
                "retryable": exc.retryable,
                "error_message": exc.message,
                "error_type": exc.error_type,
                "source_row_id": context.source_row_id,
                "field_name": "回访链接",
                "visit_link": final_link,
                "writeback_mode": "chrome_browser_session",
            }

    def _build_source_config(self, context: ExecutorContext) -> ModuleSourceConfig | None:
        if context.module_code != "visit":
            return None
        if context.source_collector_type not in {"dingtalk", "real"}:
            return None
        if not context.source_url or not context.source_doc_key:
            return None
        return ModuleSourceConfig.from_mapping(
            {
                "module_code": context.module_code,
                "module_name": context.module_code,
                "source_url": context.source_url,
                "source_doc_key": context.source_doc_key,
                "source_view_key": context.source_view_key,
                "collector_type": context.source_collector_type,
                "extra_config": context.source_extra_config,
            }
        )

    async def _load_structure(self, source_config: ModuleSourceConfig) -> DingtalkDocumentStructure:
        document_endpoint = source_config.get_extra("structured_endpoint")
        sheet_id = str(source_config.get_extra("parallelv2_sheet_id") or "")
        view_id = str(source_config.get_extra("parallelv2_view_id") or source_config.source_view_key or "")
        if not document_endpoint or not sheet_id or not view_id:
            raise DingtalkVisitWritebackError(
                "钉钉文档结构配置缺失，无法回写回访链接",
                error_type="config_missing",
                retryable=False,
            )
        try:
            request = self.fetcher._build_request(source_config, step="structured", endpoint=str(document_endpoint))
            response = await self.fetcher._send_request(step="structured", request=request)
            payload = response.json()
        except Exception as exc:  # pragma: no cover - defensive glue
            raise DingtalkVisitWritebackError(
                f"加载钉钉文档结构失败: {exc}",
                error_type="request_failed",
                retryable=True,
            ) from exc
        if not isinstance(payload, dict):
            raise DingtalkVisitWritebackError(
                "钉钉文档结构响应非法",
                error_type="response_invalid",
                retryable=False,
            )
        try:
            return parse_document_data_structure(payload, sheet_id=sheet_id, view_id=view_id)
        except Exception as exc:
            raise DingtalkVisitWritebackError(
                f"解析钉钉文档结构失败: {exc}",
                error_type="response_invalid",
                retryable=False,
            ) from exc

    @staticmethod
    def _find_field_id(structure: DingtalkDocumentStructure, field_name: str) -> str:
        for field_id, current_name in structure.field_name_by_id.items():
            if current_name == field_name:
                return field_id
        raise DingtalkVisitWritebackError(
            f"钉钉文档缺少字段 `{field_name}`",
            error_type="response_invalid",
            retryable=False,
        )

    @staticmethod
    def _find_row_index(structure: DingtalkDocumentStructure, target_row_id: str) -> int:
        try:
            return structure.record_ids.index(target_row_id)
        except ValueError as exc:
            raise DingtalkVisitWritebackError(
                f"钉钉文档中未找到目标行 `{target_row_id}`",
                error_type="response_invalid",
                retryable=False,
            ) from exc

    @staticmethod
    def _resolve_document_url(source_config: ModuleSourceConfig) -> str:
        headers = source_config.get_extra("structured_headers", {})
        if isinstance(headers, dict):
            referer = headers.get("Referer") or headers.get("referer")
            if isinstance(referer, str) and referer:
                return referer
        return source_config.source_url


class _ChromeDingtalkSession:
    def __init__(self) -> None:
        self._current_document_url: str | None = None

    async def __aenter__(self) -> "_ChromeDingtalkSession":
        running = await self._run_applescript(
            """
            tell application "Google Chrome"
              return running
            end tell
            """
        )
        if str(running).strip().lower() != "true":
            raise DingtalkVisitWritebackError(
                "请先打开 Google Chrome 并登录钉钉文档",
                error_type="session_expired",
                retryable=False,
            )
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def open_url(self, target_url: str) -> None:
        normalized_target = target_url.strip()
        if self._current_document_url == normalized_target:
            try:
                active_url = await self.get_active_url()
            except DingtalkVisitWritebackError:
                active_url = ""
            if isinstance(active_url, str) and active_url.startswith(normalized_target):
                return
        doc_key, sheet_id, view_id = _extract_dingtalk_document_fingerprint(normalized_target)
        match_checks: list[str] = []
        if doc_key:
            match_checks.append(f'currentUrl contains {json.dumps(f"docKey={doc_key}")}')
        if sheet_id:
            match_checks.append(f'currentUrl contains {json.dumps(f"sheetId={sheet_id}")}')
        if view_id:
            match_checks.append(f'currentUrl contains {json.dumps(f"viewId={view_id}")}')
        match_condition = " and ".join(match_checks) if match_checks else f'currentUrl starts with targetUrl'
        await self._run_applescript(
            f'''
            tell application "Google Chrome"
              activate
              if (count of windows) = 0 then make new window
              set targetUrl to {json.dumps(normalized_target)}
              set matchedWindowIndex to 0
              set matchedTabIndex to 0
              repeat with windowIndex from 1 to count of windows
                tell window windowIndex
                  repeat with tabIndex from 1 to count of tabs
                    set currentUrl to URL of tab tabIndex
                    if {match_condition} then
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
                end tell
              end if
              delay 1.5
            end tell
            '''
        )
        self._current_document_url = normalized_target

    async def wait_until_ready(self) -> None:
        async def _ready() -> bool:
            data = await self._execute_js(
                """
                JSON.stringify({
                  ready: document.readyState,
                  hasGrid: Boolean(document.querySelector('[data-row-id][data-field-id]')),
                  url: window.location.href
                })
                """
            )
            return data.get("ready") == "complete" and bool(data.get("hasGrid")) and "alidocs.dingtalk.com" in str(data.get("url") or "")

        await self._wait_for(_ready, "钉钉文档页面未就绪")
        await self._wait_for(
            self._has_visible_rows,
            "钉钉文档当前没有可见数据行",
            timeout=12.0,
            interval=0.3,
        )

    async def ensure_column_visible(self, target_field_id: str) -> bool:
        async def _field_visible() -> bool:
            visible = await self._visible_field_ids()
            return target_field_id in visible

        if await _field_visible():
            return True
        await self._execute_js(
            """
            (function() {
              const scroller = document.querySelector('.zscroller-scrollbar-x.scroller-x');
              if (!scroller) return JSON.stringify({ok:false});
              scroller.scrollLeft = scroller.scrollWidth;
              scroller.dispatchEvent(new Event('scroll', {bubbles:true}));
              return JSON.stringify({ok:true, scrollLeft: scroller.scrollLeft});
            })()
            """
        )
        try:
            await self._wait_for(_field_visible, "未能滚动到回访链接列", timeout=2.0, interval=0.15)
            return True
        except DingtalkVisitWritebackError:
            return False

    async def focus_grid(self, customer_field_id: str) -> None:
        await self._wait_for(
            self._has_visible_rows,
            "钉钉文档当前没有可见数据行",
            timeout=12.0,
            interval=0.3,
        )
        visible_rows = await self._visible_rows()
        if not visible_rows:
            raise DingtalkVisitWritebackError("钉钉文档当前没有可见数据行", error_type="response_invalid")
        first_row_id = visible_rows[0]["rowId"]
        await self.select_cell(target_row_id=first_row_id, field_id=customer_field_id)

    async def navigate_to_row(
        self,
        *,
        target_row_id: str,
        target_row_index: int,
        record_ids: list[str],
        customer_field_id: str,
    ) -> None:
        async def _row_visible() -> bool:
            visible = await self._visible_rows()
            row_ids = {item["rowId"] for item in visible}
            return target_row_id in row_ids

        for _ in range(24):
            if await _row_visible():
                return
            visible = await self._visible_rows()
            if not visible:
                await self._send_key_code_repeated(121, 1)
                await asyncio.sleep(0.15)
                continue
            visible_indices = [record_ids.index(item["rowId"]) for item in visible if item["rowId"] in record_ids]
            if not visible_indices:
                await self._send_key_code_repeated(121, 1)
                await asyncio.sleep(0.15)
                continue
            min_index = min(visible_indices)
            max_index = max(visible_indices)
            page_size = max(1, max_index - min_index + 1)
            if target_row_index < min_index:
                gap = min_index - target_row_index
                batch = min(10, max(1, gap // page_size))
                await self._send_key_code_repeated(116, batch)
            elif target_row_index > max_index:
                gap = target_row_index - max_index
                batch = min(10, max(1, gap // page_size))
                await self._send_key_code_repeated(121, batch)
            else:
                await self._send_key_code_repeated(121, 1)
            await asyncio.sleep(0.18)
            refreshed = await self._visible_rows()
            if refreshed:
                try:
                    await self.select_cell(target_row_id=refreshed[0]["rowId"], field_id=customer_field_id)
                except DingtalkVisitWritebackError:
                    pass
        raise DingtalkVisitWritebackError(
            f"未能导航到目标行 `{target_row_id}`",
            error_type="unknown_error",
            retryable=False,
        )

    async def select_cell(self, *, target_row_id: str, field_id: str) -> None:
        result = await self._execute_js(
            f"""
            (function() {{
              const selector = '[data-row-id="{target_row_id}"][data-field-id="{field_id}"]';
              const cell = document.querySelector(selector);
              if (!cell) return JSON.stringify({{ok:false}});
              cell.scrollIntoView({{block:'center', inline:'center'}});
              const rect = cell.getBoundingClientRect();
              const cx = rect.left + rect.width / 2;
              const cy = rect.top + rect.height / 2;
              const fire = (type) => cell.dispatchEvent(new MouseEvent(type, {{
                bubbles: true,
                cancelable: true,
                view: window,
                clientX: cx,
                clientY: cy,
                button: 0,
                buttons: 1,
              }}));
              fire('pointerdown');
              fire('mousedown');
              fire('pointerup');
              fire('mouseup');
              fire('click');
              if (typeof cell.focus === 'function') cell.focus();
              return JSON.stringify({{ok:true}});
            }})()
            """
        )
        if not result.get("ok"):
            raise DingtalkVisitWritebackError(
                f"未找到目标单元格 row={target_row_id}",
                error_type="response_invalid",
                retryable=False,
            )
        async def _selected() -> bool:
            selected = await self._selected_cell()
            return selected.get("row") == target_row_id and selected.get("field") == field_id
        await self._wait_for(
            _selected,
            f"未能选中目标单元格 row={target_row_id}",
            timeout=4.0,
            interval=0.1,
        )

    async def move_selection_to_field(self, *, target_field_id: str) -> None:
        for _ in range(24):
            selected = await self._selected_cell()
            if selected.get("field") == target_field_id:
                return
            await self._send_key_code(124)
            await asyncio.sleep(0.08)
        raise DingtalkVisitWritebackError(
            "未能选中回访链接列",
            error_type="unknown_error",
            retryable=False,
        )

    async def open_link_editor(self) -> None:
        await self._send_key_code(36)
        await self._wait_for(self._link_editor_visible, "未能打开钉钉文档链接编辑器")

    async def fill_link_editor(self, *, title: str, url: str) -> None:
        payload = json.dumps({"title": title, "url": url}, ensure_ascii=False)
        result = await self._execute_js(
            f"""
            (function() {{
              const payload = {payload};
              const inputs = Array.from(document.querySelectorAll('input'));
              const titleInput = inputs.find((item) => (item.placeholder || '').includes('链接标题'));
              const urlInput = inputs.find((item) => (item.placeholder || '').includes('链接地址'));
              if (!titleInput || !urlInput) return JSON.stringify({{ok:false}});
              const setValue = (input, value) => {{
                const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                setter.call(input, value);
                input.dispatchEvent(new Event('input', {{bubbles:true}}));
                input.dispatchEvent(new Event('change', {{bubbles:true}}));
              }};
              setValue(titleInput, payload.title);
              setValue(urlInput, payload.url);
              return JSON.stringify({{
                ok:true,
                titleValue: titleInput.value,
                urlValue: urlInput.value
              }});
            }})()
            """
        )
        if not result.get("ok") or result.get("titleValue") != title or result.get("urlValue") != url:
            raise DingtalkVisitWritebackError(
                "未找到钉钉文档链接编辑输入框",
                error_type="response_invalid",
                retryable=False,
            )

    async def save_link_editor(self) -> None:
        await self._send_key_code(36)
        await asyncio.sleep(0.5)
        if await self._link_editor_visible():
            await self._execute_js(
                """
                (function() {
                  const inputs = Array.from(document.querySelectorAll('input'));
                  const titleInput = inputs.find((item) => (item.placeholder || '').includes('链接标题'));
                  const container = titleInput?.closest('[role="dialog"]')
                    || titleInput?.closest('[data-testid]')
                    || titleInput?.parentElement?.parentElement?.parentElement?.parentElement
                    || document;
                  const buttons = Array.from(container.querySelectorAll('button'));
                  const target = buttons.find((item) => {
                    const text = (item.textContent || '').trim();
                    return text === '保存' || text === '确定' || text === '完成' || text === '确认';
                  });
                  if (target) {
                    target.click();
                    return JSON.stringify({ok:true});
                  }
                  return JSON.stringify({ok:false});
                })()
                """
            )
            await asyncio.sleep(0.4)

    async def close_link_editor_if_visible(self) -> None:
        if await self._link_editor_visible():
            await self._send_key_code(53)
            await asyncio.sleep(0.2)

    async def assert_cell_contains(
        self,
        *,
        target_row_id: str,
        target_field_id: str,
        expected: str,
    ) -> None:
        async def _matches() -> bool:
            result = await self._execute_js(
                f"""
                (function() {{
                  const cell = document.querySelector('[data-row-id="{target_row_id}"][data-field-id="{target_field_id}"]');
                  if (!cell) return JSON.stringify({{ok:false, text:''}});
                  const href = cell.querySelector('a')?.href || '';
                  const text = cell.innerText || '';
                  return JSON.stringify({{ok: href.includes({json.dumps(expected)}) || text.includes({json.dumps(expected)}), href, text}});
                }})()
                """
            )
            return bool(result.get("ok"))

        await self._wait_for(_matches, "钉钉文档回访链接写回后未能验证")

    async def _visible_rows(self) -> list[dict[str, Any]]:
        result = await self._execute_js(
            """
            (function() {
              const map = new Map();
              const isVisible = (node) => {
                const rect = node.getBoundingClientRect();
                return rect.width > 0 && rect.height > 0;
              };
              Array.from(document.querySelectorAll('[data-row-id]')).forEach((node) => {
                const rowId = node.getAttribute('data-row-id');
                const owner = node.closest('[aria-rowindex]') || node;
                const rowIndex = Number(owner.getAttribute('aria-rowindex') || 0);
                if (!rowId || map.has(rowId) || !isVisible(node)) return;
                map.set(rowId, {rowId, rowIndex});
              });
              return JSON.stringify(Array.from(map.values()).sort((a, b) => a.rowIndex - b.rowIndex));
            })()
            """
        )
        if isinstance(result, list):
            return result
        return []

    async def _has_visible_rows(self) -> bool:
        return bool(await self._visible_rows())

    async def _visible_field_ids(self) -> list[str]:
        result = await self._execute_js(
            """
            JSON.stringify(Array.from(new Set(Array.from(document.querySelectorAll('[data-field-id]')).map((node) => node.getAttribute('data-field-id')).filter(Boolean))))
            """
        )
        if isinstance(result, list):
            return [str(item) for item in result]
        return []

    async def _selected_cell(self) -> dict[str, Any]:
        result = await self._execute_js(
            """
            (function() {
              const selected = document.querySelector('[data-in-selected-area="true"][data-row-id][data-field-id]');
              if (!selected) return JSON.stringify({});
              return JSON.stringify({
                row: selected.getAttribute('data-row-id'),
                field: selected.getAttribute('data-field-id')
              });
            })()
            """
        )
        if isinstance(result, dict):
            return result
        return {}

    async def _link_editor_visible(self) -> bool:
        result = await self._execute_js(
            """
            (function() {
              const inputs = Array.from(document.querySelectorAll('input'));
              const titleInput = inputs.find((item) => (item.placeholder || '').includes('链接标题'));
              const urlInput = inputs.find((item) => (item.placeholder || '').includes('链接地址'));
              return JSON.stringify({ok: Boolean(titleInput && urlInput)});
            })()
            """
        )
        return bool(result.get("ok"))


    async def _execute_js(self, script: str) -> Any:
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

    async def get_active_url(self) -> str:
        return await self._run_applescript(
            '''
            tell application "Google Chrome"
              return URL of active tab of front window
            end tell
            '''
        )

    async def _send_key_code(self, key_code: int) -> None:
        await self._run_applescript(
            f'''
            tell application "Google Chrome" to activate
            tell application "System Events"
              key code {key_code}
            end tell
            '''
        )

    async def _send_key_code_repeated(self, key_code: int, count: int) -> None:
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

    async def _wait_for(self, predicate, message: str, *, timeout: float = 10.0, interval: float = 0.25) -> None:
        started = asyncio.get_running_loop().time()
        while True:
            if await predicate():
                return
            if asyncio.get_running_loop().time() - started > timeout:
                raise DingtalkVisitWritebackError(message, error_type="unknown_error", retryable=False)
            await asyncio.sleep(interval)

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
            detail = (result.stderr or result.stdout or "").strip()
            message = "无法驱动本机 Chrome 会话，请检查浏览器自动化权限"
            if detail:
                message = f"{message}: {detail}"
            raise DingtalkVisitWritebackError(
                message,
                error_type="unknown_error",
                retryable=False,
            )
        return result.stdout.strip()


_shared_chrome_session: _ChromeDingtalkSession | None = None
_shared_chrome_session_lock: asyncio.Lock | None = None
_shared_chrome_session_lock_loop: asyncio.AbstractEventLoop | None = None


@asynccontextmanager
async def _shared_chrome_dingtalk_session():
    global _shared_chrome_session, _shared_chrome_session_lock, _shared_chrome_session_lock_loop
    current_loop = asyncio.get_running_loop()
    if _shared_chrome_session_lock is None or _shared_chrome_session_lock_loop is not current_loop:
        _shared_chrome_session_lock = asyncio.Lock()
        _shared_chrome_session_lock_loop = current_loop
    async with _shared_chrome_session_lock:
        if _shared_chrome_session is None:
            _shared_chrome_session = _ChromeDingtalkSession()
        session = _shared_chrome_session
        await session.__aenter__()
        try:
            yield session
        finally:
            await session.__aexit__(None, None, None)


def _extract_dingtalk_document_fingerprint(target_url: str) -> tuple[str | None, str | None, str | None]:
    parsed = urlparse(target_url)
    query = parse_qs(parsed.query)
    doc_key = query.get("docKey", [None])[0]
    sheet_id = query.get("sheetId", [None])[0]
    view_id = query.get("viewId", [None])[0]
    return doc_key, sheet_id, view_id
