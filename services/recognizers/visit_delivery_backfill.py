from __future__ import annotations

import json
import re
import shutil
import tempfile
from typing import Any
from urllib.parse import urlparse, urlunparse
from pathlib import Path

import httpx

from core.config import Settings, get_settings


PROJECT_ID_PATTERN = re.compile(r"/project/([0-9a-f]{24})", re.IGNORECASE)
DELIVERY_ID_TEXT_PATTERNS = [
    re.compile(r'"deliveryId"\s*:\s*"([^"]+)"'),
    re.compile(r'"delivery_id"\s*:\s*"([^"]+)"'),
]


class VisitDeliveryIdBackfill:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._delivery_id_cache: dict[str, tuple[str | None, str, str | None]] = {}
        self._pts_auth_available: bool | None = None

    async def enrich_records(self, normalized_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not normalized_records:
            return normalized_records

        async with httpx.AsyncClient(timeout=15.0, verify=self.settings.pts_verify_ssl, follow_redirects=True) as client:
            for item in normalized_records:
                data = item.get("normalized_data", {})
                if not isinstance(data, dict):
                    continue
                self._populate_debug_defaults(data)
                if data.get("delivery_id"):
                    data["debug_delivery_id_source"] = "raw_field"
                    data["debug_delivery_id_raw"] = data.get("delivery_id")
                    data["debug_delivery_id_normalized"] = data.get("delivery_id")
                    continue
                pts_link = data.get("pts_link")
                if not isinstance(pts_link, str) or not pts_link:
                    data["debug_delivery_id_source"] = "pts_link_missing"
                    continue

                project_id = extract_pts_project_id(pts_link)
                data["debug_pts_project_id"] = project_id
                if not project_id:
                    data["debug_delivery_id_source"] = "project_id_missing"
                    continue

                data["delivery_id"] = project_id
                data["debug_delivery_id_source"] = "pts_link_project_id"
                data["debug_delivery_id_raw"] = project_id
                data["debug_delivery_id_normalized"] = project_id
                self._delivery_id_cache[project_id] = (project_id, "pts_link_project_id", project_id)
                continue

                cached = self._delivery_id_cache.get(project_id)
                if cached is not None:
                    delivery_id, source, raw_value = cached
                    data["debug_delivery_id_source"] = source
                    data["debug_delivery_id_raw"] = raw_value
                    data["debug_delivery_id_normalized"] = delivery_id
                    if delivery_id:
                        data["delivery_id"] = delivery_id
                    continue

                if self._pts_auth_available is False:
                    data["debug_delivery_id_source"] = "auth_required"
                    continue

                try:
                    delivery_id, source, raw_value = await self._resolve_delivery_id(
                        client=client,
                        project_id=project_id,
                        pts_link=pts_link,
                    )
                except Exception:
                    delivery_id, source, raw_value = None, "backfill_error", None
                if source == "auth_required":
                    self._pts_auth_available = False
                elif delivery_id:
                    self._pts_auth_available = True
                self._delivery_id_cache[project_id] = (delivery_id, source, raw_value)
                data["debug_delivery_id_source"] = source
                data["debug_delivery_id_raw"] = raw_value
                data["debug_delivery_id_normalized"] = delivery_id
                if delivery_id:
                    data["delivery_id"] = delivery_id
        return normalized_records

    async def _resolve_delivery_id(
        self,
        *,
        client: httpx.AsyncClient,
        project_id: str,
        pts_link: str,
    ) -> tuple[str | None, str, str | None]:
        delivery_id, raw_value = await self._fetch_from_project_page(client=client, pts_link=pts_link)
        if delivery_id:
            return delivery_id, "pts_project_page", raw_value
        auth_required = raw_value == "__auth_required__"

        delivery_id, raw_value = await self._fetch_with_local_chrome_profile(pts_link=pts_link)
        if delivery_id:
            return delivery_id, "pts_chrome_profile", raw_value
        if raw_value == "__auth_required__":
            auth_required = True

        delivery_id, raw_value = await self._fetch_with_playwright(pts_link=pts_link)
        if delivery_id:
            return delivery_id, "pts_playwright", raw_value
        if raw_value == "__auth_required__" or auth_required:
            return None, "auth_required", None
        return None, "not_found", raw_value

    async def _fetch_from_project_page(
        self,
        *,
        client: httpx.AsyncClient,
        pts_link: str,
    ) -> tuple[str | None, str | None]:
        if not self.settings.pts_cookie_header:
            return None, None

        headers = {
            "Cookie": self.settings.pts_cookie_header,
            "User-Agent": "Mozilla/5.0",
            "Accept": "text/html,application/xhtml+xml,application/json",
        }
        try:
            response = await client.get(strip_url_fragment(pts_link), headers=headers)
        except httpx.HTTPError:
            return None, None
        if _is_auth_redirect(response):
            return None, "__auth_required__"

        text = response.text
        return extract_delivery_id_from_text(text)

    async def _fetch_with_playwright(self, *, pts_link: str) -> tuple[str | None, str | None]:
        if not self.settings.pts_cookie_header:
            return None, None
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            return None, None

        responses: list[Any] = []
        try:
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=True)
                context = await browser.new_context(ignore_https_errors=not self.settings.pts_verify_ssl)
                await context.add_cookies(_build_playwright_cookies(self.settings.pts_cookie_header, self.settings.pts_base_url))
                page = await context.new_page()
                page.on("response", lambda response: responses.append(response))
                await page.goto(strip_url_fragment(pts_link), wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(3000)
                if "auth.chaitin.net/login" in page.url:
                    await browser.close()
                    return None, "__auth_required__"

                html = await page.content()
                delivery_id, raw_value = extract_delivery_id_from_text(html)
                if delivery_id:
                    await browser.close()
                    return delivery_id, raw_value

                for response in responses:
                    try:
                        body = await response.text()
                    except Exception:
                        continue
                    delivery_id, raw_value = extract_delivery_id_from_text(body)
                    if delivery_id:
                        await browser.close()
                        return delivery_id, raw_value
                await browser.close()
        except Exception:
            return None, None
        return None, None

    async def _fetch_with_local_chrome_profile(self, *, pts_link: str) -> tuple[str | None, str | None]:
        profile_root = _find_local_chrome_user_data_dir()
        if profile_root is None:
            return None, None

        temp_user_data_dir = Path(tempfile.mkdtemp(prefix="pts-chrome-profile-"))
        try:
            if not _copy_chrome_profile(profile_root, temp_user_data_dir):
                return None, None

            try:
                from playwright.async_api import async_playwright
            except ImportError:
                return None, None

            responses: list[Any] = []
            async with async_playwright() as playwright:
                context = await playwright.chromium.launch_persistent_context(
                    user_data_dir=str(temp_user_data_dir),
                    channel="chrome",
                    headless=True,
                    args=["--profile-directory=Default"],
                    ignore_https_errors=not self.settings.pts_verify_ssl,
                )
                page = await context.new_page()
                page.on("response", lambda response: responses.append(response))
                await page.goto(strip_url_fragment(pts_link), wait_until="domcontentloaded", timeout=45000)
                await page.wait_for_timeout(3000)
                if "auth.chaitin.net/login" in page.url:
                    await context.close()
                    return None, "__auth_required__"

                html = await page.content()
                delivery_id, raw_value = extract_delivery_id_from_text(html)
                if delivery_id:
                    await context.close()
                    return delivery_id, raw_value

                for response in responses:
                    try:
                        body = await response.text()
                    except Exception:
                        continue
                    delivery_id, raw_value = extract_delivery_id_from_text(body)
                    if delivery_id:
                        await context.close()
                        return delivery_id, raw_value
                await context.close()
        except Exception:
            return None, None
        finally:
            shutil.rmtree(temp_user_data_dir, ignore_errors=True)
        return None, None

    @staticmethod
    def _populate_debug_defaults(data: dict[str, Any]) -> None:
        data.setdefault("debug_pts_project_id", extract_pts_project_id(data.get("pts_link")))
        data.setdefault("debug_delivery_id_source", None)
        data.setdefault("debug_delivery_id_raw", None)
        data.setdefault("debug_delivery_id_normalized", data.get("delivery_id"))


def extract_pts_project_id(pts_link: Any) -> str | None:
    if not isinstance(pts_link, str) or not pts_link:
        return None
    match = PROJECT_ID_PATTERN.search(pts_link)
    if match is None:
        return None
    return match.group(1)


def extract_delivery_id_from_text(text: str) -> tuple[str | None, str | None]:
    for pattern in DELIVERY_ID_TEXT_PATTERNS:
        match = pattern.search(text)
        if match is not None:
            return match.group(1), match.group(0)
    try:
        payload = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None, None
    return extract_delivery_id_from_payload(payload)


def extract_delivery_id_from_payload(payload: Any) -> tuple[str | None, str | None]:
    if isinstance(payload, dict):
        for key in ("deliveryId", "delivery_id"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip(), json.dumps({key: value}, ensure_ascii=False)
        for value in payload.values():
            delivery_id, raw_value = extract_delivery_id_from_payload(value)
            if delivery_id:
                return delivery_id, raw_value
    elif isinstance(payload, list):
        for item in payload:
            delivery_id, raw_value = extract_delivery_id_from_payload(item)
            if delivery_id:
                return delivery_id, raw_value
    return None, None


def strip_url_fragment(url: str) -> str:
    parsed = urlparse(url)
    return urlunparse(parsed._replace(fragment=""))


def _is_auth_redirect(response: httpx.Response) -> bool:
    final_url = str(response.url)
    return response.status_code in {401, 403} or "auth.chaitin.net/login" in final_url


def _build_playwright_cookies(cookie_header: str, base_url: str) -> list[dict[str, Any]]:
    parsed = urlparse(base_url)
    cookies: list[dict[str, Any]] = []
    for part in cookie_header.split(";"):
        if "=" not in part:
            continue
        name, value = part.split("=", 1)
        cookies.append(
            {
                "name": name.strip(),
                "value": value.strip(),
                "domain": parsed.hostname or "pts.chaitin.net",
                "path": "/",
                "secure": parsed.scheme == "https",
                "httpOnly": False,
            }
        )
    return cookies


def _find_local_chrome_user_data_dir() -> Path | None:
    candidate = Path.home() / "Library/Application Support/Google/Chrome"
    local_state = candidate / "Local State"
    default_profile = candidate / "Default"
    if local_state.exists() and default_profile.exists():
        return candidate
    return None


def _copy_chrome_profile(source_root: Path, target_root: Path) -> bool:
    local_state = source_root / "Local State"
    default_profile = source_root / "Default"
    if not local_state.exists() or not default_profile.exists():
        return False

    shutil.copy2(local_state, target_root / "Local State")
    target_profile = target_root / "Default"
    target_profile.mkdir(parents=True, exist_ok=True)

    for relative_name in [
        "Cookies",
        "Preferences",
        "Web Data",
        "Login Data",
        "History",
    ]:
        source_path = default_profile / relative_name
        if source_path.exists() and source_path.is_file():
            shutil.copy2(source_path, target_profile / relative_name)

    for relative_dir in [
        "Local Storage",
        "Session Storage",
        "IndexedDB",
        "Network",
    ]:
        source_path = default_profile / relative_dir
        if source_path.exists() and source_path.is_dir():
            shutil.copytree(
                source_path,
                target_profile / relative_dir,
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns("Cache", "Code Cache", "GPUCache"),
            )
    return True
