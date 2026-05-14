from __future__ import annotations

from datetime import datetime
from pathlib import Path

from core.config import get_settings
from services.pts_browser_profile_session import (
    pts_browser_profile_configured,
    pts_browser_profile_enabled,
    resolve_pts_profile_dir,
)


DEFAULT_ENV_PATH = Path(__file__).resolve().parents[1] / ".env"


class PtsSessionService:
    def __init__(self, env_path: Path | None = None) -> None:
        self.env_path = env_path or DEFAULT_ENV_PATH

    def get_status(self) -> dict:
        values = self._read_env_values()
        settings = get_settings()
        browser_profile_enabled = pts_browser_profile_enabled(settings)
        browser_profile_dir = resolve_pts_profile_dir(settings)
        browser_profile_configured = pts_browser_profile_configured(settings)
        updated_at = None
        if self.env_path.exists():
            updated_at = datetime.fromtimestamp(self.env_path.stat().st_mtime).isoformat()
        cookie_configured = bool(values.get("PTS_COOKIE_HEADER"))
        return {
            "configured": cookie_configured or browser_profile_configured,
            "base_url": values.get("PTS_BASE_URL") or settings.pts_base_url,
            "source": values.get("PTS_AUTH_SOURCE") or ("browser_profile" if browser_profile_configured else "env_file"),
            "updated_at": updated_at,
            "cookie_configured": cookie_configured,
            "browser_profile_enabled": browser_profile_enabled,
            "browser_profile_configured": browser_profile_configured,
            "browser_profile_dir": str(browser_profile_dir),
        }

    def update_cookie(self, cookie_header: str) -> dict:
        return self.update_auth_bundle(cookie_header=cookie_header, source="manual_input")

    def update_auth_bundle(
        self,
        *,
        cookie_header: str,
        source: str = "env_file",
        pts_username: str | None = None,
    ) -> dict:
        cookie = cookie_header.strip()
        if not cookie:
            raise ValueError("PTS Cookie 不能为空")
        if "\n" in cookie or "\r" in cookie:
            raise ValueError("PTS Cookie 格式非法")

        lines: list[str] = []
        if self.env_path.exists():
            lines = self.env_path.read_text(encoding="utf-8").splitlines()

        lines = self._upsert_env_line(lines, "PTS_COOKIE_HEADER", cookie)
        lines = self._upsert_env_line(lines, "PTS_AUTH_SOURCE", str(source or "env_file").strip() or "env_file")
        if pts_username and pts_username.strip():
            lines = self._upsert_env_line(lines, "PTS_USERNAME_HINT", pts_username.strip())

        self.env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        get_settings.cache_clear()
        status = self.get_status()
        status["message"] = "PTS 会话已更新"
        return status

    def _upsert_env_line(self, lines: list[str], key: str, value: str) -> list[str]:
        updated = False
        new_lines = list(lines)
        for index, line in enumerate(new_lines):
            if line.startswith(f"{key}="):
                new_lines[index] = f"{key}={value}"
                updated = True
                break

        if not updated:
            if new_lines and new_lines[-1] != "":
                new_lines.append("")
            new_lines.append(f"{key}={value}")

        return new_lines

    def _read_env_values(self) -> dict[str, str]:
        if not self.env_path.exists():
            return {}
        values: dict[str, str] = {}
        for line in self.env_path.read_text(encoding="utf-8").splitlines():
            if not line or line.lstrip().startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key] = value
        return values
