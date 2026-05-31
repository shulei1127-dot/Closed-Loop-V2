from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime
import uuid

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.orm import Session, sessionmaker

from core.config import get_settings
from core.db import SessionLocal
from core.exceptions import OperationConflictError
from models.module_config import ModuleConfig
from repositories.module_config_repo import ModuleConfigRepository
from services.module_registry import default_module_configs, get_module_definition
from services.notifications.dingtalk_webhook_sender import DingtalkTextWebhookSender
from services.ops_service import OpsService, clear_ops_read_cache
from services.sync_service import SyncService
from services.task_execution_service import TaskExecutionService


logger = logging.getLogger(__name__)


def _is_workday() -> bool:
    """Return True if today (Asia/Shanghai) is a Chinese workday (non-holiday, non-weekend)."""
    try:
        import chinese_calendar
        from zoneinfo import ZoneInfo

        today = datetime.now(ZoneInfo(get_settings().scheduler_timezone)).date()
        return chinese_calendar.is_workday(today)
    except ImportError:
        logger.warning("chinesecalendar not installed; skipping holiday check")
        return True
    except Exception:
        logger.warning("chinesecalendar check failed; falling back to always-run")
        return True


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
        if not module_config.enabled:
            continue

        sync_trigger = _build_trigger(module_config)
        if sync_trigger is not None:
            job_id = f"sync:{module_config.module_code}"
            scheduler.add_job(
                run_scheduled_sync_job,
                trigger=sync_trigger,
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

        execute_trigger = _build_execute_trigger(module_config)
        if execute_trigger is not None:
            job_id = f"execute:{module_config.module_code}"
            scheduler.add_job(
                run_scheduled_execute_pending_job,
                trigger=execute_trigger,
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

    # Visit pipeline (sync + execute + writeback), daily 17:00
    if settings.visit_pipeline_enabled:
        visit_pipeline_cron = str(settings.visit_pipeline_cron or "").strip()
        if visit_pipeline_cron:
            job_id = "pipeline:visit"
            scheduler.add_job(
                run_scheduled_visit_pipeline_job,
                trigger=CronTrigger.from_crontab(visit_pipeline_cron),
                id=job_id,
                replace_existing=True,
                max_instances=1,
                coalesce=True,
                kwargs={"session_factory": session_factory},
            )
            registered_ids.append(job_id)

    # Proactive pipeline (sync + execute + writeback), daily 18:00
    if settings.proactive_pipeline_enabled:
        proactive_pipeline_cron = str(settings.proactive_pipeline_cron or "").strip()
        if proactive_pipeline_cron:
            job_id = "pipeline:proactive"
            scheduler.add_job(
                run_scheduled_proactive_pipeline_job,
                trigger=CronTrigger.from_crontab(proactive_pipeline_cron),
                id=job_id,
                replace_existing=True,
                max_instances=1,
                coalesce=True,
                kwargs={"session_factory": session_factory},
            )
            registered_ids.append(job_id)

    # Proactive tag mark pipeline (sync + execute tag_mark tasks), every 15 days
    if settings.proactive_tag_mark_pipeline_enabled:
        tag_mark_cron = str(settings.proactive_tag_mark_pipeline_cron or "").strip()
        if tag_mark_cron:
            job_id = "pipeline:proactive_tag_mark"
            scheduler.add_job(
                run_scheduled_proactive_tag_mark_pipeline_job,
                trigger=CronTrigger.from_crontab(tag_mark_cron),
                id=job_id,
                replace_existing=True,
                max_instances=1,
                coalesce=True,
                kwargs={"session_factory": session_factory},
            )
            registered_ids.append(job_id)

    # Review pipeline (sync + audit + writeback)
    if settings.review_pipeline_enabled:
        review_pipeline_cron = str(settings.review_pipeline_cron or "").strip()
        if review_pipeline_cron:
            job_id = "pipeline:review"
            scheduler.add_job(
                run_scheduled_review_pipeline_job,
                trigger=CronTrigger.from_crontab(review_pipeline_cron),
                id=job_id,
                replace_existing=True,
                max_instances=1,
                coalesce=True,
                kwargs={"session_factory": session_factory},
            )
            registered_ids.append(job_id)

    # 19:00 回访工单自动闭环 (visit + proactive), daily 19:00
    if settings.combined_pipeline_enabled:
        combined_pipeline_cron = str(settings.combined_pipeline_cron or "").strip()
        if combined_pipeline_cron:
            job_id = "pipeline:visit_auto_closure"
            scheduler.add_job(
                run_scheduled_combined_pipeline_job,
                trigger=CronTrigger.from_crontab(combined_pipeline_cron),
                id=job_id,
                replace_existing=True,
                max_instances=1,
                coalesce=True,
                kwargs={"session_factory": session_factory},
            )
            registered_ids.append(job_id)

    return registered_ids


def run_scheduled_sync_job(
    module_code: str,
    *,
    session_factory: Callable[[], Session] | sessionmaker = SessionLocal,
) -> None:
    module_name = _resolve_module_name(module_code)
    try:
        with session_factory() as db:
            config = ModuleConfigRepository(db).get_by_code(module_code)
            if config is not None and config.module_name:
                module_name = config.module_name
            service = SyncService(db)
            result = asyncio.run(service.run_sync(module_code, trigger="scheduler"))
        _send_scheduler_summary_notification(
            event_type="sync",
            module_code=module_code,
            module_name=module_name,
            message_text=_build_sync_summary_message(module_name, result),
        )
    except OperationConflictError:
        logger.info("scheduler skipped sync for %s because another run is active", module_code)
        _send_scheduler_summary_notification(
            event_type="sync",
            module_code=module_code,
            module_name=module_name,
            message_text=_build_skipped_summary_message(module_name, event_label="17:55 定时同步", reason="另一个同步任务仍在运行"),
        )
    except Exception:
        logger.exception("scheduler sync job failed for %s", module_code)
        _send_scheduler_summary_notification(
            event_type="sync",
            module_code=module_code,
            module_name=module_name,
            message_text=_build_failed_summary_message(module_name, event_label="17:55 定时同步"),
        )


def run_scheduled_execute_pending_job(
    module_code: str,
    *,
    session_factory: Callable[[], Session] | sessionmaker = SessionLocal,
) -> None:
    module_name = _resolve_module_name(module_code)
    try:
        with session_factory() as db:
            config = ModuleConfigRepository(db).get_by_code(module_code)
            if config is not None and config.module_name:
                module_name = config.module_name
            extra_config = config.extra_config if config is not None else {}
            dry_run = bool((extra_config or {}).get("execute_dry_run", False))
            ops_service = OpsService(db)
            execution_service = TaskExecutionService(db)
            pending_items = ops_service.list_pending_tasks(module_code=module_code, limit=5000)
            pending_items = [item for item in pending_items if item.can_execute]
            if not pending_items:
                logger.info("scheduler execute job found no actionable pending tasks for %s", module_code)
                _send_scheduler_summary_notification(
                    event_type="execute",
                    module_code=module_code,
                    module_name=module_name,
                    message_text=_build_execute_summary_message(
                        module_name=module_name,
                        total_count=0,
                        success_count=0,
                        failed_count=0,
                        manual_required_count=0,
                        conflict_count=0,
                        dry_run=dry_run,
                    ),
                )
                return

            success_count = 0
            failed_count = 0
            manual_required_count = 0
            conflict_count = 0
            for item in pending_items:
                try:
                    detail = asyncio.run(
                        execution_service.execute_task(
                            uuid.UUID(item.task_plan_id),
                            dry_run=dry_run,
                            trigger="scheduler",
                        )
                    )
                    if detail.manual_required:
                        manual_required_count += 1
                    if detail.run_status in {"success", "simulated_success", "pending_confirmation"}:
                        success_count += 1
                    else:
                        failed_count += 1
                except OperationConflictError:
                    conflict_count += 1
                    logger.info(
                        "scheduler skipped execute for %s task %s because another run is active",
                        module_code,
                        item.task_plan_id,
                    )
                except Exception:
                    failed_count += 1
                    logger.exception(
                        "scheduler execute job failed for %s task %s",
                        module_code,
                        item.task_plan_id,
                    )

            clear_ops_read_cache(module_code=module_code)
            logger.info(
                "scheduler execute job finished for %s: total=%s success=%s failed=%s manual_required=%s dry_run=%s",
                module_code,
                len(pending_items),
                success_count,
                failed_count,
                manual_required_count,
                dry_run,
            )
        _send_scheduler_summary_notification(
            event_type="execute",
            module_code=module_code,
            module_name=module_name,
            message_text=_build_execute_summary_message(
                module_name=module_name,
                total_count=len(pending_items),
                success_count=success_count,
                failed_count=failed_count,
                manual_required_count=manual_required_count,
                conflict_count=conflict_count,
                dry_run=dry_run,
            ),
        )
    except Exception:
        logger.exception("scheduler execute-pending job failed for %s", module_code)
        _send_scheduler_summary_notification(
            event_type="execute",
            module_code=module_code,
            module_name=module_name,
            message_text=_build_failed_summary_message(module_name, event_label="18:00 定时执行"),
        )


def _run_execute_pending_cycle(
    module_code: str,
    *,
    session_factory: Callable[[], Session] | sessionmaker = SessionLocal,
) -> dict[str, int]:
    """Execute pending tasks for a module, returning execution statistics."""
    module_name = _resolve_module_name(module_code)
    with session_factory() as db:
        config = ModuleConfigRepository(db).get_by_code(module_code)
        if config is not None and config.module_name:
            module_name = config.module_name
        extra_config = config.extra_config if config is not None else {}
        dry_run = bool((extra_config or {}).get("execute_dry_run", False))
        ops_service = OpsService(db)
        execution_service = TaskExecutionService(db)
        pending_items = ops_service.list_pending_tasks(module_code=module_code, limit=5000)
        pending_items = [item for item in pending_items if item.can_execute]

        if not pending_items:
            logger.info("pipeline execute cycle found no actionable pending tasks for %s", module_code)
            return {
                "total_count": 0,
                "success_count": 0,
                "failed_count": 0,
                "manual_required_count": 0,
                "conflict_count": 0,
                "dry_run": dry_run,
            }

        success_count = 0
        failed_count = 0
        manual_required_count = 0
        conflict_count = 0
        for item in pending_items:
            try:
                detail = asyncio.run(
                    execution_service.execute_task(
                        uuid.UUID(item.task_plan_id),
                        dry_run=dry_run,
                        trigger="scheduler",
                    )
                )
                if detail.manual_required:
                    manual_required_count += 1
                if detail.run_status in {"success", "simulated_success", "pending_confirmation"}:
                    success_count += 1
                else:
                    failed_count += 1
                # 在任务之间添加延迟，避免PTS API限流
                if item is not pending_items[-1]:
                    asyncio.run(asyncio.sleep(5))
            except OperationConflictError:
                conflict_count += 1
                logger.info(
                    "pipeline skipped execute for %s task %s because another run is active",
                    module_code,
                    item.task_plan_id,
                )
            except Exception:
                failed_count += 1
                logger.exception(
                    "pipeline execute cycle failed for %s task %s",
                    module_code,
                    item.task_plan_id,
                )

        clear_ops_read_cache(module_code=module_code)
        logger.info(
            "pipeline execute cycle finished for %s: total=%s success=%s failed=%s manual_required=%s dry_run=%s",
            module_code,
            len(pending_items),
            success_count,
            failed_count,
            manual_required_count,
            dry_run,
        )

    return {
        "total_count": len(pending_items),
        "success_count": success_count,
        "failed_count": failed_count,
        "manual_required_count": manual_required_count,
        "conflict_count": conflict_count,
        "dry_run": dry_run,
    }


def run_scheduled_visit_pipeline_job(
    *,
    session_factory: Callable[[], Session] | sessionmaker = SessionLocal,
) -> None:
    """Visit pipeline: sync → execute → writeback. Daily 17:00, skip holidays."""
    if not _is_workday():
        logger.info("visit pipeline skipped: today is a Chinese holiday or weekend")
        return

    module_code = "visit"
    module_name = _resolve_module_name(module_code)
    try:
        # Step 1: sync
        with session_factory() as db:
            service = SyncService(db)
            sync_result = asyncio.run(service.run_sync(module_code, trigger="scheduler"))
        _send_scheduler_summary_notification(
            event_type="pipeline",
            module_code=module_code,
            module_name=module_name,
            message_text=_build_pipeline_sync_summary_message(module_name, sync_result, pipeline_label="17:00 交付转售后"),
        )

        # Step 2: execute pending tasks
        stats = _run_execute_pending_cycle(module_code, session_factory=session_factory)
        _send_scheduler_summary_notification(
            event_type="pipeline",
            module_code=module_code,
            module_name=module_name,
            message_text=_build_pipeline_execute_summary_message(
                module_name=module_name,
                pipeline_label="17:00 交付转售后",
                **stats,
            ),
        )
    except OperationConflictError:
        logger.info("visit pipeline skipped because another sync is active")
    except Exception:
        logger.exception("visit pipeline job failed")
        _send_scheduler_summary_notification(
            event_type="pipeline",
            module_code=module_code,
            module_name=module_name,
            message_text=_build_failed_summary_message(module_name, event_label="17:00 交付转售后流水线"),
        )


def run_scheduled_proactive_pipeline_job(
    *,
    session_factory: Callable[[], Session] | sessionmaker = SessionLocal,
) -> None:
    """Proactive pipeline: sync → execute → writeback. Daily 18:00, skip holidays."""
    if not _is_workday():
        logger.info("proactive pipeline skipped: today is a Chinese holiday or weekend")
        return

    module_code = "proactive"
    module_name = _resolve_module_name(module_code)
    try:
        # Step 1: sync
        with session_factory() as db:
            service = SyncService(db)
            sync_result = asyncio.run(service.run_sync(module_code, trigger="scheduler"))
        _send_scheduler_summary_notification(
            event_type="pipeline",
            module_code=module_code,
            module_name=module_name,
            message_text=_build_pipeline_sync_summary_message(module_name, sync_result, pipeline_label="18:00 超半年主动回访"),
        )

        # Step 2: execute pending tasks
        stats = _run_execute_pending_cycle(module_code, session_factory=session_factory)
        _send_scheduler_summary_notification(
            event_type="pipeline",
            module_code=module_code,
            module_name=module_name,
            message_text=_build_pipeline_execute_summary_message(
                module_name=module_name,
                pipeline_label="18:00 超半年主动回访",
                **stats,
            ),
        )
    except OperationConflictError:
        logger.info("proactive pipeline skipped because another sync is active")
    except Exception:
        logger.exception("proactive pipeline job failed")
        _send_scheduler_summary_notification(
            event_type="pipeline",
            module_code=module_code,
            module_name=module_name,
            message_text=_build_failed_summary_message(module_name, event_label="18:00 超半年主动回访流水线"),
        )


def run_scheduled_proactive_tag_mark_pipeline_job(
    *,
    session_factory: Callable[[], Session] | sessionmaker = SessionLocal,
) -> None:
    """Proactive tag mark pipeline: sync → execute tag_mark tasks only. Runs every 15 days."""
    if not _is_workday():
        logger.info("proactive tag mark pipeline skipped: today is a Chinese holiday or weekend")
        return

    module_code = "proactive"
    module_name = _resolve_module_name(module_code)
    try:
        # Step 1: sync
        with session_factory() as db:
            service = SyncService(db)
            sync_result = asyncio.run(service.run_sync(module_code, trigger="scheduler"))
        _send_scheduler_summary_notification(
            event_type="pipeline",
            module_code=module_code,
            module_name=module_name,
            message_text=_build_pipeline_sync_summary_message(module_name, sync_result, pipeline_label="标签标记定时"),
        )

        # Step 2: execute only proactive_tag_mark tasks
        with session_factory() as db:
            config = ModuleConfigRepository(db).get_by_code(module_code)
            extra_config = config.extra_config if config is not None else {}
            dry_run = bool((extra_config or {}).get("execute_dry_run", False))
            ops_service = OpsService(db)
            execution_service = TaskExecutionService(db)
            pending_items = ops_service.list_pending_tasks(module_code=module_code, limit=5000)
            # Filter to only proactive_tag_mark tasks
            pending_items = [
                item for item in pending_items
                if item.can_execute and item.task_type == "proactive_tag_mark"
            ]

            if not pending_items:
                logger.info("proactive tag mark pipeline found no actionable tag_mark tasks")
                stats = {"total_count": 0, "success_count": 0, "failed_count": 0,
                         "manual_required_count": 0, "conflict_count": 0, "dry_run": dry_run}
            else:
                success_count = 0
                failed_count = 0
                manual_required_count = 0
                conflict_count = 0
                for item in pending_items:
                    try:
                        detail = asyncio.run(
                            execution_service.execute_task(
                                uuid.UUID(item.task_plan_id),
                                dry_run=dry_run,
                                trigger="scheduler",
                            )
                        )
                        if detail.manual_required:
                            manual_required_count += 1
                        if detail.run_status in {"success", "simulated_success", "pending_confirmation"}:
                            success_count += 1
                        else:
                            failed_count += 1
                    except OperationConflictError:
                        conflict_count += 1
                    except Exception:
                        failed_count += 1
                        logger.exception("proactive tag mark pipeline failed for task %s", item.task_plan_id)

                clear_ops_read_cache(module_code=module_code)
                stats = {
                    "total_count": len(pending_items),
                    "success_count": success_count,
                    "failed_count": failed_count,
                    "manual_required_count": manual_required_count,
                    "conflict_count": conflict_count,
                    "dry_run": dry_run,
                }

        _send_scheduler_summary_notification(
            event_type="pipeline",
            module_code=module_code,
            module_name=module_name,
            message_text=_build_pipeline_execute_summary_message(
                module_name=module_name,
                pipeline_label="标签标记定时",
                **stats,
            ),
        )
    except OperationConflictError:
        logger.info("proactive tag mark pipeline skipped because another sync is active")
    except Exception:
        logger.exception("proactive tag mark pipeline job failed")
        _send_scheduler_summary_notification(
            event_type="pipeline",
            module_code=module_code,
            module_name=module_name,
            message_text=_build_failed_summary_message(module_name, event_label="标签标记定时流水线"),
        )


def run_scheduled_review_pipeline_job(
    *,
    session_factory: Callable[[], Session] | sessionmaker = SessionLocal,
) -> None:
    """Review pipeline: sync → execute audit tasks → writeback. Daily 16:00, skip holidays."""
    if not _is_workday():
        logger.info("review pipeline skipped: today is a Chinese holiday or weekend")
        return

    module_code = "review"
    module_name = _resolve_module_name(module_code)
    try:
        # Step 1: sync
        with session_factory() as db:
            service = SyncService(db)
            sync_result = asyncio.run(service.run_sync(module_code, trigger="scheduler"))
        _send_scheduler_summary_notification(
            event_type="pipeline",
            module_code=module_code,
            module_name=module_name,
            message_text=_build_pipeline_sync_summary_message(module_name, sync_result, pipeline_label="16:00 交付转售后审核"),
        )

        # Step 2: execute pending tasks
        stats = _run_execute_pending_cycle(module_code, session_factory=session_factory)
        _send_scheduler_summary_notification(
            event_type="pipeline",
            module_code=module_code,
            module_name=module_name,
            message_text=_build_pipeline_execute_summary_message(
                module_name=module_name,
                pipeline_label="16:00 交付转售后审核",
                **stats,
            ),
        )
    except OperationConflictError:
        logger.info("review pipeline skipped because another sync is active")
    except Exception:
        logger.exception("review pipeline job failed")
        _send_scheduler_summary_notification(
            event_type="pipeline",
            module_code=module_code,
            module_name=module_name,
            message_text=_build_failed_summary_message(module_name, event_label="16:00 交付转售后审核流水线"),
        )


def _build_trigger(module_config: ModuleConfig):
    if module_config.sync_cron:
        return CronTrigger.from_crontab(module_config.sync_cron)
    extra_config = module_config.extra_config or {}
    if extra_config.get("schedule_type") == "interval":
        interval_minutes = int(extra_config.get("schedule_interval_minutes", 0))
        if interval_minutes > 0:
            return IntervalTrigger(minutes=interval_minutes)
    return None


def _build_execute_trigger(module_config: ModuleConfig):
    extra_config = module_config.extra_config or {}
    cron = str(extra_config.get("execute_cron") or "").strip()
    if cron:
        return CronTrigger.from_crontab(cron)
    if extra_config.get("execute_schedule_type") == "interval":
        interval_minutes = int(extra_config.get("execute_schedule_interval_minutes", 0))
        if interval_minutes > 0:
            return IntervalTrigger(minutes=interval_minutes)
    return None


def _send_scheduler_summary_notification(
    *,
    event_type: str,
    module_code: str,
    module_name: str,
    message_text: str,
) -> None:
    sender = DingtalkTextWebhookSender()
    if not sender.is_configured():
        return
    try:
        result = asyncio.run(sender.send_text(message_text))
        if not result.get("success", False):
            logger.warning(
                "scheduler %s summary notification failed for %s (%s): %s",
                event_type,
                module_code,
                module_name,
                result.get("error_message") or result,
            )
    except Exception:
        logger.exception(
            "scheduler %s summary notification crashed for %s (%s)",
            event_type,
            module_code,
            module_name,
        )


def _build_sync_summary_message(module_name: str, result) -> str:
    timestamp = _now_text()
    retried = "是" if result.run_context and result.run_context.retried else "否"
    return "\n".join(
        [
            f"【17:55 定时同步摘要】{module_name}",
            f"时间：{timestamp}",
            f"结果：{result.snapshot.sync_status}",
            f"源数据行数：{result.snapshot.row_count}",
            f"识别记录：{result.recognition.record_count}（完整 {result.recognition.full_count} / 部分 {result.recognition.partial_count} / 失败 {result.recognition.failed_count}）",
            f"任务规划：待执行 {result.task_plans.planned_count} / 跳过 {result.task_plans.skipped_count}",
            f"自动重试：{retried}",
        ]
    )


def _build_execute_summary_message(
    *,
    module_name: str,
    total_count: int,
    success_count: int,
    failed_count: int,
    manual_required_count: int,
    conflict_count: int,
    dry_run: bool,
) -> str:
    timestamp = _now_text()
    return "\n".join(
        [
            f"【18:00 定时执行摘要】{module_name}",
            f"时间：{timestamp}",
            f"候选任务：{total_count}",
            f"执行成功：{success_count}",
            f"执行失败：{failed_count}",
            f"待人工确认：{manual_required_count}",
            f"运行冲突跳过：{conflict_count}",
            f"dry_run：{'是' if dry_run else '否'}",
        ]
    )


def _build_skipped_summary_message(module_name: str, *, event_label: str, reason: str) -> str:
    return "\n".join(
        [
            f"【{event_label}摘要】{module_name}",
            f"时间：{_now_text()}",
            "结果：已跳过",
            f"原因：{reason}",
        ]
    )


def _build_failed_summary_message(module_name: str, *, event_label: str) -> str:
    return "\n".join(
        [
            f"【{event_label}摘要】{module_name}",
            f"时间：{_now_text()}",
            "结果：失败",
            "详情：请查看服务日志排查",
        ]
    )


def _build_pipeline_sync_summary_message(module_name: str, result, *, pipeline_label: str) -> str:
    timestamp = _now_text()
    retried = "是" if result.run_context and result.run_context.retried else "否"
    return "\n".join(
        [
            f"【{pipeline_label} 同步摘要】{module_name}",
            f"时间：{timestamp}",
            f"结果：{result.snapshot.sync_status}",
            f"源数据行数：{result.snapshot.row_count}",
            f"识别记录：{result.recognition.record_count}（完整 {result.recognition.full_count} / 部分 {result.recognition.partial_count} / 失败 {result.recognition.failed_count}）",
            f"任务规划：待执行 {result.task_plans.planned_count} / 跳过 {result.task_plans.skipped_count}",
            f"自动重试：{retried}",
        ]
    )


def _build_pipeline_execute_summary_message(
    *,
    module_name: str,
    pipeline_label: str,
    total_count: int,
    success_count: int,
    failed_count: int,
    manual_required_count: int,
    conflict_count: int,
    dry_run: bool,
) -> str:
    timestamp = _now_text()
    return "\n".join(
        [
            f"【{pipeline_label} 执行摘要】{module_name}",
            f"时间：{timestamp}",
            f"候选任务：{total_count}",
            f"执行成功：{success_count}",
            f"执行失败：{failed_count}",
            f"待人工确认：{manual_required_count}",
            f"运行冲突跳过：{conflict_count}",
            f"dry_run：{'是' if dry_run else '否'}",
        ]
    )


def _now_text() -> str:
    settings = get_settings()
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo(settings.scheduler_timezone)).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _resolve_module_name(module_code: str) -> str:
    try:
        return str(get_module_definition(module_code).get("module_name") or module_code)
    except Exception:
        return module_code


def run_scheduled_combined_pipeline_job(
    *,
    session_factory: Callable[[], Session] | sessionmaker = SessionLocal,
) -> None:
    """19:00 回访工单自动闭环: sync & execute visit + proactive. Daily 19:00, skip holidays."""
    if not _is_workday():
        logger.info("combined pipeline skipped: today is a Chinese holiday or weekend")
        _send_scheduler_summary_notification(
            event_type="pipeline",
            module_code="combined",
            module_name="回访工单自动闭环",
            message_text=f"【19:00 回访工单自动闭环】时间：{_now_text()}\n结果：已跳过\n原因：今日为节假日或周末",
        )
        return

    module_codes = ["visit", "proactive"]
    results = {}

    for module_code in module_codes:
        module_name = _resolve_module_name(module_code)
        try:
            # Step 1: sync
            with session_factory() as db:
                service = SyncService(db)
                sync_result = asyncio.run(service.run_sync(module_code, trigger="scheduler"))
            results[f"{module_code}_sync"] = sync_result

            # Step 2: execute pending tasks
            stats = _run_execute_pending_cycle(module_code, session_factory=session_factory)
            results[f"{module_code}_execute"] = stats
        except OperationConflictError:
            logger.info("combined pipeline skipped %s because another sync is active", module_code)
        except Exception:
            logger.exception("combined pipeline job failed for %s", module_code)

    # 发送组合流水线完成通知
    visit_sync = results.get("visit_sync")
    proactive_sync = results.get("proactive_sync")
    visit_execute = results.get("visit_execute", {})
    proactive_execute = results.get("proactive_execute", {})

    completion_message = "\n".join(
        [
            "【19:00 回访工单自动闭环 - 完成】",
            f"时间：{_now_text()}",
            "",
            "📊 交付转售后回访闭环",
            f"同步状态：{visit_sync.snapshot.sync_status if visit_sync else 'N/A'}",
            f"源数据行数：{visit_sync.snapshot.row_count if visit_sync else 0}",
            f"识别记录：{visit_sync.recognition.record_count if visit_sync else 0}",
            f"任务规划：{visit_sync.task_plans.planned_count if visit_sync else 0} / 跳过 {visit_sync.task_plans.skipped_count if visit_sync else 0}",
            f"执行结果：成功 {visit_execute.get('success_count', 0)} / 失败 {visit_execute.get('failed_count', 0)} / 待确认 {visit_execute.get('manual_required_count', 0)}",
            "",
            "📊 超半年主动回访闭环",
            f"同步状态：{proactive_sync.snapshot.sync_status if proactive_sync else 'N/A'}",
            f"源数据行数：{proactive_sync.snapshot.row_count if proactive_sync else 0}",
            f"识别记录：{proactive_sync.recognition.record_count if proactive_sync else 0}",
            f"任务规划：{proactive_sync.task_plans.planned_count if proactive_sync else 0} / 跳过 {proactive_sync.task_plans.skipped_count if proactive_sync else 0}",
            f"执行结果：成功 {proactive_execute.get('success_count', 0)} / 失败 {proactive_execute.get('failed_count', 0)} / 待确认 {proactive_execute.get('manual_required_count', 0)}",
        ]
    )

    _send_scheduler_summary_notification(
        event_type="pipeline",
        module_code="combined",
        module_name="回访工单自动闭环",
        message_text=completion_message,
    )


def _build_pipeline_combined_summary_message(
    *,
    module_name: str,
    sync_result,
    pipeline_label: str,
    total_count: int,
    success_count: int,
    failed_count: int,
    manual_required_count: int,
    conflict_count: int,
    dry_run: bool,
) -> str:
    timestamp = _now_text()
    retried = "是" if sync_result.run_context and sync_result.run_context.retried else "否"
    return "\n".join(
        [
            f"【{pipeline_label} 摘要】{module_name}",
            f"时间：{timestamp}",
            "--- 同步 ---",
            f"结果：{sync_result.snapshot.sync_status}",
            f"源数据行数：{sync_result.snapshot.row_count}",
            f"识别记录：{sync_result.recognition.record_count}（完整 {sync_result.recognition.full_count} / 部分 {sync_result.recognition.partial_count} / 失败 {sync_result.recognition.failed_count}）",
            f"任务规划：待执行 {sync_result.task_plans.planned_count} / 跳过 {sync_result.task_plans.skipped_count}",
            f"自动重试：{retried}",
            "--- 执行 ---",
            f"候选任务：{total_count}",
            f"执行成功：{success_count}",
            f"执行失败：{failed_count}",
            f"待人工确认：{manual_required_count}",
            f"运行冲突跳过：{conflict_count}",
            f"dry_run：{'是' if dry_run else '否'}",
        ]
    )
