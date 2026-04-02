from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.orm import Session, sessionmaker

from core.config import get_settings
from core.db import SessionLocal
from core.exceptions import OperationConflictError
from models.module_config import ModuleConfig
from repositories.module_config_repo import ModuleConfigRepository
from services.module_registry import default_module_configs
from services.sync_service import SyncService


logger = logging.getLogger(__name__)


def register_jobs(
    scheduler: BackgroundScheduler,
    *,
    session_factory: Callable[[], Session] | sessionmaker = SessionLocal,
) -> list[str]:
    settings = get_settings()
    if not settings.scheduler_enabled:
        return []

    try:
        with session_factory() as db:
            repo = ModuleConfigRepository(db)
            repo.upsert_defaults(default_module_configs())
            db.commit()
            module_configs = repo.list_all()
    except Exception:
        logger.exception(
            "failed to load module configs for scheduler; verify database connectivity and migrations"
        )
        return []

    registered_ids: list[str] = []
    for module_config in module_configs:
        trigger = _build_trigger(module_config)
        if not module_config.enabled or trigger is None:
            continue
        job_id = f"sync:{module_config.module_code}"
        scheduler.add_job(
            run_scheduled_sync_job,
            trigger=trigger,
            id=job_id,
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            kwargs={
                "module_code": module_config.module_code,
                "session_factory": session_factory,
            },
        )
        registered_ids.append(job_id)
    return registered_ids


def run_scheduled_sync_job(
    module_code: str,
    *,
    session_factory: Callable[[], Session] | sessionmaker = SessionLocal,
) -> None:
    try:
        with session_factory() as db:
            service = SyncService(db)
            asyncio.run(service.run_sync(module_code, trigger="scheduler"))
    except OperationConflictError:
        logger.info("scheduler skipped sync for %s because another run is active", module_code)
    except Exception:
        logger.exception("scheduler sync job failed for %s", module_code)


def _build_trigger(module_config: ModuleConfig):
    if module_config.sync_cron:
        return CronTrigger.from_crontab(module_config.sync_cron)
    extra_config = module_config.extra_config or {}
    if extra_config.get("schedule_type") == "interval":
        interval_minutes = int(extra_config.get("schedule_interval_minutes", 0))
        if interval_minutes > 0:
            return IntervalTrigger(minutes=interval_minutes)
    return None
