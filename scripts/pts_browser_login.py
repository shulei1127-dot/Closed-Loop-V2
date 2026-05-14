from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.config import get_settings
from services.pts_browser_profile_session import resolve_pts_profile_dir


async def main() -> int:
    parser = argparse.ArgumentParser(description="Login to PTS with the stable browser profile used by executions.")
    parser.add_argument("--profile-dir", default="", help="Override PTS browser profile directory.")
    parser.add_argument("--channel", default="", help="Playwright browser channel, for example chrome.")
    parser.add_argument("--url", default="", help="PTS URL to open.")
    args = parser.parse_args()

    settings = get_settings()
    profile_dir = Path(args.profile_dir).expanduser() if args.profile_dir else resolve_pts_profile_dir(settings)
    if not profile_dir.is_absolute():
        profile_dir = Path.cwd() / profile_dir
    profile_dir.mkdir(parents=True, exist_ok=True)
    target_url = args.url or f"{settings.pts_base_url.rstrip('/')}/project"
    channel = args.channel or settings.pts_browser_channel or None

    from playwright.async_api import async_playwright

    playwright = await async_playwright().start()
    context = None
    try:
        launch_kwargs = {
            "headless": False,
            "ignore_https_errors": not settings.pts_verify_ssl,
            "viewport": {"width": 1440, "height": 1000},
        }
        if channel:
            launch_kwargs["channel"] = channel
        try:
            context = await playwright.chromium.launch_persistent_context(str(profile_dir), **launch_kwargs)
        except Exception as exc:
            if channel or "Executable doesn't exist" not in str(exc):
                raise
            print("未找到 Playwright 自带 Chromium，自动改用本机 Google Chrome。")
            context = await playwright.chromium.launch_persistent_context(
                str(profile_dir),
                **{**launch_kwargs, "channel": "chrome"},
            )
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(target_url, wait_until="domcontentloaded")
        print(f"已打开 PTS 登录窗口，profile_dir={profile_dir}")
        print("登录成功并能看到 PTS 页面后，回到终端按 Enter 结束。")
        await asyncio.to_thread(sys.stdin.readline)
        return 0
    finally:
        if context is not None:
            await context.close()
        await playwright.stop()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
