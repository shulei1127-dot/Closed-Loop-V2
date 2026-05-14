from pathlib import Path

from core.config import Settings, get_settings
from services.executors.visit_real_runner import VisitRealRunner
from services.pts_session_service import PtsSessionService


def _mark_profile_configured(profile_dir: Path) -> None:
    default_dir = profile_dir / "Default"
    default_dir.mkdir(parents=True)
    (default_dir / "Preferences").write_text("{}", encoding="utf-8")


def test_visit_runner_accepts_configured_browser_profile_without_cookie(tmp_path) -> None:
    profile_dir = tmp_path / "pts-profile"
    _mark_profile_configured(profile_dir)
    runner = VisitRealRunner(
        Settings(
            pts_base_url="https://pts.example.com",
            pts_cookie_header="",
            pts_browser_profile_dir=str(profile_dir),
            pts_execution_transport="browser_profile",
        )
    )

    valid, diagnostics, error_message = runner.validate()

    assert valid is True
    assert error_message is None
    assert diagnostics["transport_mode"] == "pts_browser_profile"
    assert diagnostics["pts_auth_header"] == "BrowserProfile"
    assert diagnostics["missing_fields"] == []


def test_pts_session_status_reports_configured_browser_profile(monkeypatch, tmp_path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("PTS_BASE_URL=https://pts.example.com\nPTS_COOKIE_HEADER=\n", encoding="utf-8")
    profile_dir = tmp_path / "pts-profile"
    _mark_profile_configured(profile_dir)
    monkeypatch.setenv("PTS_BROWSER_PROFILE_DIR", str(profile_dir))
    monkeypatch.setenv("PTS_EXECUTION_TRANSPORT", "browser_profile")
    get_settings.cache_clear()

    status = PtsSessionService(env_path=env_path).get_status()

    assert status["configured"] is True
    assert status["cookie_configured"] is False
    assert status["browser_profile_configured"] is True
    assert status["source"] == "browser_profile"
    get_settings.cache_clear()
