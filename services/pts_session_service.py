from __future__ import annotations

from datetime import datetime
from pathlib import Path

from core.config import get_settings


DEFAULT_ENV_PATH = Path(__file__).resolve().parents[1] / ".env"


class PtsSessionService:
    def __init__(self, env_path: Path | None = None) -> None:
        self.env_path = env_path or DEFAULT_ENV_PATH

    def get_status(self) -> dict:
        values = self._read_env_values()
        updated_at = None
        if self.env_path.exists():
            updated_at = datetime.fromtimestamp(self.env_path.stat().st_mtime).isoformat()
        return {
            "configured": bool(values.get("PTS_COOKIE_HEADER")),
            "base_url": values.get("PTS_BASE_URL") or get_settings().pts_base_url,
            "source": "env_file",
            "updated_at": updated_at,
        }

    def update_cookie(self, cookie_header: str) -> dict:
        cookie = cookie_header.strip()
        if not cookie:
            raise ValueError("PTS Cookie 不能为空")
        if "\n" in cookie or "\r" in cookie:
            raise ValueError("PTS Cookie 格式非法")

        lines: list[str] = []
        if self.env_path.exists():
            lines = self.env_path.read_text(encoding="utf-8").splitlines()

        updated = False
        for index, line in enumerate(lines):
            if line.startswith("PTS_COOKIE_HEADER="):
                lines[index] = f"PTS_COOKIE_HEADER={cookie}"
                updated = True
                break

        if not updated:
            if lines and lines[-1] != "":
                lines.append("")
            lines.append(f"PTS_COOKIE_HEADER={cookie}")

        self.env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        get_settings.cache_clear()
        status = self.get_status()
        status["message"] = "PTS Cookie 已更新"
        return status

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
