from apscheduler.schedulers.background import BackgroundScheduler

from core.config import get_settings


def build_scheduler() -> BackgroundScheduler:
    settings = get_settings()
    return BackgroundScheduler(timezone=settings.scheduler_timezone)
